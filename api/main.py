"""
ShipDoc API - the pipeline as a service
=======================================
POST /process              one email (attachments inline) -> decision + per-field evidence, synchronous
POST /batch                one Cloud Tasks task per email; returns batch_id immediately
GET  /batch/{batch_id}     progress and per-email status of a batch
GET  /report/{id}          result by email_id or idempotency key (persisted in Firestore)
GET  /failures             the dead-letter queue (status FAILED)
POST /failures/{key}/retry re-enqueue a dead-lettered email from its stored input
POST /tasks/process        Cloud Tasks callback (OIDC-verified); one attempt of one email
POST /admin/chaos          demo switch: simulate a downstream outage for every task
GET  /health               liveness; ?deep=1 makes one real LLM call
GET  /                     test / demo page

Auth, tiered by cost:
      anonymous  - GET /, GET /health (no project id / model chain / internal counters), GET /report/{id}
                   (unguessable id, never a traceback), GET /batch/{id}, and POST /process (one email;
                   rate-limited per client IP, RATE_LIMIT_PER_MIN requests per minute, HTTP 429 above that)
      X-API-Key  - POST /batch (a batch can burn the whole LLM quota), GET /failures and
                   GET /failures/{key} (original inputs and tracebacks), POST /failures/{key}/retry,
                   POST /admin/chaos. The key comes from Secret Manager on Cloud Run (API_TOKEN);
                   without API_TOKEN set, these are open too (local development).
LLM:  shared with the pipeline (pipeline/classify_llm.py). With LLM_PROVIDER=vertex it uses the
      runtime's Application Default Credentials (the Cloud Run service account) - no API key.

Failure handling ("handle processing failures visibly and allow retries"):
  each task gets MAX_ATTEMPTS (3) tries with exponential backoff; the third failure is written to the
  dead_letter collection with the reason and the original input, listed by GET /failures, and can be
  retried by POST /failures/{key}/retry. Idempotency key = email_id + content hash: re-submitting the
  same email never produces a second report.
"""
from __future__ import annotations
import base64, collections, os, pathlib, sys, tempfile, threading, time, uuid
from typing import Any, Literal, Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pipeline.run import decide                                                     # noqa: E402
from pipeline.classify_llm import classify_with_llm                               # noqa: E402
from pipeline.classify_llm import _load_dotenv                                      # noqa: E402
_load_dotenv()                                            # local .env; absent in the cloud, env vars are used
from api.store import make_store, idempotency_key, now, REPORTS, JOBS, DEAD_LETTER, SETTINGS  # noqa: E402
from api import tasks as taskmod                                                    # noqa: E402

APP_VERSION = os.getenv("APP_VERSION", "dev")
API_TOKEN = os.getenv("API_TOKEN", "").strip()
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
RATE_LIMIT_PER_MIN = int(os.getenv("RATE_LIMIT_PER_MIN", "10"))   # anonymous POST /process, per client IP
KEYED_ENDPOINTS = ["POST /batch", "GET /failures", "GET /failures/{key}", "POST /failures/{key}/retry",
                   "POST /report/{id}/review", "POST /admin/chaos"]

app = FastAPI(title="ShipDoc API", version=APP_VERSION,
              description="Shipping-document intake: classify the email, parse the attached SI and draft BL, "
                          "compare the seven key fields deterministically, and escalate what a person must see. "
                          "Batches run through Cloud Tasks with 3 retries and a visible dead-letter queue.")
app.mount("/static", StaticFiles(directory=str(ROOT / "api" / "static")), name="static")   # fonts for the demo page

store = make_store()
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
    fail_times: int = Field(0, ge=0, description="Demo only: make the first N processing attempts fail")

    model_config = {"populate_by_name": True}


class BatchIn(BaseModel):
    emails: list[EmailIn]


class ReviewIn(BaseModel):
    """Human-in-the-loop: "let a person confirm or correct it, then update the report"."""
    decision: Literal["confirmed", "corrected"]
    corrected_fields: dict[str, Any] = Field(default_factory=dict,
        description="Only for decision=corrected: the decision fields the reviewer changes, e.g. {\"status\": \"OK\", \"has_defect\": false, \"defect_fields\": []}")
    reviewer_note: str = Field("", max_length=2000)
    reviewer: str = Field(..., min_length=1, max_length=200)


# ----------------------------------------------------------------------------- helpers
def _check_key(x_api_key: Optional[str]) -> None:
    if API_TOKEN and x_api_key != API_TOKEN:
        raise HTTPException(status_code=401, detail="missing or invalid X-API-Key")


# Per-IP sliding window for the anonymous endpoint. Standard library only; state is per instance,
# which is enough to stop a single client hammering one instance (Cloud Run runs 0-3 of them).
_rate_hits: dict[str, collections.deque] = collections.defaultdict(collections.deque)
_rate_lock = threading.Lock()


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")        # Cloud Run puts the client first
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "unknown")


def _rate_limit(request: Request) -> None:
    now_ts = time.time()
    ip = _client_ip(request)
    with _rate_lock:
        hits = _rate_hits[ip]
        while hits and hits[0] <= now_ts - 60:
            hits.popleft()
        if len(hits) >= RATE_LIMIT_PER_MIN:
            retry_after = int(hits[0] + 60 - now_ts) + 1
            raise HTTPException(status_code=429, headers={"Retry-After": str(retry_after)},
                                detail=f"rate limit: {RATE_LIMIT_PER_MIN} requests per minute per IP; retry in {retry_after}s")
        hits.append(now_ts)


def _email_dict(email: EmailIn) -> dict:
    d = email.model_dump(by_alias=True, exclude_none=True)
    d["email_id"] = email.email_id or f"api-{uuid.uuid4().hex[:8]}"
    return d


def _materialise(email: dict, workdir: pathlib.Path) -> dict:
    """Write inline attachments to a temp dir; return the email dict the pipeline expects."""
    att_dir = workdir / "attachments"; att_dir.mkdir(parents=True, exist_ok=True)
    rels = []
    for a in email.get("attachments") or []:
        name = pathlib.Path(a.get("name", "")).name                   # strip any path component
        if not name:
            raise ValueError("attachment name is empty")
        if a.get("content_base64") is not None:
            try:
                data = base64.b64decode(a["content_base64"], validate=True)
            except Exception:
                raise ValueError(f"attachment {name}: invalid base64")
        elif a.get("text") is not None:
            data = a["text"].encode("utf-8")
        else:
            raise ValueError(f"attachment {name}: provide content_base64 or text")
        if len(data) > MAX_ATTACHMENT_BYTES:
            raise ValueError(f"attachment {name} exceeds {MAX_ATTACHMENT_BYTES} bytes")
        (att_dir / name).write_bytes(data)
        rels.append(f"attachments/{name}")
    return {"email_id": email["email_id"], "from": email.get("from") or "",
            "subject": email.get("subject", ""), "body": email.get("body", ""), "attachments": rels}


def _email_summary(e: dict) -> dict:
    """What the console needs to list and show an email without the attachment payloads."""
    return {"from": e.get("from"), "subject": e.get("subject"), "body": e.get("body"),
            "attachments": [a.get("name") for a in e.get("attachments") or []]}


def _process_email(email: dict) -> dict:
    """Run the pipeline on one email dict. Raises on any failure (that is what the task layer relies on)."""
    t0 = time.time()
    with tempfile.TemporaryDirectory(prefix="shipdoc-") as tmp:
        root = pathlib.Path(tmp)
        e = _materialise(email, root)
        details: dict = {}
        decision = decide(e, root, details=details)
    return {"email_id": e["email_id"], "decision": decision, "review_detail": details.get("review_detail", []),
            "evidence": details, "elapsed_ms": round((time.time() - t0) * 1000), "version": APP_VERSION}


def _ctx(request: Request) -> taskmod.TaskContext:
    base = os.getenv("SERVICE_URL") or str(request.base_url)
    return taskmod.TaskContext(store, _process_email, service_url=base)


# ----------------------------------------------------------------------------- endpoints
@app.get("/health", summary="Liveness and configuration; ?deep=1 also exercises the LLM")
def health(deep: int = 0):
    chaos = store.get(SETTINGS, "chaos") or {}
    # Public: no project id, model chain or internal LLM counters (F7).
    info: dict[str, Any] = {
        "status": "ok", "version": APP_VERSION, "uptime_s": round(time.time() - _started),
        "llm_provider": os.getenv("LLM_PROVIDER") or "(disabled)",
        "api_key_required_for": KEYED_ENDPOINTS if API_TOKEN else [],
        "anonymous_rate_limit_per_min": RATE_LIMIT_PER_MIN,
        "tasks_mode": os.getenv("TASKS_MODE", "inline"), "store": os.getenv("STORE", "memory"),
        "max_attempts": taskmod.MAX_ATTEMPTS, "chaos_enabled": bool(chaos.get("enabled")),
    }
    if deep:
        t0 = time.time()
        r = classify_with_llm({"email_id": "health", "subject": "Query on invoice 123",
                               "body": "Hi, is the THC included in invoice 123? Please advise.", "attachments": []})
        info["llm_check"] = {"ok": r is not None, "elapsed_ms": round((time.time() - t0) * 1000)}
        if r is None:
            info["status"] = "degraded"
    return info


@app.post("/process", summary="Process one email synchronously: decision + per-field evidence (no key needed, rate-limited)")
def process(email: EmailIn, request: Request):
    _rate_limit(request)
    e = _email_dict(email)
    try:
        result = _process_email(e)
    except ValueError as ex:
        raise HTTPException(422, str(ex))
    key = idempotency_key(e)
    store.set(REPORTS, key, {"status": "DONE", "email_id": e["email_id"], "key": key, "result": result,
                             "email": _email_summary(e), "attempts": 1, "error": None, "updated": now()})
    return dict(result, key=key)


@app.post("/batch", summary="Enqueue one Cloud Tasks task per email; returns immediately (X-API-Key required)")
def batch(payload: BatchIn, request: Request, x_api_key: Optional[str] = Header(default=None)):
    _check_key(x_api_key)
    if len(payload.emails) > 200:
        raise HTTPException(413, "max 200 emails per batch")
    ctx = _ctx(request)
    batch_id = f"batch-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    items = []
    for em in payload.emails:
        e = _email_dict(em)
        key = idempotency_key(e)
        existing = store.get(REPORTS, key)
        if existing and existing.get("status") in ("QUEUED", "PROCESSING", "RETRYING", "DONE"):
            items.append({"email_id": e["email_id"], "key": key, "status": "duplicate",
                          "existing_status": existing["status"]})
            continue
        store.set(REPORTS, key, {"status": "QUEUED", "email_id": e["email_id"], "key": key, "batch_id": batch_id,
                                 "email": _email_summary(e), "attempts": 0, "error": None, "result": None,
                                 "updated": now()})
        ref = taskmod.enqueue(ctx, {"key": key, "batch_id": batch_id, "email": e})
        items.append({"email_id": e["email_id"], "key": key, "status": "queued", "task": ref})
    queued = sum(1 for i in items if i["status"] == "queued")
    store.set(JOBS, batch_id, {"batch_id": batch_id, "total": queued, "duplicates": len(items) - queued,
                               "keys": [i["key"] for i in items if i["status"] == "queued"], "updated": now()})
    return {"batch_id": batch_id, "queued": queued, "duplicates": len(items) - queued, "items": items,
            "mode": ctx.mode}


@app.get("/batch/{batch_id}", summary="Progress of a batch and the status of each email")
def batch_status(batch_id: str):
    job = store.get(JOBS, batch_id)
    if not job:
        raise HTTPException(404, f"no batch {batch_id}")
    rows = []
    for key in job.get("keys", []):
        r = store.get(REPORTS, key) or {}
        rows.append({"key": key, "email_id": r.get("email_id"), "status": r.get("status"),
                     "attempts": r.get("attempts"), "error": r.get("error"),
                     "category": (r.get("result") or {}).get("decision", {}).get("category"),
                     "decision_status": (r.get("result") or {}).get("decision", {}).get("status")})
    # counts are derived from the per-email status documents (race-free), never from counters
    done = sum(1 for r in rows if r["status"] == "DONE")
    failed = sum(1 for r in rows if r["status"] == "FAILED")
    pending = len(rows) - done - failed
    return {"batch_id": batch_id, "total": job.get("total"), "done": done, "failed": failed,
            "pending": pending, "duplicates": job.get("duplicates", 0), "items": rows}


def _find_report(ident: str) -> tuple[str, dict]:
    """By idempotency key, else the most recent report for that email_id. Returns (key, doc)."""
    r = store.get(REPORTS, ident)
    if r is not None:
        return ident, r
    hits = store.list(REPORTS, where=("email_id", "==", ident), limit=1)
    if hits:
        return hits[0]["id"], hits[0]
    raise HTTPException(404, f"no report for {ident}")


def _effective_decision(r: dict) -> Optional[dict]:
    """The AI decision, with the reviewer's corrections applied when the review says 'corrected'.
    The AI's original decision is never modified; this is a derived view."""
    ai = (r.get("result") or {}).get("decision")
    rev = r.get("review")
    if ai is None:
        return None
    if rev and rev.get("human_decision") == "corrected":
        return {**ai, **rev.get("corrections", {})}
    return ai


def _report_row(r: dict) -> dict:
    """One slim row for the console: decision + email header, never evidence or body."""
    res = r.get("result") or {}
    d = res.get("decision") or {}
    em = r.get("email") or {}
    return {"key": r.get("key") or r.get("id"), "email_id": r.get("email_id"), "from": em.get("from"),
            "subject": em.get("subject"), "attachments": len(em.get("attachments") or []),
            "report_status": r.get("status"), "category": d.get("category"), "status": d.get("status"),
            "review_reason": d.get("review_reason"), "review_detail": res.get("review_detail") or [],
            "has_defect": d.get("has_defect", False), "defect_fields": d.get("defect_fields") or [],
            "decided_by": d.get("decided_by"), "updated": r.get("updated")}


@app.get("/reports", summary="List processed emails (slim rows for the console; no key needed)")
def reports(limit: int = 1000, category: Optional[str] = None, status: Optional[str] = None,
            review_reason: Optional[str] = None, prefix: Optional[str] = None):
    """Filters run in Python after one ordered read so no composite index is needed; 520 emails is small."""
    limit = max(1, min(limit, 1000))
    rows, seen = [], set()
    for r in store.list(REPORTS, limit=limit):        # newest first: keep one row per email_id (a re-sent
        if r.get("email_id") in seen:                 # email with changed content gets a new key)
            continue
        seen.add(r.get("email_id")); rows.append(_report_row(r))
    if prefix:                                        # e.g. prefix=email_ keeps demo-page rows out of the inbox
        rows = [r for r in rows if (r["email_id"] or "").startswith(prefix)]
    if category:
        rows = [r for r in rows if r["category"] == category]
    if status:
        rows = [r for r in rows if r["status"] == status]
    if review_reason:
        rows = [r for r in rows if r["review_reason"] == review_reason]
    return {"count": len(rows), "items": rows}


@app.get("/report/{ident}", summary="Result by idempotency key or email_id")
def report(ident: str):
    key, r = _find_report(ident)
    r.pop("traceback", None)                         # never part of a public response
    r["key"] = key
    r["effective_decision"] = _effective_decision(r)
    return r


@app.post("/report/{ident}/review", summary="Human review: confirm or correct the AI decision (X-API-Key required)")
def review_report(ident: str, body: ReviewIn, x_api_key: Optional[str] = Header(default=None)):
    """Persists an audit trail next to the report - original_ai_decision / human_decision / corrections /
    reviewed_at / reviewer - and never overwrites the AI's result. Not used by the pipeline or by
    submission.json; it only changes what /report shows as effective_decision."""
    _check_key(x_api_key)
    key, r = _find_report(ident)
    ai = (r.get("result") or {}).get("decision")
    if ai is None:
        raise HTTPException(409, f"report {key} has no AI decision yet (status {r.get('status')})")
    if body.decision == "corrected" and not body.corrected_fields:
        raise HTTPException(422, "decision=corrected requires corrected_fields")
    unknown = set(body.corrected_fields) - set(ai)
    if unknown:
        raise HTTPException(422, f"corrected_fields contains unknown decision fields: {sorted(unknown)}")
    review = {
        "original_ai_decision": dict(ai),
        "human_decision": body.decision,
        "corrections": dict(body.corrected_fields) if body.decision == "corrected" else {},
        "reviewer_note": body.reviewer_note,
        "reviewer": body.reviewer,
        "reviewed_at": now(),
    }
    store.update(REPORTS, key, {"review": review, "updated": now()})
    r = store.get(REPORTS, key) or dict(r, review=review)
    return {"key": key, "email_id": r.get("email_id"), "review": review, "effective_decision": _effective_decision(r)}


@app.get("/failures", summary="The dead-letter queue (X-API-Key required)")
def failures(all: int = 0, limit: int = 100, x_api_key: Optional[str] = Header(default=None)):
    _check_key(x_api_key)
    where = None if all else ("status", "==", "FAILED")
    rows = store.list(DEAD_LETTER, where=where, limit=limit)
    for r in rows:                                    # keep the listing light; input is available per item
        inp = r.get("input") or {}
        r["input_summary"] = {"email_id": inp.get("email_id"), "subject": inp.get("subject"),
                              "attachments": [a.get("name") for a in inp.get("attachments") or []],
                              "fail_times": inp.get("fail_times", 0)}
        r.pop("input", None); r.pop("traceback", None)
    return {"count": len(rows), "items": rows}


@app.get("/failures/{key}", summary="One dead-letter item with its original input and traceback (X-API-Key required)")
def failure(key: str, x_api_key: Optional[str] = Header(default=None)):
    _check_key(x_api_key)
    r = store.get(DEAD_LETTER, key)
    if r is None:
        raise HTTPException(404, f"no dead-letter item {key}")
    return dict(r, key=key)


@app.post("/failures/{key}/retry", summary="Retry a dead-lettered email from its stored input (X-API-Key required)")
def retry(key: str, request: Request, x_api_key: Optional[str] = Header(default=None)):
    _check_key(x_api_key)
    r = store.get(DEAD_LETTER, key)
    if r is None:
        raise HTTPException(404, f"no dead-letter item {key}")
    email = dict(r.get("input") or {})
    email.pop("fail_times", None)                     # the operator fixed the cause; retry without the injected fault
    ctx = _ctx(request)
    batch_id = r.get("batch_id")
    store.update(DEAD_LETTER, key, {"status": "RETRYING", "retried_at": now(), "updated": now(),
                                    "retry_count": int(r.get("retry_count", 0)) + 1})
    store.update(REPORTS, key, {"status": "QUEUED", "error": None, "updated": now()})
    ref = taskmod.enqueue(ctx, {"key": key, "batch_id": batch_id, "email": email, "retry": True})
    return {"key": key, "status": "RETRYING", "task": ref, "mode": ctx.mode}


@app.post("/tasks/process", summary="Cloud Tasks callback: one attempt of one email", include_in_schema=True)
async def tasks_process(request: Request, authorization: Optional[str] = Header(default=None),
                        x_api_key: Optional[str] = Header(default=None),
                        x_cloudtasks_taskretrycount: Optional[str] = Header(default=None)):
    ctx = _ctx(request)
    try:
        taskmod.verify_task_request(ctx, authorization, api_key_ok=bool(API_TOKEN) and x_api_key == API_TOKEN)
    except Exception as ex:                          # noqa: BLE001
        raise HTTPException(401, f"task auth failed: {ex}")
    payload = await request.json()
    attempt = int(x_cloudtasks_taskretrycount or 0) + 1
    done, error = taskmod.handle(ctx, payload, attempt)
    if done:
        return {"key": payload.get("key"), "attempt": attempt, "status": "failed" if error else "done", "error": error}
    # not done -> 5xx so Cloud Tasks retries with the queue's exponential backoff
    return JSONResponse(status_code=503, content={"key": payload.get("key"), "attempt": attempt,
                                                  "status": "retry", "error": error})


@app.post("/admin/chaos", summary="Demo switch: make every task attempt fail (simulated outage; X-API-Key required)")
def chaos(enabled: bool, x_api_key: Optional[str] = Header(default=None)):
    _check_key(x_api_key)
    store.set(SETTINGS, "chaos", {"enabled": enabled, "updated": now()})
    return {"chaos_enabled": enabled}


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def index():
    return (ROOT / "api" / "index.html").read_text(encoding="utf-8")


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    return JSONResponse(status_code=500, content={"detail": f"{type(exc).__name__}: {exc}"})
