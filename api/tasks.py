"""
Asynchronous processing with visible failures and retries.

  enqueue(payload)          -> Cloud Tasks (TASKS_MODE=cloud) or an in-process thread (TASKS_MODE=inline)
  run_attempt(payload, n)   -> one attempt; raises on failure
  handle(payload, attempt)  -> the retry policy: raise -> caller retries with backoff;
                               on the MAX_ATTEMPTS-th failure write dead_letter and stop

Retry policy (same in both modes): MAX_ATTEMPTS = 3, exponential backoff.
  cloud : the queue is created with --max-attempts=3 --min-backoff=5s --max-backoff=60s --max-doublings=3
  inline: sleeps 1s, 2s between attempts (kept short for local runs and tests)

Cloud Tasks calls POST /tasks/process with an OIDC token minted for TASKS_SA; the handler verifies
the token's audience and service-account email so nobody else can inject tasks.

Failure injection for demos (never on by default):
  email["fail_times"] = N       the first N attempts raise (N >= MAX_ATTEMPTS -> dead letter)
  settings/chaos {enabled:true} every attempt raises ("simulated downstream outage")
"""
from __future__ import annotations
import json, os, pathlib, tempfile, threading, time, traceback
from typing import Callable, Optional

from api.store import REPORTS, JOBS, DEAD_LETTER, SETTINGS, now

MAX_ATTEMPTS = 3
INLINE_BACKOFF_S = (1.0, 2.0)                      # attempt 1 -> wait 1s -> attempt 2 -> wait 2s -> attempt 3


class TaskContext:
    """Wiring supplied by main.py so this module stays free of FastAPI."""
    def __init__(self, store, process_fn: Callable[[dict], dict], service_url: str = "") -> None:
        self.store = store
        self.process_fn = process_fn                  # email dict -> result dict (raises on failure)
        self.service_url = service_url.rstrip("/")
        self.mode = (os.getenv("TASKS_MODE") or "inline").strip().lower()
        self.queue = os.getenv("TASKS_QUEUE", "shipdoc-process")
        self.location = os.getenv("TASKS_LOCATION", "asia-southeast1")
        self.project = os.getenv("GCP_PROJECT", "")
        self.sa = os.getenv("TASKS_SA", "")


# ----------------------------------------------------------------------------- one attempt
def run_attempt(ctx: TaskContext, payload: dict, attempt: int) -> dict:
    email = payload["email"]
    chaos = ctx.store.get(SETTINGS, "chaos") or {}
    if chaos.get("enabled"):
        raise RuntimeError("chaos mode: simulated downstream outage (settings/chaos.enabled=true)")
    fail_times = int(email.get("fail_times") or 0)
    if attempt <= fail_times:
        raise RuntimeError(f"simulated failure {attempt}/{fail_times} (fail_times set on the email)")
    return ctx.process_fn(email)


# ----------------------------------------------------------------------------- retry policy
def handle(ctx: TaskContext, payload: dict, attempt: int) -> tuple[bool, Optional[str]]:
    """Returns (done, error). done=True means the task must not be retried (success or dead-lettered);
    done=False means the caller should retry (cloud: return 5xx; inline: sleep and loop)."""
    key, email = payload["key"], payload["email"]
    ctx.store.update(REPORTS, key, {"status": "PROCESSING", "attempts": attempt, "updated": now()})
    try:
        result = run_attempt(ctx, payload, attempt)
    except Exception as e:                       # noqa: BLE001 - every failure must be visible, none silent
        reason = f"{type(e).__name__}: {e}"
        if attempt >= MAX_ATTEMPTS:
            ctx.store.set(DEAD_LETTER, key, {
                "status": "FAILED", "email_id": email.get("email_id"), "batch_id": payload.get("batch_id"),
                "reason": reason, "attempts": attempt, "traceback": traceback.format_exc()[-2000:],
                "input": email, "failed_at": now(), "updated": now(),
            })
            ctx.store.update(REPORTS, key, {"status": "FAILED", "error": reason, "attempts": attempt, "updated": now()})
            _bump_job(ctx, payload.get("batch_id"), "failed")
            return True, reason
        ctx.store.update(REPORTS, key, {"status": "RETRYING", "error": reason, "attempts": attempt, "updated": now()})
        return False, reason

    ctx.store.update(REPORTS, key, {"status": "DONE", "result": result, "error": None,
                                    "attempts": attempt, "updated": now()})
    if ctx.store.get(DEAD_LETTER, key):
        ctx.store.update(DEAD_LETTER, key, {"status": "RECOVERED", "recovered_at": now(), "updated": now()})
    _bump_job(ctx, payload.get("batch_id"), "done")
    return True, None


def _bump_job(ctx: TaskContext, batch_id: Optional[str], field: str) -> None:
    if not batch_id:
        return
    job = ctx.store.get(JOBS, batch_id) or {}
    job[field] = int(job.get(field, 0)) + 1
    job["updated"] = now()
    ctx.store.update(JOBS, batch_id, job)


# ----------------------------------------------------------------------------- enqueue
def enqueue(ctx: TaskContext, payload: dict) -> str:
    """Returns a task reference (Cloud Tasks task name or 'inline:<key>')."""
    if ctx.mode == "cloud":
        return _enqueue_cloud(ctx, payload)
    t = threading.Thread(target=_run_inline, args=(ctx, payload), daemon=True)
    t.start()
    return f"inline:{payload['key']}"


def _run_inline(ctx: TaskContext, payload: dict) -> None:
    for attempt in range(1, MAX_ATTEMPTS + 1):
        done, _ = handle(ctx, payload, attempt)
        if done:
            return
        time.sleep(INLINE_BACKOFF_S[min(attempt - 1, len(INLINE_BACKOFF_S) - 1)])


def _enqueue_cloud(ctx: TaskContext, payload: dict) -> str:
    from google.cloud import tasks_v2
    client = tasks_v2.CloudTasksClient()
    parent = client.queue_path(ctx.project, ctx.location, ctx.queue)
    url = f"{ctx.service_url}/tasks/process"
    task = tasks_v2.Task(http_request=tasks_v2.HttpRequest(
        http_method=tasks_v2.HttpMethod.POST, url=url,
        headers={"Content-Type": "application/json"},
        body=json.dumps(payload).encode("utf-8"),
        oidc_token=tasks_v2.OidcToken(service_account_email=ctx.sa, audience=url),
    ))
    created = client.create_task(parent=parent, task=task)
    return created.name


# ----------------------------------------------------------------------------- auth for the task endpoint
def verify_task_request(ctx: TaskContext, authorization: Optional[str], api_key_ok: bool) -> None:
    """Cloud Tasks -> OIDC bearer token for our service account; otherwise a valid X-API-Key is accepted
    (lets the demo page and local tests hit the endpoint directly)."""
    if api_key_ok:
        return
    if not authorization or not authorization.lower().startswith("bearer "):
        raise PermissionError("missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    from google.auth.transport import requests as ga_requests
    from google.oauth2 import id_token
    claims = id_token.verify_oauth2_token(token, ga_requests.Request(), audience=f"{ctx.service_url}/tasks/process")
    if ctx.sa and claims.get("email") != ctx.sa:
        raise PermissionError(f"token issued for {claims.get('email')}, expected {ctx.sa}")
