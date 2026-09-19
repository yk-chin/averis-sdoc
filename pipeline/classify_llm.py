"""
LLM fallback classifier - called only when the rule confidence is too low
=========================================================================
- provider selected by LLM_PROVIDER: aistudio (API key, free tier for development) | vertex (GCP project + ADC, for delivery)
- structured output: response_schema = pydantic model, validated by the SDK and again locally
- 10 s timeout (the API minimum), 1 retry; any failure returns None and the caller keeps the rule result
- never raises into the main pipeline
- model fallback chain: GEMINI_MODEL may be a comma-separated priority list; 404 drops a model, 429/503 cools it for 60 s and switches
- results cached in .cache/ by hash of (prompt version, subject, body), so repeated evals are free;
  changing the prompt / few-shot / schema invalidates the old cache automatically

Environment (may live in .env, see .env.example):
  LLM_PROVIDER      aistudio | vertex          unset -> LLM off, everything goes by rules
  GEMINI_API_KEY    required for aistudio
  GEMINI_MODEL      default gemini-3.6-flash,gemini-3.5-flash,gemini-3.5-flash-lite
  GCP_PROJECT       required for vertex
  GCP_LOCATION      optional for vertex, default us-central1
  LLM_MIN_INTERVAL  minimum seconds between calls, for free-tier rate limits, default 0
"""
from __future__ import annotations
import hashlib, json, logging, os, pathlib, re, sys, threading, time
from typing import Literal, Optional

from pydantic import BaseModel, Field, ValidationError

logging.getLogger("google_genai").setLevel(logging.ERROR)   # silence the SDK's AFC notice and other irrelevant warnings

ROOT = pathlib.Path(__file__).resolve().parents[1]
CACHE_PATH = ROOT / ".cache" / "llm_classify.json"
TIMEOUT_MS = 10_000                   # Gemini API hard minimum of 10 s (8 s is rejected with 400: "Minimum allowed deadline is 10s")
MAX_ATTEMPTS = 2                      # 1 attempt + 1 retry
BODY_MAX_CHARS = 1_500                # SI bodies are long; signatures / quoted mail carry no signal

Category = Literal["BL_COMPARISON", "SI_REQUEST", "INVOICE_QUERY", "GENERAL", "SPAM"]


class Classification(BaseModel):
    category: Category
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(max_length=200, description="one sentence quoting the evidence in the body")


# ----------------------------------------------------------------------------
# .env loading (no python-dotenv dependency; existing environment variables win)
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

# few-shot: 2 per class, all from the real inbox (bodies shortened)
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
     "_Approval Required_ Time Off Request",          # <- misleading subject
     "This is an automated notification. The India HSS SD Billing Process for MARCOPOLO 810 V.BS005 has completed successfully. No action required.\n\n-- RPA Bot",
     [], "GENERAL"),

    ("email_231",
     "Dear Valued Customer, update your account to avoid suspension",   # <- misleading subject
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
# client (lazy, singleton)
# ----------------------------------------------------------------------------
_client = None
_client_err: Optional[str] = None
_lock = threading.Lock()
_warned = False


def _get_client():
    """Returns genai.Client or None (missing config / import failure). Initialised once."""
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
                    _client_err = "LLM_PROVIDER=aistudio but GEMINI_API_KEY is not set"
                    return None
                _client = genai.Client(api_key=key, http_options=http)
            elif provider == "vertex":
                project = os.getenv("GCP_PROJECT")
                if not project:
                    _client_err = "LLM_PROVIDER=vertex but GCP_PROJECT is not set"
                    return None
                _client = genai.Client(vertexai=True, project=project,
                                       location=os.getenv("GCP_LOCATION", "us-central1"),
                                       http_options=http)
            elif provider == "":
                _client_err = "LLM_PROVIDER not set, LLM fallback disabled"
                return None
            else:
                _client_err = f"unknown LLM_PROVIDER={provider!r} (expected aistudio | vertex)"
                return None
        except Exception as e:                       # import or construction failure - never raise
            _client_err = f"LLM client initialisation failed: {type(e).__name__}: {e}"
            return None
    return _client


def _warn_once(msg: str) -> None:
    global _warned
    if not _warned:
        _warned = True
        print(f"  [classify_llm] {msg} -> falling back to rule results", file=sys.stderr)


# ----------------------------------------------------------------------------
# cache
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
    """Any change to the prompt / few-shot / schema -> the old cache is invalidated automatically."""
    h = hashlib.sha1()
    h.update(SYSTEM_PROMPT.encode("utf-8"))
    h.update(json.dumps(FEW_SHOT, ensure_ascii=False).encode("utf-8"))
    h.update(json.dumps(Classification.model_json_schema(), sort_keys=True).encode("utf-8"))
    return h.hexdigest()[:12]


_PROMPT_VERSION = _prompt_version()


def _cache_key(email: dict) -> str:
    """Cache by prompt version + email content; switching within the model chain is treated as equivalent, the answering model is recorded in the value for audit."""
    h = hashlib.sha1()
    h.update(_PROMPT_VERSION.encode()); h.update(b"\0")
    h.update((email.get("subject") or "").encode("utf-8", "replace")); h.update(b"\0")
    h.update((email.get("body") or "").encode("utf-8", "replace"))
    return h.hexdigest()


# ----------------------------------------------------------------------------
# Model fallback chain + circuit breaker
#   GEMINI_MODEL may be a comma-separated priority list; 404 (model missing / retired) -> dropped for this run;
#   429/503 still failing after the retry -> cooled down for COOLDOWN_S seconds, next model used meanwhile
# ----------------------------------------------------------------------------
COOLDOWN_S = 60.0
_dead_models: set[str] = set()
_cooldown_until: dict[str, float] = {}


def _model_chain() -> list[str]:
    """Comma- or semicolon-separated (Cloud Run --set-env-vars uses commas between pairs, so values use semicolons)."""
    raw = os.getenv("GEMINI_MODEL", "gemini-3.6-flash,gemini-3.5-flash,gemini-3.5-flash-lite")
    return [m.strip() for m in re.split(r"[,;]", raw) if m.strip()]


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
# statistics (printed at the end of run.py)
# ----------------------------------------------------------------------------
STATS = {"calls": 0, "cache_hits": 0, "failures": 0, "retries": 0, "model_switches": 0}
MODEL_USAGE: dict[str, int] = {}                     # how many answers each model actually produced


# ----------------------------------------------------------------------------
# Rate limiting: LLM_MIN_INTERVAL (seconds) between two real calls, for RPM-limited free tiers.
#   gemini-2.5-flash free tier ~10 RPM -> 6.5; flash-lite ~15 RPM -> 4.5; paid / Vertex -> 0
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
# entry point
# ----------------------------------------------------------------------------
def classify_with_llm(email: dict) -> Optional[Classification]:
    """Success -> Classification; any failure -> None (the caller falls back to rules). Never raises."""
    try:
        return _classify(email)
    except Exception as e:                           # last line of defence
        STATS["failures"] += 1
        _warn_once(f"unexpected exception {type(e).__name__}: {e}")
        return None


def _classify(email: dict) -> Optional[Classification]:
    client = _get_client()
    if client is None:
        _warn_once(_client_err or "LLM unavailable")
        return None

    cache = _cache_load()
    key = _cache_key(email)
    if key in cache:
        try:
            STATS["cache_hits"] += 1
            return Classification.model_validate(cache[key]["result"])
        except (ValidationError, KeyError, TypeError):
            pass                                     # corrupt cache entry -> call again

    from google.genai import types
    prompt = _build_prompt(email)

    def _config(model: str):
        kw = dict(system_instruction=SYSTEM_PROMPT,
                  response_mime_type="application/json",
                  response_schema=Classification,
                  temperature=0.0,
                  max_output_tokens=256)
        if model.startswith("gemini-2.5-flash"):    # 2.5 series takes thinking_budget; classification needs no thinking
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
                if parsed is None:                   # if the SDK did not parse it, parse it ourselves
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
                if kind == "gone":                   # model missing: no retry, drop it
                    _dead_models.add(model)
                    break
                if attempt < MAX_ATTEMPTS:
                    STATS["retries"] += 1
                    time.sleep(6.0 if kind == "quota" else 1.0)
                else:                                # still failing after the retry: cool down, switch to the next model
                    _cooldown_until[model] = time.time() + COOLDOWN_S

    STATS["failures"] += 1
    if STATS["failures"] <= 3:                       # print only the first few, avoid flooding
        print(f"  [classify_llm] {email.get('email_id')} call failed (tried {tried or 'no model available'}), "
              f"falling back to rules: {type(last_err).__name__ if last_err else '-'}: "
              f"{str(last_err)[:160] if last_err else 'model chain empty or all cooling down'}", file=sys.stderr)
    return None
