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

# 引用的原邮件 / 外部邮件警告：不属于本邮件的意图，判别前剥掉
QUOTED_PAT = re.compile(r"^(_{5,}|-{3,}\s*Original Message|From:\s)", re.I | re.M)
EXT_WARNING_PAT = re.compile(r"^WARNING: This email originated outside.*?\n\n", re.S)

# 缺附件时的意图判别（见 run.py 的可靠性闸门）
#   发件人认为 SI/BL 已随邮件到达、要求比对/确认 → 真正的 missing_attachment，需要人去催文件
#   发件人只是索要 draft BL / SI                → 手上本来就没有可比对的东西，不是缺附件
DOCS_IN_HAND_PAT = re.compile(
    r"\b(attached|enclosed|please\s+find\s+(?:the\s+)?(?:attached|enclosed))\b|"
    r"\b(compare|check|verify|confirm|review|cross[- ]?check|validate)\b[^.\n]{0,60}"
    r"\b(si\b[^.\n]{0,20}\b(?:draft\s*)?b/?l|draft\s*b/?l|bill\s+of\s+lading|documents?|docs)\b", re.I)
DOCS_REQUESTED_PAT = re.compile(
    r"\b(send|provide|share|forward|resend|issue)\b[^.\n]{0,40}"
    r"\b(draft\s*b/?l|bill\s+of\s+lading|b/?l|si|shipping\s+instruction)\b", re.I)


def own_text(email: dict) -> str:
    """本邮件自己写的正文：去掉外部邮件警告与下方引用的原邮件。正文为空才退回标题。"""
    body = (email.get("body", "") or "").replace("\r", "")
    body = EXT_WARNING_PAT.sub("", body, count=1)
    m = QUOTED_PAT.search(body)
    if m:
        body = body[:m.start()]
    body = body.strip()
    return body if body else (email.get("subject", "") or "")


def expects_attachments(email: dict) -> bool:
    """缺附件时：True = 应当上报 missing_attachment；False = 只是索要文件，状态 OK。
    拿不准一律 True（设计不变量：宁可转人工，绝不猜 OK）。"""
    text = own_text(email)
    if DOCS_REQUESTED_PAT.search(text) and not DOCS_IN_HAND_PAT.search(text):
        return False
    return True


def classify_email(email: dict, has_si: bool, has_bl: bool) -> tuple[str, str, float]:
    """返回 (category, decided_by, confidence)"""
    # ⚠️ 进阶数据含误导性标题（同一正文配无关标题、垃圾邮件配业务标题）——
    #    规则只看本邮件自己的正文；标题交给 LLM 作弱提示
    text = own_text(email)

    if SPAM_PAT.search(text):
        return "SPAM", "rule", 0.95

    # 带 SI/BL 附件 → 几乎必然是比对请求
    if has_si and has_bl:
        return "BL_COMPARISON", "rule", 0.97
    if has_si and not has_bl and COMPARE_PAT.search(text):
        return "BL_COMPARISON", "rule", 0.85

    if COMPARE_PAT.search(text):
        return "BL_COMPARISON", "rule", 0.80      # 附件缺失的比对请求
    # ⚠️ 下面三个分支实测精确率低（INVOICE_PAT 会被 SI 里的 "3 Original invoice"、
    #    RPA 通知里的 "Billing Process" 命中），置信度压到 <0.8 → 交 LLM 复核
    if SI_REQUEST_PAT.search(text):
        return "SI_REQUEST", "rule", 0.70
    if INVOICE_PAT.search(text):
        return "INVOICE_QUERY", "rule", 0.60

    return "GENERAL", "rule", 0.50                 # 低置信 → 交 LLM 复核
