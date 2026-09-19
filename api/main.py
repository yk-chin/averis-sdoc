"""
ShipDoc API - the pipeline as a service
=======================================
POST /process        one email (attachments inline) -> decision + per-field evidence + readable report
POST /batch          up to 200 emails
GET  /report/{id}    most recent result for an email_id (instance memory; gone when the instance is recycled)
GET  /health         liveness; ?deep=1 makes one real LLM call to verify Vertex IAM auth in the cloud
GET  /               minimal test page (works on a phone)

Auth: when API_TOKEN is set (from Secret Manager on Cloud Run) the POST endpoints require a
      matching X-API-Key header; GET endpoints are public. Without API_TOKEN everything is open.
LLM:  shared with the pipeline (pipeline/classify_llm.py). With LLM_PROVIDER=vertex it uses the
      runtime's Application Default Credentials (the Cloud Run service account) - no API key.
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
_load_dotenv()                                            # local .env; absent in the cloud, env vars are used

APP_VERSION = os.getenv("APP_VERSION", "dev")
API_TOKEN = os.getenv("API_TOKEN", "").strip()
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024

app = FastAPI(title="ShipDoc API", version=APP_VERSION,
              description="Shipping-document intake: classify the email, parse the attached SI and draft BL, "
                          "compare the seven key fields deterministically, and escalate what a person must see.")

_reports: dict[str, dict] = {}                         # email_id -> most recent result (instance memory)
_started = time.time()


# ----------------------------------------------------------------------------- models
class Attachment(BaseModel):
    name: str = Field(..., description="File name, e.g. email_001_SI.txt. Names containing _SI / _BL are routed by name; otherwise the document type is detected from content.")
    content_base64: Optional[str] = Field(None, description="Binary content (pdf / docx / xlsx), base64-encoded")
    text: Optional[str] = Field(None, description="Plain-text content (txt)")


class EmailIn(BaseModel):
    email_id: Optional[str] = None
    subject: str = ""
    body: str = ""
    sender: Optional[str] = Field(None, alias="from")
    attachments: list[Attachment] = []

    model_config = {"populate_by_name": True}


class BatchIn(BaseModel):
    emails: list[EmailIn]


# ----------------------------------------------------------------------------- helpers
def _check_key(x_api_key: Optional[str]) -> None:
    if API_TOKEN and x_api_key != API_TOKEN:
        raise HTTPException(status_code=401, detail="missing or invalid X-API-Key")


def _materialise(email: EmailIn, workdir: pathlib.Path) -> dict:
    """Write inline attachments to a temp dir; return the email dict the pipeline expects."""
    att_dir = workdir / "attachments"; att_dir.mkdir(parents=True, exist_ok=True)
    rels = []
    for a in email.attachments:
        name = pathlib.Path(a.name).name                       # strip any path component
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


# ----------------------------------------------------------------------------- endpoints
@app.get("/health", summary="Liveness and configuration; ?deep=1 also exercises the LLM")
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


@app.post("/process", summary="Process one email: decision + per-field evidence")
def process(email: EmailIn, x_api_key: Optional[str] = Header(default=None)):
    _check_key(x_api_key)
    return _process_one(email)


@app.post("/batch", summary="Process up to 200 emails")
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


@app.get("/report/{email_id}", summary="Return the most recent result for an email_id (instance memory)")
def report(email_id: str):
    r = _reports.get(email_id)
    if r is None:
        raise HTTPException(404, f"no report for {email_id} (results live in instance memory; re-run /process)")
    return r


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def index():
    return (ROOT / "api" / "index.html").read_text(encoding="utf-8")


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    return JSONResponse(status_code=500, content={"detail": f"{type(exc).__name__}: {exc}"})
