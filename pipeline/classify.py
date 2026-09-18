"""
邮件分类 —— 规则优先，LLM 兜底
==============================
五类：BL_COMPARISON | SI_REQUEST | INVOICE_QUERY | GENERAL | SPAM

规则命中即返回 decided_by="rule"（官方 scoring.py 会统计 rule_pct）；
规则拿不准才交给 LLM。这保证了成本与可解释性。

⚠️ 关键陷阱：附件缺失的比对请求，gold 仍然是 BL_COMPARISON + NEEDS_REVIEW。
   所以不能"没附件就不是比对请求"，必须读正文意图。
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


def classify_email(email: dict, has_si: bool, has_bl: bool) -> tuple[str, str, float]:
    """返回 (category, decided_by, confidence)"""
    subject = email.get("subject", "") or ""
    body = email.get("body", "") or ""
    # ⚠️ 进阶数据含误导性标题 —— 正文权重高于标题
    text = f"{body}\n{subject}"

    if SPAM_PAT.search(text):
        return "SPAM", "rule", 0.95

    # 带 SI/BL 附件 → 几乎必然是比对请求
    if has_si and has_bl:
        return "BL_COMPARISON", "rule", 0.97
    if has_si and not has_bl and COMPARE_PAT.search(text):
        return "BL_COMPARISON", "rule", 0.85

    if COMPARE_PAT.search(text):
        return "BL_COMPARISON", "rule", 0.80      # 附件缺失的比对请求
    if SI_REQUEST_PAT.search(text):
        return "SI_REQUEST", "rule", 0.85
    if INVOICE_PAT.search(text):
        return "INVOICE_QUERY", "rule", 0.80

    return "GENERAL", "rule", 0.50                 # 低置信 → 交 LLM 复核
