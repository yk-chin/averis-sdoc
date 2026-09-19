"""Regressions for the pipeline rule layer: misleading subjects, missing-attachment intent. All cases come from real inbox templates."""
import pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from pipeline.classify import classify_email, expects_attachments, own_text

SIG = "\n\nBest Regards,\nWilly Situmorang\nShipping Documentation\nDID : +971 04 4938240"


def _email(subject, body, **kw):
    return {"email_id": "t", "subject": subject, "body": body, "attachments": [], **kw}


# ---------------------------------------------------------------- misleading subjects
def test_spam_body_with_business_subject_is_not_bl_comparison():
    # email_417 / 455: the subject "confirm your bank details" used to hit COMPARE_PAT
    e = _email("Re: Invoice payment - kindly confirm your bank details",
               "Hello Dear, I am a bank officer with an urgent business proposal involving USD 4.5 million. "
               "Please reply with your bank details to proceed.")
    cat, _, conf = classify_email(e, False, False)
    assert cat != "BL_COMPARISON"
    assert conf < 0.8                      # rules unsure -> LLM


def test_rpa_body_with_hr_subject_is_low_confidence():
    e = _email("_Approval Required_ Time Off Request",
               "This is an automated notification. The India HSS SD Billing Process for MARCOPOLO 810 "
               "V.BS005 has completed successfully. No action required.\n\n-- RPA Bot")
    cat, _, conf = classify_email(e, False, False)
    assert conf < 0.8


def test_compare_request_in_body_still_routes_to_bl_comparison():
    e = _email("AFEMY - ASHDOD_ISRAEL - EVER(EGLV332003791769) - 5RAE-20163",
               "Dear Najiha,\n\nPlease assist to send the draft BL for 070500236763 for checking asap." + SIG)
    cat, _, conf = classify_email(e, False, False)
    assert (cat, conf) == ("BL_COMPARISON", 0.80)


# ---------------------------------------------------------------- own_text
def test_own_text_strips_external_warning_and_quoted_mail():
    e = _email("x", "WARNING: This email originated outside of our organisation. As a security measure, "
                    "please exercise caution.\n\nHi,\n\nPlease assist to send the draft BL." + SIG +
                    "\n\n______________________________\nFrom: A <a@x>\nAttached are the SI and draft BL.")
    t = own_text(e)
    assert t.startswith("Hi,")
    assert "Attached are the SI" not in t


def test_own_text_falls_back_to_subject_when_body_empty():
    assert own_text(_email("TO CONFIRM DOCS _ 5RSG-51522", "")) == "TO CONFIRM DOCS _ 5RSG-51522"


# ---------------------------------------------------------------- missing-attachment intent
def test_compare_intent_with_dropped_attachments_expects_them():
    # email_506 / 508 / 510
    e = _email("RE_ AFRT - LONG BEACH_US - 5RSG-19787",
               "Dear Team,\n\nPlease compare the SI and draft BL for 070500263211 and confirm "
               "(attachments appear to have been dropped). Thank you.")
    assert expects_attachments(e) is True


def test_compare_intent_with_bl_missing_expects_it():
    # email_507 / 509 (only the SI attached)
    e = _email("RE_ TO CONFIRM DOCS _ 5AKR-00230",
               "Dear Team,\n\nPlease compare the SI and draft BL for I756178688 and confirm "
               "(the draft BL is still missing). Thank you.",
               attachments=["attachments/email_507_SI.txt"])
    assert expects_attachments(e) is True


def test_attached_claim_expects_attachments():
    e = _email("TO CONFIRM DOCS _ 5RSG-51522",
               "Hi Elisa,\n\nAttached are the SI and draft BL for OC 5RSG-51522. Please check the details and confirm." + SIG)
    assert expects_attachments(e) is True


def test_request_for_draft_bl_does_not_expect_attachments():
    # 91 emails "please send the draft BL for checking": a request for files, not a missing attachment
    e = _email("AFEMY - ASHDOD_ISRAEL - 5RAE-20163",
               "Dear Najiha,\n\nPlease assist to send the draft BL for 070500236763 for checking asap.\n\nThank you." + SIG)
    assert expects_attachments(e) is False


def test_request_with_quoted_attachment_claim_still_not_expected():
    # "Attached are..." sits in the quoted original mail and does not count as this email's claim
    e = _email("RE_ x",
               "Dear Ooi,\n\nPlease assist to send the draft BL for MCLSINJEA2576036 for checking asap." + SIG +
               "\n\n______________________________\nFrom: Mitchelle Ting\nAttached are the SI and draft BL.")
    assert expects_attachments(e) is False


def test_ambiguous_body_defaults_to_escalate():
    # neither a request nor a claim of attachment: unsure -> escalate (design invariant)
    assert expects_attachments(_email("x", "Dear Team,\n\nKindly action. Thank you.")) is True


# ---------------------------------------------------------------- R2: attachment slots (filename > content fingerprint > unassigned)
from pipeline.run import classify_attachments

SI_TXT = "SHIPPING INSTRUCTION\n====\nShipper: ABC CO LTD\nConsignee: XYZ\nPOL: SINGAPORE\n"
BL_TXT = "BILL OF LADING (DRAFT)\n====\nShipper: ABC CO LTD\nConsignee: XYZ\nPort of Loading: SINGAPORE\n"


def _write(tmp_path, name, text):
    (tmp_path / "attachments").mkdir(exist_ok=True)
    (tmp_path / "attachments" / name).write_text(text, encoding="utf-8")
    return f"attachments/{name}"


def test_untagged_attachments_are_assigned_by_content(tmp_path):
    a = _write(tmp_path, "email_1_doc_a.txt", SI_TXT)
    b = _write(tmp_path, "email_1_doc_b.txt", BL_TXT)
    slots, unassigned = classify_attachments(tmp_path, [b, a])       # order does not matter
    assert slots["SI"][0].doc_type == "SI" and slots["BL"][0].doc_type == "BL"
    assert unassigned == []


def test_filename_tag_wins_over_content(tmp_path):
    # filename says SI, content is BL: goes into the SI slot by name; the downstream wrong_doc_type gate reports it
    a = _write(tmp_path, "email_2_SI.txt", BL_TXT)
    b = _write(tmp_path, "email_2_BL.txt", BL_TXT)
    slots, unassigned = classify_attachments(tmp_path, [a, b])
    assert slots["SI"][0].doc_type == "BL" and unassigned == []


def test_unidentifiable_attachment_stays_unassigned(tmp_path):
    a = _write(tmp_path, "email_3_doc_a.txt", SI_TXT)
    c = _write(tmp_path, "email_3_doc_c.txt", "PACKING LIST\nItem: paper\n")
    slots, unassigned = classify_attachments(tmp_path, [a, c])
    assert slots["SI"] is not None and slots["BL"] is None
    assert len(unassigned) == 1 and unassigned[0][0].doc_type == "PACKING"


# ---------------------------------------------------------------- R4: bare LOCODE <-> port name
def test_bare_locode_matches_port_name():
    from shipdoc_core.normalize import normalize_port, ports_match
    assert ports_match(normalize_port("MOMBASA, KENYA (KEMBA)"), normalize_port("KEMBA"))[0] is True
    assert ports_match(normalize_port("FREMANTLE, AUSTRALIA"), normalize_port("AUFRE"))[0] is True
    assert ports_match(normalize_port("BUSAN, SOUTH KOREA"), normalize_port("AUFRE"))[0] is False


# ---------------------------------------------------------------- routing layer: extraction confidence -> comparator -> status
from shipdoc_core.fields import resolve_label_conf
from pipeline.parse_doc import parse_text_document
from pipeline.run import decide


def test_label_match_confidence_reflects_how_the_label_matched():
    assert resolve_label_conf("Port of Loading") == ("port_of_loading", False, 1.0)      # exact alias
    assert resolve_label_conf("TOTAL Gross Wt (kgs)")[2] == 0.95                          # qualifier stripped
    key, near, conf = resolve_label_conf("TOTAL Gross Weightnn(KGS)")                   # fuzzy fallback
    assert key == "gross_weight_kg" and not near and 0.86 <= conf < 1.0
    assert resolve_label_conf("Vessel Name") == (None, False, 0.0)


def test_parsed_doc_carries_per_field_confidence():
    doc = parse_text_document("SHIPPING INSTRUCTION\nShipper: ABC\nConsignee:\nXYZ LLC\n"
                              "Notify: Party/Intermediate ConsCigEnReIEeX\nTOTAL Gross Weightnn(KGS): 100 KG\n")
    assert doc.confidence["shipper"] == 1.0
    assert doc.confidence["consignee"] == 0.9                       # value came from the next line
    assert doc.confidence["notify_party"] == 0.7                    # recovered from the interleave artefact
    assert 0.86 <= doc.confidence["gross_weight_kg"] < 1.0          # fuzzy label


def test_low_extraction_confidence_routes_to_human(tmp_path):
    si = "SHIPPING INSTRUCTION\nShipper: ABC CO LTD\nConsignee: XYZ LLC\nNotify: XYZ LLC\nPOL: SINGAPORE\nPOD: PORT KLANG\nContainer Count: 3\nGross Weight: 22000 KG\n"
    bl = si.replace("SHIPPING INSTRUCTION", "BILL OF LADING (DRAFT)")
    (tmp_path / "attachments").mkdir()
    (tmp_path / "attachments" / "e_SI.txt").write_text(si, encoding="utf-8")
    (tmp_path / "attachments" / "e_BL.txt").write_text(bl, encoding="utf-8")
    email = {"email_id": "e", "subject": "TO CONFIRM DOCS", "body": "Attached are the SI and draft BL. Please check the details and confirm.",
             "attachments": ["attachments/e_SI.txt", "attachments/e_BL.txt"]}
    assert decide(email, tmp_path)["status"] == "OK"                # clean documents: OK
    from pipeline import run as runmod
    orig = runmod.parse_text_document
    def low_conf(text):                                             # simulate an OCR/LLM extraction that is unsure
        d = orig(text); d.confidence["shipper"] = 0.3; return d
    runmod.parse_text_document = low_conf
    try:
        out = decide(email, tmp_path)
    finally:
        runmod.parse_text_document = orig
    assert out["status"] == "NEEDS_REVIEW" and out["review_reason"] == "missing_value"


# ---------------------------------------------------------------- F1: review_reason stays official, review_detail tells the truth
def test_review_detail_carries_the_true_cause(tmp_path):
    si = "SHIPPING INSTRUCTION\nShipper: GLOBAL PAPER TRADING CO LTD\nConsignee: XYZ LLC\nNotify: XYZ LLC\nPOL: SINGAPORE\nPOD: PORT KLANG\nContainer Count: 3\nGross Weight: 22000 KG\n"
    bl = si.replace("SHIPPING INSTRUCTION", "BILL OF LADING (DRAFT)").replace("GLOBAL PAPER TRADING CO LTD", "GLOBAL PAPER TRADERS CO LTD")  # similarity 0.889: grey zone
    (tmp_path / "attachments").mkdir()
    (tmp_path / "attachments" / "g_SI.txt").write_text(si, encoding="utf-8")
    (tmp_path / "attachments" / "g_BL.txt").write_text(bl, encoding="utf-8")
    email = {"email_id": "g", "subject": "TO CONFIRM DOCS", "body": "Attached are the SI and draft BL. Please check the details and confirm.",
             "attachments": ["attachments/g_SI.txt", "attachments/g_BL.txt"]}
    details = {}
    out = decide(email, tmp_path, details=details)
    assert out["status"] == "NEEDS_REVIEW" and out["review_reason"] == "missing_value"   # official enum only
    assert any("grey zone" in d for d in details["review_detail"])                           # the real cause
    # a missing attachment carries its own detail
    details = {}
    out = decide({"email_id": "m", "subject": "x", "body": "Please compare the SI and draft BL and confirm.", "attachments": []}, tmp_path, details=details)
    assert out["review_reason"] == "missing_attachment" and "neither SI nor BL" in details["review_detail"][0]


# ---------------------------------------------------------------- F2: the LLM cache key covers everything the prompt contains
def test_llm_cache_key_includes_attachment_names():
    from pipeline.classify_llm import _cache_key
    base = {"subject": "s", "body": "b", "attachments": []}
    with_si = dict(base, attachments=["attachments/e_SI.txt"])
    assert _cache_key(base) != _cache_key(with_si)                      # attachment names are part of the prompt
    assert _cache_key(dict(base, attachments=["x/b_BL.txt", "x/a_SI.txt"])) == _cache_key(dict(base, attachments=["x/a_SI.txt", "x/b_BL.txt"]))  # order-insensitive


# ---------------------------------------------------------------- F4: the no-attachment comparison branch is final by design
def test_no_attachment_comparison_branch_never_calls_the_llm(monkeypatch, tmp_path):
    from pipeline import classify as c, run as runmod
    e = _email("x", "Dear Najiha,\n\nPlease assist to send the draft BL for 070500236763 for checking asap." + SIG)
    cat, _, conf = c.classify_email(e, False, False)
    assert cat == "BL_COMPARISON" and conf == c.LLM_THRESHOLD           # exactly the threshold ...
    def boom(_):
        raise AssertionError("LLM must not be called for this branch")
    monkeypatch.setattr(runmod, "classify_with_llm", boom)
    out = decide(dict(e, email_id="f4"), tmp_path)                       # ... and `<` keeps it out of the LLM
    assert out["category"] == "BL_COMPARISON" and out["decided_by"] == "rule"
