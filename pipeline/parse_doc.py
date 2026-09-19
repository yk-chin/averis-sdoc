"""
Document parser - deterministic
===============================
SI/BL documents are a regular "Label: Value" structure (plain text, or pdf/docx/xlsx normalised to
that shape by parse_office.py). A deterministic parser handles them outright, so no LLM call is spent
per document: every field carries an extraction confidence (how the label matched, where the value
came from), and anything the parser cannot read is escalated to a person as `unreadable` /
`missing_value` rather than guessed.

For a BPO like Averis with 300+ clients, the **unit cost** of document handling is the business:
zero LLM calls on the document path is a deliberate design choice, not a gap.
(An LLM / vision extraction fallback for documents the parser cannot read is on the roadmap in
README.md; it is not implemented.)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from shipdoc_core.fields import resolve_label_conf

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
    fields: dict = field(default_factory=dict)          # {field_key: value | None}; None = label present, value blank
    confidence: dict = field(default_factory=dict)      # {field_key: 0-1}; how sure the extraction of that value is
    readable: bool = True


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
        return ParsedDoc("UNKNOWN", readable=False)
    # garbage detection: too few printable characters
    printable = sum(1 for c in text if c.isprintable() or c in "\n\r\t")
    if printable / max(len(text), 1) < 0.85:
        return ParsedDoc("UNKNOWN", readable=False)

    doc = ParsedDoc(detect_doc_type(text))
    lines = text.splitlines()

    # Record model: a label line opens a record; its value is the text after the colon, or - when that
    # is empty - the next line, provided the next line is not itself a label line. Address continuation
    # lines after the value are not part of the field (only the legal entity is compared).
    for i, line in enumerate(lines):
        m = LABEL_LINE.match(line)
        if not m:
            continue
        raw_label, raw_value = m.group(1).strip(), m.group(2).strip()
        key, is_near, conf = resolve_label_conf(raw_label)   # conf: how the label matched (exact / stripped / fuzzy)
        if key is None or is_near:                    # unknown label, or a trap label (not adopted)
            continue
        if not raw_value and i + 1 < len(lines):
            nxt = lines[i + 1].strip()
            if nxt and not LABEL_LINE.match(nxt):
                raw_value, conf = nxt, min(conf, 0.9)     # value taken from the next line
        fixed = _deinterleave(raw_value)
        if fixed != raw_value:
            raw_value, conf = fixed, min(conf, 0.7)       # value recovered from an extraction artefact
        if key not in doc.fields:                     # first occurrence wins
            doc.fields[key] = None if BLANK.match(raw_value) else raw_value
            doc.confidence[key] = conf

    if doc.doc_type in ("SI", "BL") and not doc.fields:
        doc.readable = False
    return doc
