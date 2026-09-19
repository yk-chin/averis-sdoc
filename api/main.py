"""
shipdoc API —— 把流水线包成服务
==============================
POST /process        一封邮件（附件内联）→ 判定 + 每字段证据 + 可读报告
POST /batch          批量
GET  /report/{id}    取回最近一次 /process 或 /batch 的结果（进程内存；实例回收后即失效）
GET  /health         存活；?deep=1 时真调一次 LLM，验证云上 Vertex IAM 认证
GET  /               极简测试页（手机可用）

认证：环境变量 API_TOKEN 存在时（Cloud Run 上来自 Secret Manager），POST 端点要求
      请求头 X-API-Key 匹配；GET 端点公开。本地不设 API_TOKEN 则全部公开。
LLM：与流水线共用 pipeline/classify_llm.py —— LLM_PROVIDER=vertex 时用运行环境的 ADC
      （Cloud Run 服务账号），不需要 API key。
"""
from __future__ import annotations
import base64, os, pathlib, sys, tempfile, time, uuid
from typing import Any, Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pipeline.run import decide                         # noqa: E402
from pipeline.classify_llm import classify_with_llm, STATS as LLM_STATS, MODEL_USAGE   # noqa: E402
from pipeline.classify_llm import _load_dotenv                                     # noqa: E402
_load_dotenv()                                            # 本地 .env；云上无此文件，走环境变量

APP_VERSION = os.getenv("APP_VERSION", "dev")
API_TOKEN = os.getenv("API_TOKEN", "").strip()
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024

app = FastAPI(title="shipdoc API", version=APP_VERSION,
              description="Shipping-document intake: classify → parse → compare SI vs draft BL → escalate.")

_reports: dict[str, dict] = {}                         # email_id → 最近结果（进程内存）
_started = time.time()


# ----------------------------------------------------------------------------- 模型
class Attachment(BaseModel):
    name: str = Field(..., description="文件名，如 email_001_SI.txt；无 _SI/_BL 标记时按内容识别")
    content_base64: Optional[str] = Field(None, description="二进制内容（pdf/docx/xlsx）base64")
    text: Optional[str] = Field(None, description="纯文本内容（txt）")


class EmailIn(BaseModel):
    email_id: Optional[str] = None
    subject: str = ""
    body: str = ""
    sender: Optional[str] = Field(None, alias="from")
    attachments: list[Attachment] = []

    model_config = {"populate_by_name": True}


class BatchIn(BaseModel):
    emails: list[EmailIn]


# ----------------------------------------------------------------------------- 工具
def _check_key(x_api_key: Optional[str]) -> None:
    if API_TOKEN and x_api_key != API_TOKEN:
        raise HTTPException(status_code=401, detail="missing or invalid X-API-Key")


def _materialise(email: EmailIn, workdir: pathlib.Path) -> dict:
    """把内联附件写到临时目录，返回流水线期望的 email dict。"""
    att_dir = workdir / "attachments"; att_dir.mkdir(parents=True, exist_ok=True)
    rels = []
    for a in email.attachments:
        name = pathlib.Path(a.name).name                       # 去掉路径成分
        if not name:
            raise HTTPException(422, "attachment name is empty")
        if a.content_base64 is not None:
            try:
                data = base64.b64decode(a.content_base64, validate=True)
            except Exception:
                raise HTTPException(422, f"attachment {name}: invalid base64")
        elif a.text is not None:
            data = a.text.encode("utf-8")
        else:
            raise HTTPException(422, f"attachment {name}: provide content_base64 or text")
        if len(data) > MAX_ATTACHMENT_BYTES:
            raise HTTPException(413, f"attachment {name} exceeds {MAX_ATTACHMENT_BYTES} bytes")
        (att_dir / name).write_bytes(data)
        rels.append(f"attachments/{name}")
    return {"email_id": email.email_id or f"api-{uuid.uuid4().hex[:8]}",
            "from": email.sender or "", "subject": email.subject, "body": email.body,
            "attachments": rels}


def _process_one(email: EmailIn) -> dict:
    t0 = time.time()
    with tempfile.TemporaryDirectory(prefix="shipdoc-") as tmp:
        root = pathlib.Path(tmp)
        e = _materialise(email, root)
        details: dict = {}
        decision = decide(e, root, details=details)
    result = {"email_id": e["email_id"], "decision": decision, "evidence": details,
              "elapsed_ms": round((time.time() - t0) * 1000), "version": APP_VERSION}
    _reports[e["email_id"]] = result
    return result


# ----------------------------------------------------------------------------- 端点
@app.get("/health")
def health(deep: int = 0):
    info: dict[str, Any] = {
        "status": "ok", "version": APP_VERSION, "uptime_s": round(time.time() - _started),
        "llm_provider": os.getenv("LLM_PROVIDER") or "(disabled)",
        "gemini_model": os.getenv("GEMINI_MODEL", ""), "gcp_project": os.getenv("GCP_PROJECT", ""),
        "api_key_required": bool(API_TOKEN), "reports_in_memory": len(_reports),
        "llm_stats": dict(LLM_STATS), "llm_models_used": dict(MODEL_USAGE),
    }
    if deep:
        t0 = time.time()
        r = classify_with_llm({"email_id": "health", "subject": "Query on invoice 123",
                               "body": "Hi, is the THC included in invoice 123? Please advise.", "attachments": []})
        info["llm_check"] = {"ok": r is not None, "elapsed_ms": round((time.time() - t0) * 1000),
                             "result": r.model_dump() if r else None}
        if r is None:
            info["status"] = "degraded"
    return info


@app.post("/process")
def process(email: EmailIn, x_api_key: Optional[str] = Header(default=None)):
    _check_key(x_api_key)
    return _process_one(email)


@app.post("/batch")
def batch(payload: BatchIn, x_api_key: Optional[str] = Header(default=None)):
    _check_key(x_api_key)
    if len(payload.emails) > 200:
        raise HTTPException(413, "max 200 emails per batch")
    results = [_process_one(e) for e in payload.emails]
    summary: dict[str, int] = {}
    for r in results:
        d = r["decision"]
        summary[d["category"]] = summary.get(d["category"], 0) + 1
        if d["category"] == "BL_COMPARISON":
            summary["status:" + d["status"]] = summary.get("status:" + d["status"], 0) + 1
    return {"count": len(results), "summary": summary, "results": results}


@app.get("/report/{email_id}")
def report(email_id: str):
    r = _reports.get(email_id)
    if r is None:
        raise HTTPException(404, f"no report for {email_id} (results live in instance memory; re-run /process)")
    return r


@app.get("/", response_class=HTMLResponse)
def index():
    return (ROOT / "api" / "index.html").read_text(encoding="utf-8")


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    return JSONResponse(status_code=500, content={"detail": f"{type(exc).__name__}: {exc}"})
