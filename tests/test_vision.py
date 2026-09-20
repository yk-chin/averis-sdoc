"""Vision proposals never change a decision; they only add evidence for the reviewer. No network in tests."""
import json, os, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
os.environ.pop("LLM_PROVIDER", None)
import pytest
from pipeline import vision
from pipeline.run import decide

SI_TXT = "SHIPPING INSTRUCTION\nShipper: ABC CO LTD\nConsignee: XYZ LLC\nNotify: XYZ LLC\nPOL: SINGAPORE\nPOD: PORT KLANG\nContainer Count: 3\nGross Weight: 22000 KG\n"
PROPOSAL = {"shipper": "ABC CO LTD", "consignee": "XYZ LLC", "notify_party": "XYZ LLC", "port_of_loading": "SINGAPORE",
            "port_of_discharge": "PORT KLANG", "container_count": "4", "gross_weight_kg": "22,000 KG"}


def _email(root, bl_bytes: bytes):
    (root / "attachments").mkdir()
    (root / "attachments" / "e_SI.txt").write_text(SI_TXT, encoding="utf-8")
    (root / "attachments" / "e_BL.pdf").write_bytes(bl_bytes)
    return {"email_id": "e", "from": "a@b", "subject": "TO CONFIRM DOCS",
            "body": "Attached are the SI and draft BL. Please check the details and confirm.",
            "attachments": ["attachments/e_SI.txt", "attachments/e_BL.pdf"]}


def test_corrupt_pdf_is_never_sent_and_stays_unreadable(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION", "on")
    called = []
    monkeypatch.setattr(vision, "_extract_remote", lambda b: called.append(1) or (None, None))
    e = _email(tmp_path, b"%PDF-1.5\n" + bytes(range(256)))
    details = {}
    out = decide(e, tmp_path, details=details)
    assert out["status"] == "NEEDS_REVIEW" and out["review_reason"] == "unreadable"
    assert called == []                                                     # nothing uploaded
    assert details["attachments"]["BL"]["vision"]["status"] == "not_a_pdf"
    assert any("no vision proposal" in d for d in details["review_detail"])


def test_scan_gets_a_capped_proposal_and_a_provisional_comparison(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION", "on")
    monkeypatch.setattr(vision, "is_scanned_pdf", lambda p: True)
    monkeypatch.setattr(vision, "CACHE_PATH", tmp_path / "vision.json")
    monkeypatch.setattr(vision, "_cache", None)
    fake = vision.VisionExtraction(document_type="BL", fields=[
        vision.VisionField(key=k, value=v, confidence=0.95) for k, v in PROPOSAL.items()])
    monkeypatch.setattr(vision, "_extract_remote", lambda b: (fake, "fake-vision-model"))
    e = _email(tmp_path, b"%PDF-1.3 scan")
    details = {}
    out = decide(e, tmp_path, details=details)
    # the decision is unchanged: still a human, still the official reason
    assert out == {"category": "BL_COMPARISON", "status": "NEEDS_REVIEW", "review_reason": "unreadable",
                   "has_defect": False, "defect_fields": [], "decided_by": "rule"}
    v = details["attachments"]["BL"]["vision"]
    assert v["status"] == "proposal" and v["model"] == "fake-vision-model"
    assert all(c <= vision.VISION_MAX_CONF for c in v["confidence"].values())      # 0.95 capped to 0.60
    prov = {f["field"]: f for f in details["provisional"]["fields"]}
    assert prov["container_count"]["outcome"] == "mismatch" and prov["container_count"]["confidence"] <= 0.6
    assert all(f["confidence"] <= 0.6 for f in prov.values())                        # nothing could auto-pass
    assert any("provisional comparison flags: container_count" in d for d in details["review_detail"])
    assert any("please confirm" in d for d in details["review_detail"])
    # second call is served from the cache: the remote is not called again
    monkeypatch.setattr(vision, "_extract_remote", lambda b: pytest.fail("remote called despite cache"))
    decide(e, tmp_path, details={})
    assert json.loads((tmp_path / "vision.json").read_text())


def test_vision_off_switch(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION", "off")
    monkeypatch.setattr(vision, "_extract_remote", lambda b: pytest.fail("must not be called"))
    e = _email(tmp_path, b"%PDF-1.3 scan")
    details = {}
    out = decide(e, tmp_path, details=details)
    assert out["review_reason"] == "unreadable" and details["attachments"]["BL"]["vision"]["status"] == "disabled"


def test_readable_documents_never_trigger_vision(tmp_path, monkeypatch):
    monkeypatch.setattr(vision, "extract_fields", lambda p: pytest.fail("vision must not run on readable docs"))
    (tmp_path / "attachments").mkdir()
    (tmp_path / "attachments" / "e_SI.txt").write_text(SI_TXT, encoding="utf-8")
    (tmp_path / "attachments" / "e_BL.txt").write_text(SI_TXT.replace("SHIPPING INSTRUCTION", "BILL OF LADING (DRAFT)"), encoding="utf-8")
    e = {"email_id": "e", "from": "a@b", "subject": "TO CONFIRM DOCS", "body": "Attached are the SI and draft BL. Please check.",
         "attachments": ["attachments/e_SI.txt", "attachments/e_BL.txt"]}
    assert decide(e, tmp_path)["status"] == "OK"
