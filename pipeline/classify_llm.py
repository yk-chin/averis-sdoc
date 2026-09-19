"""
LLM 兜底分类器 —— 只在规则置信度不够时被调用
==============================================
- provider 由 LLM_PROVIDER 控制：aistudio（API key，开发用免费额度）| vertex（GCP 项目 + ADC，交付用）
- 结构化输出：response_schema = pydantic 模型，SDK 侧 + 本地各校验一次
- 10 秒超时（API 最小允许值）、1 次重试；任何失败都返回 None，由调用方退回规则结果
- 绝不向主流程抛异常
- 模型降级链：GEMINI_MODEL 可写逗号分隔的优先级列表；404 剔除、429/503 冷却 60s 自动切换
- 结果按 (prompt 版本, subject, body) 哈希缓存到 .cache/，重复跑 eval 不重复计费；
  改 prompt / few-shot / schema 会自动让旧缓存失效

环境变量（可放 .env，见 .env.example）：
  LLM_PROVIDER      aistudio | vertex          未设置 → LLM 关闭，全部走规则
  GEMINI_API_KEY    aistudio 必填
  GEMINI_MODEL      默认 gemini-3.6-flash,gemini-3.5-flash,gemini-3.5-flash-lite
  GCP_PROJECT       vertex 必填
  GCP_LOCATION      vertex 可选，默认 us-central1
  LLM_MIN_INTERVAL  两次调用最小间隔秒数，免费额度限流用，默认 0
"""
from __future__ import annotations
import hashlib, json, logging, os, pathlib, sys, threading, time
from typing import Literal, Optional

from pydantic import BaseModel, Field, ValidationError

logging.getLogger("google_genai").setLevel(logging.ERROR)   # 静音 SDK 的 AFC 提示等无关 warning

ROOT = pathlib.Path(__file__).resolve().parents[1]
CACHE_PATH = ROOT / ".cache" / "llm_classify.json"
TIMEOUT_MS = 10_000                   # Gemini API 硬性下限 10s（8s 会被 400 拒绝："Minimum allowed deadline is 10s"）
MAX_ATTEMPTS = 2                      # 1 次 + 1 次重试
BODY_MAX_CHARS = 1_500                # SI 全文很长，签名/引用无信息量

Category = Literal["BL_COMPARISON", "SI_REQUEST", "INVOICE_QUERY", "GENERAL", "SPAM"]


class Classification(BaseModel):
    category: Category
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(max_length=200, description="一句话，引用正文中的依据")


# ----------------------------------------------------------------------------
# .env 加载（不引入 python-dotenv；已存在的环境变量优先）
# ----------------------------------------------------------------------------
def _load_dotenv() -> None:
    p = ROOT / ".env"
    if not p.exists():
        return
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and v and k not in os.environ:
                os.environ[k] = v
    except Exception:
        pass


# ----------------------------------------------------------------------------
# Prompt
# ----------------------------------------------------------------------------
SYSTEM_PROMPT = """You classify emails received by a shipping-documentation team into exactly ONE category.

CATEGORIES
- BL_COMPARISON : the sender wants the draft Bill of Lading checked / verified / confirmed against the Shipping Instruction (SI). Includes requests to "send the draft BL for checking". Attachments named *_SI.* and *_BL.* are a strong signal, but the intent counts even when attachments are missing.
- SI_REQUEST    : the email transmits a Shipping Instruction (POL / POD / Shipper / Consignee / Notify Party / Description of Goods ...) or asks for an SI to be prepared or filed. Typical opening: "Please find Shipping instruction for <OC>". NOTE: the SI text ends with a "Documents Required: 1) 3 Original invoice ..." list — that is part of the SI, NOT an invoice query.
- INVOICE_QUERY : the email is about money: an invoice, charges (THC, local charges, D&D / detention), a missing GR for an invoice, cancelling / reversing an invoice, confirming an amount before payment.
- GENERAL       : internal operational notices with no action on documents or money: RPA / bot "process completed" notifications, daily berthing reports, vessel update summaries, outstanding-BL lists, reminders to submit SI & AED, holiday greetings. The words "Billing", "outstanding", "pending" inside such notices do NOT make them INVOICE_QUERY.
- SPAM          : unsolicited / phishing / scam: prize draws, mailbox-full verification, bank-officer business proposals, unpaid customs fee links, limited-time software offers.

CRITICAL RULE — SUBJECT LINES ARE UNRELIABLE
This dataset deliberately contains misleading subject lines: a subject like "Time Off Request" may sit on an RPA notification, "UPDATE SUMMARY" on a New Year greeting, "Increase your shipping revenue" on a mailbox-phishing mail. ALWAYS decide from the BODY. Use the subject only as a weak hint when the body is genuinely ambiguous.

OUTPUT
Return JSON matching the schema: category, confidence (0-1), reason (one short sentence quoting the body evidence).
"""

# few-shot：五类各 2 条，均取自真实 inbox（正文已截短）
FEW_SHOT: list[tuple[str, str, str, list[str], str]] = [
    # (email_id, subject, body, attachments, gold)
    ("email_051",
     "TO CONFIRM DOCS _ 5RSG-51522 _ SAVANNAH_US _ KPP-ANTALIS (SINGAPORE) PTE. LTD. _ HLCUSIN613750606",
     "Hi Elisa,\n\nAttached are the SI and draft BL for OC 5RSG-51522 (UNCOATED WOODFREE PAPER IN REA). Please check the details and confirm.\n\nBest Regards,\nHari Mardianto\nShipping Documentation",
     ["email_051_SI.txt", "email_051_BL.txt"], "BL_COMPARISON"),
    ("email_018",
     "RE_ AFPTME - SAVANNAH_US - MONTER(MCLSIN2316658) - 5RAE-69096 - 5250077054 - KPP-ANTALIS (SINGAPORE) PTE. LTD. - OA",
     "Dear Ooi,\n\nPlease assist to send the draft BL for MCLSINJEA2576036 for checking asap.\n\nThank you.\n\nBest Regards,\nElisa Tukiman\nShipping Documentation",
     [], "BL_COMPARISON"),

    ("email_054",
     "RE_ REQUEST SI _ 5RCY-45054 _ MERSIN_TURKEY _ INTERNATIONAL FOREST PRODUCTS LLC _ OOLU1704303054",
     "Hi Mitchelle\n\nPlease find Shipping instruction for 5RCY-45054.\n\nPOL: NHAVA SHEVA, INDIA\nPOD: MERSIN, TURKEY\n\nShipper:\nASIA PACIFIC PAPERBOARD TRADING PTE LTD\n80 RAFFLES PLACE, #50-01 UOB PLAZA 1\nSINGAPORE 048624\n\nConsignee:\nINTERNATIONAL FOREST PRODUCTS LLC\n...\nDocuments Required:\n1) 3 Original invoice\n2) 3 Packing list\n3) 3 Original BL + 3 N/N\nPlease revert with draft BL once available.",
     [], "SI_REQUEST"),
    ("email_030",
     "SI NEEDED_ 5RCY-63982 _ 3S PAPER PRODUCTS SDN BHD _ PO_25_2579 _ NEW YORK",
     "Hi Syed\n\nPlease find Shipping instruction for 5RCY-63982.\n\nPOL: SINGAPORE\nPOD: NEW YORK, US\n\nShipper:\nAPRIL FINE PAPER TRADING (MIDDLE EAST) FZE\n#813, 4 EA, DUBAI AIRPORT FREE ZONE\n\nConsignee:\n3S PAPER PRODUCTS SDN BHD\nNO 12, JALAN INDUSTRI 3/6\n...",
     [], "SI_REQUEST"),

    ("email_002",
     "RE_ LOCAL CHARGES FOB - KARGOSMAR - 5AKR-61849 - TELEX RELEASE CHARGES",
     "Hi,\n\nQuery on invoice 5250075931: is the THC / local charge included or billed separately? Please advise the breakdown.\n\nBest Regards,\nNajiha Nur Hanna\nShipping Documentation",
     [], "INVOICE_QUERY"),
    ("email_017",
     "Mill D & D charges - 6437419879",
     "Dear All,\n\nPlease find the D&D / detention charges for MSDUL0942527743. Kindly confirm the amount before we release payment.\n\nBest Regards,\nDeswita Elvyani\nShipping Documentation",
     [], "INVOICE_QUERY"),

    ("email_153",
     "daily Berthing Report - 28 JAN 2026",
     "Dear All,\n\nPlease find attached the update summary for SOLID 16 V.044NW2. Loading completed, documents to follow.\n\nRegards,\nDocumentation Team",
     [], "GENERAL"),
    ("email_075",
     "_Approval Required_ Time Off Request",          # ← 误导性标题
     "This is an automated notification. The India HSS SD Billing Process for MARCOPOLO 810 V.BS005 has completed successfully. No action required.\n\n-- RPA Bot",
     [], "GENERAL"),

    ("email_231",
     "Dear Valued Customer, update your account to avoid suspension",   # ← 误导性标题
     "CONGRATULATIONS!!! Your email address has been selected in our monthly draw. Click here to claim your $1,000 gift card now: http://bit.ly/claim-prize-now",
     [], "SPAM"),
    ("email_226",
     "Exclusive offer: 90% OFF premium logistics software this week only",
     "Hello Dear, I am a bank officer with an urgent business proposal involving USD 4.5 million. Please reply with your bank details to proceed.",
     [], "SPAM"),
]


def _fmt_email(subject: str, body: str, attachments: list[str]) -> str:
    att = ", ".join(attachments) if attachments else "(none)"
    return f"SUBJECT: {subject}\nATTACHMENTS: {att}\nBODY:\n{body}"


def _build_prompt(email: dict) -> str:
    parts = ["EXAMPLES (subject / attachments / body → category):\n"]
    for i, (_, s, b, a, gold) in enumerate(FEW_SHOT, 1):
        parts.append(f"--- Example {i} ---\n{_fmt_email(s, b, a)}\n→ {gold}\n")
    subject = (email.get("subject") or "").strip()
    body = (email.get("body") or "").replace("\r", "").strip()[:BODY_MAX_CHARS]
    atts = [os.path.basename(a) for a in (email.get("attachments") or [])]
    parts.append("--- Now classify this email ---\n" + _fmt_email(subject, body, atts))
    return "\n".join(parts)


# ----------------------------------------------------------------------------
# 客户端（懒加载，单例）
# ----------------------------------------------------------------------------
_client = None
_client_err: Optional[str] = None
_lock = threading.Lock()
_warned = False


def _get_client():
    """返回 genai.Client 或 None（配置缺失 / 导入失败）。只初始化一次。"""
    global _client, _client_err
    if _client is not None or _client_err is not None:
        return _client
    with _lock:
        if _client is not None or _client_err is not None:
            return _client
        _load_dotenv()
        provider = (os.getenv("LLM_PROVIDER") or "").strip().lower()
        try:
            from google import genai
            from google.genai import types
            http = types.HttpOptions(timeout=TIMEOUT_MS)
            if provider == "aistudio":
                key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
                if not key:
                    _client_err = "LLM_PROVIDER=aistudio 但未设置 GEMINI_API_KEY"
                    return None
                _client = genai.Client(api_key=key, http_options=http)
            elif provider == "vertex":
                project = os.getenv("GCP_PROJECT")
                if not project:
                    _client_err = "LLM_PROVIDER=vertex 但未设置 GCP_PROJECT"
                    return None
                _client = genai.Client(vertexai=True, project=project,
                                       location=os.getenv("GCP_LOCATION", "us-central1"),
                                       http_options=http)
            elif provider == "":
                _client_err = "LLM_PROVIDER 未设置，LLM 兜底关闭"
                return None
            else:
                _client_err = f"未知 LLM_PROVIDER={provider!r}（应为 aistudio | vertex）"
                return None
        except Exception as e:                       # 导入失败、构造失败……都不抛
            _client_err = f"LLM 客户端初始化失败: {type(e).__name__}: {e}"
            return None
    return _client


def _warn_once(msg: str) -> None:
    global _warned
    if not _warned:
        _warned = True
        print(f"  [classify_llm] {msg} → 全部退回规则结果", file=sys.stderr)


# ----------------------------------------------------------------------------
# 缓存
# ----------------------------------------------------------------------------
_cache: Optional[dict] = None


def _cache_load() -> dict:
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            _cache = {}
    return _cache


def _cache_save() -> None:
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(_cache, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass


def _prompt_version() -> str:
    """prompt / few-shot / schema 任一改动 → 旧缓存自动失效。"""
    h = hashlib.sha1()
    h.update(SYSTEM_PROMPT.encode("utf-8"))
    h.update(json.dumps(FEW_SHOT, ensure_ascii=False).encode("utf-8"))
    h.update(json.dumps(Classification.model_json_schema(), sort_keys=True).encode("utf-8"))
    return h.hexdigest()[:12]


_PROMPT_VERSION = _prompt_version()


def _cache_key(email: dict) -> str:
    """按 prompt 版本 + 邮件内容缓存；模型链内切换视为等价，答复模型记录在值里供审计。"""
    h = hashlib.sha1()
    h.update(_PROMPT_VERSION.encode()); h.update(b"\0")
    h.update((email.get("subject") or "").encode("utf-8", "replace")); h.update(b"\0")
    h.update((email.get("body") or "").encode("utf-8", "replace"))
    return h.hexdigest()


# ----------------------------------------------------------------------------
# 模型降级链 + 熔断
#   GEMINI_MODEL 可为逗号分隔的优先级列表；404（模型不存在/下线）→ 本次运行内剔除；
#   429/503 等重试后仍失败 → 冷却 COOLDOWN_S 秒，期间自动切下一个模型
# ----------------------------------------------------------------------------
COOLDOWN_S = 60.0
_dead_models: set[str] = set()
_cooldown_until: dict[str, float] = {}


def _model_chain() -> list[str]:
    raw = os.getenv("GEMINI_MODEL", "gemini-3.6-flash,gemini-3.5-flash,gemini-3.5-flash-lite")
    return [m.strip() for m in raw.split(",") if m.strip()]


def _available_models() -> list[str]:
    now = time.time()
    return [m for m in _model_chain()
            if m not in _dead_models and _cooldown_until.get(m, 0.0) <= now]


def _err_kind(e: Exception) -> str:
    s = str(e)
    if "404" in s or "NOT_FOUND" in s:
        return "gone"
    if "429" in s or "RESOURCE_EXHAUSTED" in s:
        return "quota"
    if "503" in s or "UNAVAILABLE" in s or "overloaded" in s.lower():
        return "busy"
    if "timeout" in s.lower() or "timed out" in s.lower():
        return "timeout"
    return "other"


# ----------------------------------------------------------------------------
# 统计（run.py 结尾打印）
# ----------------------------------------------------------------------------
STATS = {"calls": 0, "cache_hits": 0, "failures": 0, "retries": 0, "model_switches": 0}
MODEL_USAGE: dict[str, int] = {}                     # 每个模型实际答复的次数


# ----------------------------------------------------------------------------
# 限速：LLM_MIN_INTERVAL（秒）两次真实调用之间的最小间隔。免费额度按 RPM 限流时用。
#   gemini-2.5-flash 免费档约 10 RPM → 6.5；flash-lite 约 15 RPM → 4.5；付费/Vertex → 0
# ----------------------------------------------------------------------------
_last_call_ts = 0.0


def _pace() -> None:
    global _last_call_ts
    try:
        gap = float(os.getenv("LLM_MIN_INTERVAL", "0") or 0)
    except ValueError:
        gap = 0.0
    if gap > 0:
        wait = _last_call_ts + gap - time.time()
        if wait > 0:
            time.sleep(wait)
    _last_call_ts = time.time()


# ----------------------------------------------------------------------------
# 主入口
# ----------------------------------------------------------------------------
def classify_with_llm(email: dict) -> Optional[Classification]:
    """成功 → Classification；任何失败 → None（调用方退回规则）。永不抛异常。"""
    try:
        return _classify(email)
    except Exception as e:                           # 最后一道保险
        STATS["failures"] += 1
        _warn_once(f"未预期异常 {type(e).__name__}: {e}")
        return None


def _classify(email: dict) -> Optional[Classification]:
    client = _get_client()
    if client is None:
        _warn_once(_client_err or "LLM 不可用")
        return None

    cache = _cache_load()
    key = _cache_key(email)
    if key in cache:
        try:
            STATS["cache_hits"] += 1
            return Classification.model_validate(cache[key]["result"])
        except (ValidationError, KeyError, TypeError):
            pass                                     # 缓存坏了就重新调

    from google.genai import types
    prompt = _build_prompt(email)

    def _config(model: str):
        kw = dict(system_instruction=SYSTEM_PROMPT,
                  response_mime_type="application/json",
                  response_schema=Classification,
                  temperature=0.0,
                  max_output_tokens=256)
        if model.startswith("gemini-2.5-flash"):    # 2.5 系用 thinking_budget；分类不需要思考
            kw["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
        return types.GenerateContentConfig(**kw)

    last_err: Optional[Exception] = None
    tried: list[str] = []
    for model in _available_models():
        tried.append(model)
        if len(tried) > 1:
            STATS["model_switches"] += 1
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                _pace()
                STATS["calls"] += 1
                resp = client.models.generate_content(model=model, contents=prompt,
                                                      config=_config(model))
                parsed = resp.parsed
                if parsed is None:                   # SDK 没解析出来就自己解析
                    parsed = Classification.model_validate_json(resp.text or "")
                elif not isinstance(parsed, Classification):
                    parsed = Classification.model_validate(parsed)
                cache[key] = {"model": model, "result": parsed.model_dump()}
                _cache_save()
                MODEL_USAGE[model] = MODEL_USAGE.get(model, 0) + 1
                return parsed
            except Exception as e:
                last_err = e
                kind = _err_kind(e)
                if kind == "gone":                   # 模型不存在：不重试，直接剔除
                    _dead_models.add(model)
                    break
                if attempt < MAX_ATTEMPTS:
                    STATS["retries"] += 1
                    time.sleep(6.0 if kind == "quota" else 1.0)
                else:                                # 重试后仍失败：冷却，切下一个模型
                    _cooldown_until[model] = time.time() + COOLDOWN_S

    STATS["failures"] += 1
    if STATS["failures"] <= 3:                       # 只打前几条，避免刷屏
        print(f"  [classify_llm] {email.get('email_id')} 调用失败（已尝试 {tried or '无可用模型'}），"
              f"退回规则：{type(last_err).__name__ if last_err else '-'}: "
              f"{str(last_err)[:160] if last_err else '模型链为空或全部冷却中'}", file=sys.stderr)
    return None
