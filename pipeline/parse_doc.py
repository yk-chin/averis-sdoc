"""
文档解析器 —— 确定性优先，AI 兜底
=================================
观察：纯文本 SI/BL 是 "Label: Value" 的规则结构，**确定性解析器就能拿下**。
所以我们不为每份文档都掏钱调 LLM，而是：

    确定性解析 → 成功则直接用（decided_by="rule"）
               → 失败/字段缺失 → 才调 LLM/Vision（decided_by="llm"）

对 Averis 这种 300+ 客户的 BPO 来说，单据处理的**单位成本**是生意本身。
一个 90% 走规则、10% 走 LLM 的系统，和一个 100% 调 LLM 的系统，
在规模化时是两门不同的生意。这一点要写进 pitch。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from shipdoc_core.fields import resolve_label, FIELD_KEYS

# 文档类型指纹（出现在首几行）
# ⚠️ 顺序即优先级。真实数据里 SI 的抬头是 "BILL OF LADING INSTRUCTION" / "BL INSTRUCTION"，
#    天真的 BL 正则会把它抢走 —— 这是把 SI 误判成 BL 的根因。SI 必须先匹配。
DOC_SIGNATURES = [
    ("SI",      re.compile(r"(SHIPPING\s+INSTRUCTION|BILL\s+OF\s+LADING\s+INSTRUCTION|"
                           r"\bB/?L\s+INSTRUCTION|SHIPPING\s+INSTRUCTIONS?)", re.I)),
    ("BL",      re.compile(r"BILL\s+OF\s+LADING(?!\s+INSTRUCTION)", re.I)),
    ("INVOICE", re.compile(r"COMMERCIAL\s+INVOICE", re.I)),
    ("PACKING", re.compile(r"PACKING\s+LIST", re.I)),
    ("COO",     re.compile(r"CERTIFICATE\s+OF\s+ORIGIN", re.I)),
]

# 空值占位符：'???'、'____'、'N/A'、'TBA' 等 → missing_value
BLANK = re.compile(r"^[\s_?\-.]*$|^(N\.?/?A\.?|TBA|TBD|PENDING|XXX+)$", re.I)

# ⚠️ 标签里会混排中文："Gross Weight毛重(KGS):" 在真实数据中出现 51 次。
#    如果字符类里不含 CJK，整行会直接失配，该字段被当成"缺失"→ 整封邮件被误判
#    为 NEEDS_REVIEW。这是 62 处字段缺失里 100% 的根因。
#    解法：标签位置放行"除冒号外的任意字符"，CJK 交给 _norm_label 统一剔除。
LABEL_LINE = re.compile(r"^\s*([A-Za-z][^:\n]{0,60}?)\s*:\s*(.*)$")


@dataclass
class ParsedDoc:
    doc_type: str                       # SI | BL | INVOICE | PACKING | COO | UNKNOWN
    fields: dict = field(default_factory=dict)          # {field_key: value}
    blanks: list = field(default_factory=list)          # 命中占位符的字段
    near_miss: list = field(default_factory=list)       # 命中陷阱标签的记录
    readable: bool = True
    note: str = ""


def detect_doc_type(text: str) -> str:
    head = "\n".join(text.splitlines()[:12])
    for name, rx in DOC_SIGNATURES:
        if rx.search(head):
            return name
    hits = [(m.start(), name) for name, rx in DOC_SIGNATURES
            if (m := rx.search(text))]
    return min(hits)[1] if hits else "UNKNOWN"


def parse_text_document(text: str) -> ParsedDoc:
    if text is None or not text.strip():
        return ParsedDoc("UNKNOWN", readable=False, note="empty file")
    # 乱码检测：可打印字符占比过低
    printable = sum(1 for c in text if c.isprintable() or c in "\n\r\t")
    if printable / max(len(text), 1) < 0.85:
        return ParsedDoc("UNKNOWN", readable=False, note="garbled bytes")

    doc = ParsedDoc(detect_doc_type(text))
    lines = text.splitlines()

    for i, line in enumerate(lines):
        m = LABEL_LINE.match(line)
        if not m:
            continue
        raw_label, raw_value = m.group(1).strip(), m.group(2).strip()
        key, is_near = resolve_label(raw_label)
        if key is None:
            continue
        if is_near:
            doc.near_miss.append({"label": raw_label, "would_be": key, "value": raw_value})
            continue                                  # 陷阱标签不采纳

        # 续行：下一行缩进且不含标签 → 属于地址，本比对不采纳（只比法人主体）
        if BLANK.match(raw_value):
            doc.blanks.append(key)
            doc.fields.setdefault(key, None)
            continue
        doc.fields.setdefault(key, raw_value)         # 首次出现优先

    if doc.doc_type in ("SI", "BL") and not doc.fields:
        doc.readable = False
        doc.note = "no parsable fields"
    return doc
