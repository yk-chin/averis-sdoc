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
