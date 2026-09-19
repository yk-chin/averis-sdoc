"""Retry / dead-letter / idempotency behaviour of the async layer (memory store, inline tasks)."""
import os, pathlib, sys, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
os.environ["STORE"] = "memory"
os.environ["TASKS_MODE"] = "inline"
os.environ.pop("API_TOKEN", None)
os.environ.pop("LLM_PROVIDER", None)          # rules only - no network in tests

from api.store import MemoryStore, idempotency_key, REPORTS, DEAD_LETTER, SETTINGS
from api import tasks as taskmod

SI = "SHIPPING INSTRUCTION\nShipper: ABC CO LTD\nConsignee: XYZ LLC\nNotify: XYZ LLC\nPOL: SINGAPORE\nPOD: PORT KLANG\nContainer Count: 3\nGross Weight: 22000 KG\n"
BL = "BILL OF LADING (DRAFT)\nShipper: ABC Co Ltd\nConsignee: XYZ LLC\nNotify: XYZ LLC\nPort of Loading: SINGAPORE\nPort of Discharge: PORT KLANG\nTotal Containers: 4\nGross Wt (kgs): 22,000\n"


def _email(eid="e1", fail_times=0):
    e = {"email_id": eid, "from": "a@b", "subject": "TO CONFIRM DOCS",
         "body": "Attached are the SI and draft BL. Please check the details and confirm.",
         "attachments": [{"name": f"{eid}_SI.txt", "text": SI}, {"name": f"{eid}_BL.txt", "text": BL}]}
    if fail_times:
        e["fail_times"] = fail_times
    return e


def _ctx(store, fn=None):
    return taskmod.TaskContext(store, fn or (lambda e: {"decision": {"category": "BL_COMPARISON", "status": "MISMATCH"}}))


# ---------------------------------------------------------------- retry policy
def test_transient_failure_succeeds_within_three_attempts():
    store = MemoryStore(); ctx = _ctx(store)
    e = _email(fail_times=2); key = idempotency_key(e); p = {"key": key, "email": e}
    assert taskmod.handle(ctx, p, 1) == (False, "RuntimeError: simulated failure 1/2 (fail_times set on the email)")
    assert store.get(REPORTS, key)["status"] == "RETRYING"
    assert taskmod.handle(ctx, p, 2)[0] is False
    done, err = taskmod.handle(ctx, p, 3)
    assert done and err is None
    assert store.get(REPORTS, key)["status"] == "DONE"
    assert store.get(DEAD_LETTER, key) is None


def test_third_failure_goes_to_dead_letter_with_reason_and_input():
    store = MemoryStore(); ctx = _ctx(store)
    e = _email(fail_times=99); key = idempotency_key(e); p = {"key": key, "email": e, "batch_id": "b1"}
    assert taskmod.handle(ctx, p, 1)[0] is False
    assert taskmod.handle(ctx, p, 2)[0] is False
    done, err = taskmod.handle(ctx, p, 3)
    assert done and "simulated failure 3/99" in err
    dl = store.get(DEAD_LETTER, key)
    assert dl["status"] == "FAILED" and dl["attempts"] == 3
    assert dl["reason"] == err and dl["input"] == e and dl["batch_id"] == "b1"
    assert store.get(REPORTS, key)["status"] == "FAILED"


def test_manual_retry_recovers_dead_letter():
    store = MemoryStore(); ctx = _ctx(store)
    e = _email(fail_times=99); key = idempotency_key(e)
    for n in (1, 2, 3):
        taskmod.handle(ctx, {"key": key, "email": e}, n)
    fixed = dict(e); fixed.pop("fail_times")                 # the operator fixed the cause
    done, err = taskmod.handle(ctx, {"key": key, "email": fixed, "retry": True}, 1)
    assert done and err is None
    assert store.get(DEAD_LETTER, key)["status"] == "RECOVERED"
    assert store.get(REPORTS, key)["status"] == "DONE"


def test_chaos_switch_fails_every_attempt():
    store = MemoryStore(); ctx = _ctx(store)
    store.set(SETTINGS, "chaos", {"enabled": True})
    e = _email(); key = idempotency_key(e)
    done, err = taskmod.handle(ctx, {"key": key, "email": e}, 1)
    assert done is False and "chaos" in err
    store.set(SETTINGS, "chaos", {"enabled": False})
    assert taskmod.handle(ctx, {"key": key, "email": e}, 2) == (True, None)


def test_real_processing_failure_is_captured_not_swallowed():
    store = MemoryStore()
    ctx = _ctx(store, fn=lambda e: (_ for _ in ()).throw(ValueError("attachment x: invalid base64")))
    e = _email(); key = idempotency_key(e)
    for n in (1, 2):
        assert taskmod.handle(ctx, {"key": key, "email": e}, n)[0] is False
    done, err = taskmod.handle(ctx, {"key": key, "email": e}, 3)
    assert done and err == "ValueError: attachment x: invalid base64"
    assert store.get(DEAD_LETTER, key)["reason"] == err


# ---------------------------------------------------------------- idempotency
def test_idempotency_key_depends_on_content_not_order():
    a = _email(); b = _email()
    b["attachments"] = list(reversed(b["attachments"]))
    assert idempotency_key(a) == idempotency_key(b)
    c = _email(); c["body"] += " (changed)"
    assert idempotency_key(a) != idempotency_key(c)


# ---------------------------------------------------------------- end to end through the HTTP API (inline tasks)
def test_batch_dead_letter_retry_flow_over_http():
    from fastapi.testclient import TestClient
    import api.main as m
    m.store = MemoryStore()
    client = TestClient(m.app)

    good, bad = _email("ok-1"), _email("bad-1", fail_times=99)
    r = client.post("/batch", json={"emails": [good, bad, good]})
    assert r.status_code == 200
    j = r.json()
    assert j["queued"] == 2 and j["duplicates"] == 1                # the repeated good email is deduplicated
    bid = j["batch_id"]

    deadline = time.time() + 15                                      # inline backoff 1s + 2s for the bad one
    while time.time() < deadline:
        s = client.get(f"/batch/{bid}").json()
        if s["pending"] == 0:
            break
        time.sleep(0.3)
    assert s["done"] == 1 and s["failed"] == 1

    f = client.get("/failures").json()
    assert f["count"] == 1
    item = f["items"][0]
    assert item["status"] == "FAILED" and item["attempts"] == 3 and "simulated failure" in item["reason"]
    key = item["id"]
    assert client.get(f"/failures/{key}").json()["input"]["email_id"] == "bad-1"

    r = client.post(f"/failures/{key}/retry")
    assert r.status_code == 200 and r.json()["status"] == "RETRYING"
    deadline = time.time() + 10
    while time.time() < deadline:
        rep = client.get(f"/report/{key}").json()
        if rep["status"] == "DONE":
            break
        time.sleep(0.3)
    assert rep["status"] == "DONE"
    assert rep["result"]["decision"]["category"] == "BL_COMPARISON"
    assert rep["result"]["decision"]["defect_fields"] == ["container_count"]
    assert client.get("/failures").json()["count"] == 0             # recovered items leave the FAILED list
    assert client.get("/failures?all=1").json()["items"][0]["status"] == "RECOVERED"
    assert client.get("/report/bad-1").json()["status"] == "DONE"    # lookup by email_id works too


def test_tasks_endpoint_rejects_unauthenticated_calls_when_protected(monkeypatch):
    from fastapi.testclient import TestClient
    import api.main as m
    monkeypatch.setattr(m, "API_TOKEN", "secret")
    client = TestClient(m.app)
    r = client.post("/tasks/process", json={"key": "k", "email": _email()})
    assert r.status_code == 401
    r = client.post("/tasks/process", json={"key": "k", "email": _email()}, headers={"X-API-Key": "secret"})
    assert r.status_code == 200 and r.json()["status"] == "done"


# ---------------------------------------------------------------- auth tiers and the anonymous rate limit
def test_process_is_anonymous_and_batch_is_keyed(monkeypatch):
    from fastapi.testclient import TestClient
    import api.main as m
    monkeypatch.setattr(m, "API_TOKEN", "secret")
    m.store = MemoryStore()
    client = TestClient(m.app)
    r = client.post("/process", json=_email("anon-1"))
    assert r.status_code == 200 and r.json()["decision"]["category"] == "BL_COMPARISON"
    assert client.post("/batch", json={"emails": [_email("b-1")]}).status_code == 401
    assert client.post("/batch", json={"emails": [_email("b-1")]}, headers={"X-API-Key": "secret"}).status_code == 200
    assert client.post("/admin/chaos?enabled=false").status_code == 401
    assert client.get("/health").json()["api_key_required_for"] == m.KEYED_ENDPOINTS
    # F7: dead-letter endpoints are keyed; /health leaks no internals
    assert client.get("/failures").status_code == 401
    assert client.get("/failures/xyz").status_code == 401
    assert client.get("/failures", headers={"X-API-Key": "secret"}).status_code == 200
    h = client.get("/health").json()
    assert not any(k in h for k in ("gcp_project", "gemini_model", "llm_stats", "llm_models_used"))


def test_process_rate_limit_per_ip(monkeypatch):
    from fastapi.testclient import TestClient
    import api.main as m
    monkeypatch.setattr(m, "RATE_LIMIT_PER_MIN", 3)
    m._rate_hits.clear()
    client = TestClient(m.app)
    codes = [client.post("/process", json=_email("rl"), headers={"X-Forwarded-For": "203.0.113.9"}).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    r = client.post("/process", json=_email("rl"), headers={"X-Forwarded-For": "203.0.113.9"})
    assert "Retry-After" in r.headers and "per minute" in r.json()["detail"]
    # another client is not affected
    assert client.post("/process", json=_email("rl"), headers={"X-Forwarded-For": "198.51.100.4"}).status_code == 200


# ---------------------------------------------------------------- F8: human review keeps the AI decision and adds an audit trail
def test_review_confirm_and_correct_keep_original(monkeypatch):
    from fastapi.testclient import TestClient
    import api.main as m
    monkeypatch.setattr(m, "API_TOKEN", "secret")
    m.store = MemoryStore()
    client = TestClient(m.app)
    key = client.post("/process", json=_email("rv-1")).json()["key"]
    ai = client.get(f"/report/{key}").json()["result"]["decision"]
    assert ai["status"] == "MISMATCH" and ai["defect_fields"] == ["container_count"]

    assert client.post(f"/report/{key}/review", json={"decision": "confirmed", "reviewer": "ops"}).status_code == 401
    r = client.post(f"/report/{key}/review", json={"decision": "confirmed", "reviewer": "ops", "reviewer_note": "checked"},
                    headers={"X-API-Key": "secret"})
    assert r.status_code == 200 and r.json()["review"]["human_decision"] == "confirmed"
    assert r.json()["effective_decision"] == ai

    r = client.post(f"/report/{key}/review", headers={"X-API-Key": "secret"},
                    json={"decision": "corrected", "reviewer": "ops", "reviewer_note": "BL was later amended",
                          "corrected_fields": {"status": "OK", "has_defect": False, "defect_fields": []}})
    assert r.status_code == 200
    rep = client.get(f"/report/{key}").json()
    assert rep["result"]["decision"] == ai                                   # AI decision untouched
    assert rep["review"]["original_ai_decision"] == ai
    assert rep["review"]["reviewer"] == "ops" and rep["review"]["reviewed_at"] > 0
    assert rep["effective_decision"]["status"] == "OK" and rep["effective_decision"]["defect_fields"] == []
    assert rep["effective_decision"]["category"] == "BL_COMPARISON"          # untouched fields carried over
    # by email_id too, and validation
    assert client.get("/report/rv-1").json()["review"]["human_decision"] == "corrected"
    assert client.post(f"/report/{key}/review", json={"decision": "corrected", "reviewer": "ops"}, headers={"X-API-Key": "secret"}).status_code == 422
    assert client.post(f"/report/{key}/review", json={"decision": "corrected", "reviewer": "ops", "corrected_fields": {"nope": 1}}, headers={"X-API-Key": "secret"}).status_code == 422
    assert client.post("/report/does-not-exist/review", json={"decision": "confirmed", "reviewer": "ops"}, headers={"X-API-Key": "secret"}).status_code == 404


# ---------------------------------------------------------------- console listing: slim rows, email header kept
def test_reports_lists_slim_rows_with_email_header_and_filters():
    from fastapi.testclient import TestClient
    import api.main as m
    m.store = MemoryStore()
    client = TestClient(m.app)
    client.post("/process", json=_email("ls-1"))
    r = client.post("/batch", json={"emails": [_email("ls-2")]})
    assert r.status_code == 200
    deadline = time.time() + 10
    while time.time() < deadline and client.get("/report/ls-2").json()["status"] != "DONE":
        time.sleep(0.2)
    rows = client.get("/reports").json()
    assert rows["count"] == 2
    by_id = {x["email_id"]: x for x in rows["items"]}
    for eid in ("ls-1", "ls-2"):
        row = by_id[eid]
        assert row["subject"] == "TO CONFIRM DOCS" and row["from"] == "a@b" and row["attachments"] == 2
        assert row["category"] == "BL_COMPARISON" and row["status"] == "MISMATCH" and row["defect_fields"] == ["container_count"]
        assert row["report_status"] == "DONE" and row["decided_by"] == "rule"
        assert "evidence" not in row and "body" not in row and "result" not in row
    assert client.get("/reports?category=SPAM").json()["count"] == 0
    assert client.get("/reports?status=MISMATCH").json()["count"] == 2
    assert client.get("/reports?review_reason=missing_attachment").json()["count"] == 0
    # the stored report keeps the email header (incl. body) after the batch task finished
    rep = client.get("/report/ls-2").json()
    assert rep["email"]["subject"] == "TO CONFIRM DOCS" and "Attached are the SI" in rep["email"]["body"]
    assert rep["email"]["attachments"] == ["ls-2_SI.txt", "ls-2_BL.txt"]
