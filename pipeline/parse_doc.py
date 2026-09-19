"""
Document parser - deterministic first, AI as fallback
=====================================================
Observation: plain-text SI/BL files are a regular "Label: Value" structure that a
**deterministic parser handles outright**. So we do not pay for an LLM call per document:

    deterministic parse -> success: use it directly (decided_by="rule")
                        -> failure / missing fields: only then call LLM/Vision (decided_by="llm")

For a BPO like Averis with 300+ clients, the **unit cost** of document handling is the business.
A system that is 90 % rules / 10 % LLM and one that is 100 % LLM are two different businesses
at scale. This belongs in the pitch.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from shipdoc_core.fields import resolve_label, FIELD_KEYS

# Document-type fingerprints (found in the first few lines)
# Order is priority. In real data an SI is headed "BILL OF LADING INSTRUCTION" / "BL INSTRUCTION",
# which a naive BL regex would steal - the root cause of SI being misread as BL. SI must match first.
DOC_SIGNATURES = [
    ("SI",      re.compile(r"(SHIPPING\s+INSTRUCTION|BILL\s+OF\s+LADING\s+INSTRUCTION|"
                           r"\bB/?L\s+INSTRUCTION|SHIPPING\s+INSTRUCTIONS?)", re.I)),
    ("BL",      re.compile(r"BILL\s+OF\s+LADING(?!\s+INSTRUCTION)", re.I)),
    ("INVOICE", re.compile(r"COMMERCIAL\s+INVOICE", re.I)),
    ("PACKING", re.compile(r"PACKING\s+LIST", re.I)),
    ("COO",     re.compile(r"CERTIFICATE\s+OF\s+ORIGIN", re.I)),
]

# Blank placeholders: '???', '____', 'N/A', 'TBA' etc. -> missing_value
BLANK = re.compile(r"^[\s_?\-.]*$|^(N\.?/?A\.?|TBA|TBD|PENDING|XXX+)$", re.I)

# Labels mix in Chinese: "Gross Weight毛重(KGS):" occurs 51 times in the real data.
# If the character class excluded CJK the whole line would fail to match, the field would count as
# "missing" and the entire email would be misjudged NEEDS_REVIEW - the root cause of 100 % of the
# 62 missing-field cases. Fix: allow "anything but a colon" in the label and let _norm_label strip CJK.
LABEL_LINE = re.compile(r"^\s*([A-Za-z][^:\n]{0,60}?)\s*:\s*(.*)$")

# PDF text-extraction interleave artefact (seen 3 times in the dataset):
#   "Notify Party/Intermediate Consignee: CERIEX" is extracted as
#   "Notify: Party/Intermediate ConsCigEnReIEeX" - the label tail "ignee" interleaved with the value "CERIEX".
#   Rule: document values are all upper-case, the interleaved label fragments are lower-case ->
#   removing the lower-case letters recovers the true value.
INTERLEAVED_LABEL = re.compile(r"^Party/Intermediate\s+Cons(?P<rest>\S.*)$")


def _deinterleave(value: str) -> str:
    m = INTERLEAVED_LABEL.match(value)
    if not m:
        return value
    rest = m.group("rest")
    if not re.search(r"[a-z]", rest) or not re.search(r"[A-Z]", rest):
        return value
    return re.sub(r"\s+", " ", re.sub(r"[a-z]", "", rest)).strip()


@dataclass
class ParsedDoc:
    doc_type: str                       # SI | BL | INVOICE | PACKING | COO | UNKNOWN
    fields: dict = field(default_factory=dict)          # {field_key: value}
    blanks: list = field(default_factory=list)          # fields that hit a blank placeholder
    near_miss: list = field(default_factory=list)       # records that hit a trap label
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
    # garbage detection: too few printable characters
    printable = sum(1 for c in text if c.isprintable() or c in "\n\r\t")
    if printable / max(len(text), 1) < 0.85:
        return ParsedDoc("UNKNOWN", readable=False, note="garbled bytes")

    doc = ParsedDoc(detect_doc_type(text))
    lines = text.splitlines()

    for i, line in enumerate(lines):
        m = LABEL_LINE.match(line)
        if not m:
            continue
        raw_label, raw_value = m.group(1).strip(), _deinterleave(m.group(2).strip())
        key, is_near = resolve_label(raw_label)
        if key is None:
            continue
        if is_near:
            doc.near_miss.append({"label": raw_label, "would_be": key, "value": raw_value})
            continue                                  # trap labels are not adopted

        # continuation: an indented next line without a label is address text, not used here (entity only)
        if BLANK.match(raw_value):
            doc.blanks.append(key)
            doc.fields.setdefault(key, None)
            continue
        doc.fields.setdefault(key, raw_value)         # first occurrence wins

    if doc.doc_type in ("SI", "BL") and not doc.fields:
        doc.readable = False
        doc.note = "no parsable fields"
    return doc
