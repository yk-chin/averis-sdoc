"""
Email classification - rules first, LLM as fallback
===================================================
Five classes: BL_COMPARISON | SI_REQUEST | INVOICE_QUERY | GENERAL | SPAM

A rule hit returns decided_by="rule" (the official scoring.py reports rule_pct);
only when the rules are unsure does the LLM get the email. That keeps cost and explainability.

Key trap: a comparison request whose attachments are missing is still gold
BL_COMPARISON + NEEDS_REVIEW. So "no attachment => not a comparison request" is wrong;
the body intent must be read.
"""
from __future__ import annotations
import re

SPAM_PAT = re.compile(
    r"\b(unsubscribe|viagra|crypto|bitcoin|lottery|winner|congratulation[s]?\b.*\bwon|"
    r"limited\s+time\s+offer|click\s+here\s+now|earn\s+\$|work\s+from\s+home|"
    r"free\s+gift|casino|forex\s+signal|SEO\s+service|guaranteed\s+ranking)\b", re.I)

COMPARE_PAT = re.compile(
    r"\b(check|verify|compare|review|confirm|cross[- ]?check|validate|vet)\b"
    r"[^.]{0,80}\b(draft\s*b/?l|bill\s+of\s+lading|b/?l|si\s+(?:vs|and|&)|documents?|details?)\b|"
    r"\b(draft\s*b/?l|bl\s+draft)\b[^.]{0,40}\b(attach|enclos|review|check|confirm)", re.I)

SI_REQUEST_PAT = re.compile(
    r"\b(prepare|issue|create|raise|draft|send\s+us|need|require|request)\b"
    r"[^.]{0,60}\b(shipping\s+instruction|s/?i\b|new\s+si)\b|"
    r"\bsi\s+(?:request|preparation)\b", re.I)

INVOICE_PAT = re.compile(
    r"\b(invoice|billing|freight\s+charge|debit\s+note|credit\s+note|payment|"
    r"outstanding|remittance|overcharge|surcharge|tariff)\b", re.I)

# Quoted original mail / external-sender warning: not this email's intent, strip before classifying
QUOTED_PAT = re.compile(r"^(_{5,}|-{3,}\s*Original Message|From:\s)", re.I | re.M)
EXT_WARNING_PAT = re.compile(r"^WARNING: This email originated outside.*?\n\n", re.S)

# Intent check when attachments are missing (see the reliability gate in run.py)
#   sender believes SI/BL arrived with the mail and asks to compare/confirm -> a real missing_attachment, someone must chase the files
#   sender merely asks for the draft BL / SI                                -> nothing to compare yet, not a missing attachment
DOCS_IN_HAND_PAT = re.compile(
    r"\b(attached|enclosed|please\s+find\s+(?:the\s+)?(?:attached|enclosed))\b|"
    r"\b(compare|check|verify|confirm|review|cross[- ]?check|validate)\b[^.\n]{0,60}"
    r"\b(si\b[^.\n]{0,20}\b(?:draft\s*)?b/?l|draft\s*b/?l|bill\s+of\s+lading|documents?|docs)\b", re.I)
DOCS_REQUESTED_PAT = re.compile(
    r"\b(send|provide|share|forward|resend|issue)\b[^.\n]{0,40}"
    r"\b(draft\s*b/?l|bill\s+of\s+lading|b/?l|si|shipping\s+instruction)\b", re.I)


def own_text(email: dict) -> str:
    """The text this email itself wrote: strip the external-sender warning and the quoted mail below. Fall back to the subject only when the body is empty."""
    body = (email.get("body", "") or "").replace("\r", "")
    body = EXT_WARNING_PAT.sub("", body, count=1)
    m = QUOTED_PAT.search(body)
    if m:
        body = body[:m.start()]
    body = body.strip()
    return body if body else (email.get("subject", "") or "")


def expects_attachments(email: dict) -> bool:
    """When attachments are missing: True = escalate as missing_attachment; False = just a request for files, status OK.
    Unsure -> True (design invariant: rather a human than a guessed OK)."""
    text = own_text(email)
    if DOCS_REQUESTED_PAT.search(text) and not DOCS_IN_HAND_PAT.search(text):
        return False
    return True


LLM_THRESHOLD = 0.80   # rule results below this confidence go to the LLM; the BL_COMPARISON branches return >= 0.80 and are final


def classify_email(email: dict, has_si: bool, has_bl: bool) -> tuple[str, str, float]:
    """Returns (category, decided_by, confidence)"""
    # The advanced data has misleading subjects (same body under unrelated subjects, spam under business subjects) -
    # the rules read only this email's own body; the subject is left to the LLM as a weak hint
    text = own_text(email)

    if SPAM_PAT.search(text):
        return "SPAM", "rule", 0.95

    # SI and BL attached -> almost certainly a comparison request
    if has_si and has_bl:
        return "BL_COMPARISON", "rule", 0.97
    if has_si and not has_bl and COMPARE_PAT.search(text):
        return "BL_COMPARISON", "rule", 0.85

    if COMPARE_PAT.search(text):
        # Comparison request without attachments. Returns exactly LLM_THRESHOLD, and run.py tests
        # `conf < LLM_THRESHOLD`, so this branch is DELIBERATELY FINAL and never goes to the LLM:
        # on v2 it covers 96 emails at precision/recall 1.0; an LLM round-trip would add cost and a
        # failure surface for no gain. Lower this value below LLM_THRESHOLD if that decision changes.
        return "BL_COMPARISON", "rule", LLM_THRESHOLD
    # The three branches below measured low precision (INVOICE_PAT is hit by "3 Original invoice" inside an SI
    # and by "Billing Process" in RPA notices); confidence is kept < 0.8 so the LLM reviews them
    if SI_REQUEST_PAT.search(text):
        return "SI_REQUEST", "rule", 0.70
    if INVOICE_PAT.search(text):
        return "INVOICE_QUERY", "rule", 0.60

    return "GENERAL", "rule", 0.50                 # low confidence -> LLM review
