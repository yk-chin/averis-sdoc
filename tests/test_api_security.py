"""Public-surface guarantees of the API in demo mode (docs/SECURITY.md)."""
import datetime
import os, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
os.environ["STORE"] = "memory"; os.environ["TASKS_MODE"] = "inline"
os.environ.pop("LLM_PROVIDER", None)

from api.store import MemoryStore, REPORTS, idempotency_key

SI = "SHIPPING INSTRUCTION\nShipper: ABC CO LTD\nConsignee: XYZ LLC\nNotify: XYZ LLC\nPOL: SINGAPORE\nPOD: PORT KLANG\nContainer Count: 3\nGross Weight: 22000 KG\n"
BL = SI.replace("SHIPPING INSTRUCTION", "BILL OF LADING (DRAFT)").replace("Container Count: 3", "Total Containers: 4")


def _email(eid):
    return {"email_id": eid, "from": "a@b", "subject": "TO CONFIRM DOCS",
            "body": "Attached are the SI and draft BL. Please check the details and confirm.",
            "attachments": [{"name": f"{eid}_SI.txt", "text": SI}, {"name": f"{eid}_BL.txt", "text": BL}]}


def _client(monkeypatch, token="secret"):
    from fastapi.testclient import TestClient
    import api.main as m
    monkeypatch.setattr(m, "API_TOKEN", token)
    m.store = MemoryStore()
    return m, TestClient(m.app)


def test_reserved_prefix_is_rejected_for_anonymous_callers(monkeypatch):
    m, c = _client(monkeypatch)
    r = c.post("/process", json=_email("email_001"))
    assert r.status_code == 422 and "reserved" in r.json()["detail"]
    assert c.post("/process", json=_email("demo-001")).status_code == 200
    # the team can still refresh a dataset email with the API key; that write carries no expiry
    r = c.post("/process", json=_email("email_001"), headers={"X-API-Key": "secret"})
    assert r.status_code == 200
    assert "expire_at" not in m.store.get(REPORTS, idempotency_key(_email("email_001")))
    # and /reports?prefix=email_ shows only what was written with the key (never an anonymous shadow)
    ids = [x["email_id"] for x in c.get("/reports?prefix=email_").json()["items"]]
    assert ids == ["email_001"]


def test_public_report_masks_reviewer_identity(monkeypatch):
    m, c = _client(monkeypatch)
    key = c.post("/process", json=_email("demo-2")).json()["key"]
    doc = m.store.get(REPORTS, key)
    doc["review"] = {"original_ai_decision": doc["result"]["decision"], "human_decision": "confirmed", "corrections": {},
                     "reviewer_note": "ok", "reviewer": "alice@averis.com", "reviewed_at": 1.0,
                     "identity": {"method": "oidc", "verified": True, "email": "alice@averis.com"}, "request_id": "req-1"}
    m.store.set(REPORTS, key, doc)
    pub = c.get(f"/report/{key}").json()
    assert pub["review"]["reviewer"] == "a***@averis.com"
    assert pub["review"]["identity"] == {"method": "oidc", "verified": True, "email": "a***@averis.com"}
    assert "request_id" not in pub["review"] and "request_id" not in pub and "traceback" not in pub
    assert pub["effective_decision"] == doc["result"]["decision"]
    # a plain name is left alone; the stored document is untouched
    doc["review"]["reviewer"] = "Ops reviewer"; m.store.set(REPORTS, key, doc)
    assert c.get(f"/report/{key}").json()["review"]["reviewer"] == "Ops reviewer"
    assert m.store.get(REPORTS, key)["review"]["identity"]["email"] == "alice@averis.com"


def test_anonymous_uploads_expire_batch_documents_do_not(monkeypatch):
    m, c = _client(monkeypatch)
    c.post("/process", json=_email("demo-3"))
    anon = m.store.get(REPORTS, idempotency_key(_email("demo-3")))
    assert isinstance(anon["expire_at"], datetime.datetime)
    assert datetime.timedelta(hours=23) < anon["expire_at"] - datetime.datetime.now(datetime.timezone.utc) <= datetime.timedelta(hours=24)
    c.post("/batch", json={"emails": [_email("batch-1")]}, headers={"X-API-Key": "secret"})
    assert "expire_at" not in m.store.get(REPORTS, idempotency_key(_email("batch-1")))
