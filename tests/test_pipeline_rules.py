"""pipeline 规则层的回归测试：误导性标题、缺附件意图判别。用例均取自真实 inbox 模板。"""
import pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from pipeline.classify import classify_email, expects_attachments, own_text

SIG = "\n\nBest Regards,\nWilly Situmorang\nShipping Documentation\nDID : +971 04 4938240"


def _email(subject, body, **kw):
    return {"email_id": "t", "subject": subject, "body": body, "attachments": [], **kw}


# ---------------------------------------------------------------- 误导性标题
def test_spam_body_with_business_subject_is_not_bl_comparison():
    # email_417 / 455：标题 "confirm your bank details" 曾命中 COMPARE_PAT
    e = _email("Re: Invoice payment - kindly confirm your bank details",
               "Hello Dear, I am a bank officer with an urgent business proposal involving USD 4.5 million. "
               "Please reply with your bank details to proceed.")
    cat, _, conf = classify_email(e, False, False)
    assert cat != "BL_COMPARISON"
    assert conf < 0.8                      # 规则拿不准 → 交 LLM


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


# ---------------------------------------------------------------- 缺附件意图
def test_compare_intent_with_dropped_attachments_expects_them():
    # email_506 / 508 / 510
    e = _email("RE_ AFRT - LONG BEACH_US - 5RSG-19787",
               "Dear Team,\n\nPlease compare the SI and draft BL for 070500263211 and confirm "
               "(attachments appear to have been dropped). Thank you.")
    assert expects_attachments(e) is True


def test_compare_intent_with_bl_missing_expects_it():
    # email_507 / 509（只附了 SI）
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
    # 91 封 "please send the draft BL for checking"：索要文件，不是缺附件
    e = _email("AFEMY - ASHDOD_ISRAEL - 5RAE-20163",
               "Dear Najiha,\n\nPlease assist to send the draft BL for 070500236763 for checking asap.\n\nThank you." + SIG)
    assert expects_attachments(e) is False


def test_request_with_quoted_attachment_claim_still_not_expected():
    # 引用的原邮件里有 "Attached are..."，不能算本邮件的承诺
    e = _email("RE_ x",
               "Dear Ooi,\n\nPlease assist to send the draft BL for MCLSINJEA2576036 for checking asap." + SIG +
               "\n\n______________________________\nFrom: Mitchelle Ting\nAttached are the SI and draft BL.")
    assert expects_attachments(e) is False


def test_ambiguous_body_defaults_to_escalate():
    # 既不索要也不声称附上：拿不准 → 上报（设计不变量）
    assert expects_attachments(_email("x", "Dear Team,\n\nKindly action. Thank you.")) is True
