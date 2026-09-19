"""
Main pipeline - read inbox -> classify -> extract -> compare -> write submission.json
Usage:  python pipeline/run.py <data dir> [output file]
"""
from __future__ import annotations
import json, os, sys, pathlib, collections

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from shipdoc_core.compare import compare_documents, Outcome
from shipdoc_core.fields import FIELD_KEYS
from pipeline.parse_doc import parse_text_document
from pipeline.parse_office import office_to_text
from pipeline.classify import classify_email, expects_attachments
from pipeline.classify_llm import classify_with_llm, STATS as LLM_STATS, MODEL_USAGE

TEXT_EXT = {".txt"}
OFFICE_EXT = {".pdf", ".docx", ".xlsx"}
LLM_THRESHOLD = 0.80      # LLM is called only below this rule confidence; all BL_COMPARISON branches are >= 0.80 and never call it


def read_attachment(root: pathlib.Path, rel: str) -> tuple[str | None, str]:
    """Returns (text, source). Non-text returns (None, ext) - handed to the OCR/LLM layer."""
    p = root / rel
    ext = p.suffix.lower()
    if not p.exists():
        return None, "missing"
    if ext in TEXT_EXT:
        try:
            return p.read_text(encoding="utf-8", errors="replace"), "text"
        except Exception:
            return None, "unreadable"
    if ext in OFFICE_EXT:
        txt = office_to_text(p)          # pdf/docx/xlsx -> "Label: Value" text
        return (txt, ext.lstrip(".")) if txt else (None, "unreadable")
    return None, ext.lstrip(".")


def classify_attachments(root, atts):
    """Assign attachments to the SI / BL slots.
    Priority: filename tag (_SI. / _BL.) > content fingerprint (detect_doc_type) > both fail -> left in unassigned
    Returns (slots, unassigned): slots = {"SI": (doc, src) | None, "BL": ...}; unassigned = [(doc, src)]
    doc is None when no text could be read (unreadable / missing)."""
    slots: dict[str, tuple | None] = {"SI": None, "BL": None}
    pending, unassigned = [], []
    for a in atts:
        base = os.path.basename(a).upper()
        text, src = read_attachment(root, a)
        doc = parse_text_document(text) if text is not None else None
        tag = "SI" if "_SI." in base else "BL" if "_BL." in base else None
        if tag and slots[tag] is None:
            slots[tag] = (doc, src)
        else:
            pending.append((doc, src))
    for doc, src in pending:                                   # content-fingerprint fallback
        kind = doc.doc_type if doc is not None else None
        if kind in slots and slots[kind] is None:
            slots[kind] = (doc, src)
        else:
            unassigned.append((doc, src))
    return slots, unassigned


def decide(email, root, *, details: dict | None = None) -> dict:
    """Returns the submission record. Pass details={} to also receive the basis of the decision (API / review UI):
    classification{rule_category, rule_confidence, llm}, attachments{SI,BL}, fields[FieldResult...]"""
    atts = email.get("attachments", []) or []
    slots, unassigned = classify_attachments(root, atts)
    has_si, has_bl = slots["SI"] is not None, slots["BL"] is not None
    si, si_src = slots["SI"] or (None, None)
    bl, bl_src = slots["BL"] or (None, None)
    category, decided_by, conf = classify_email(email, has_si, has_bl)
    if details is not None:
        details["classification"] = {"rule_category": category, "rule_confidence": conf, "llm": None}
        details["attachments"] = {
            k: (None if v is None else {"source": v[1], "doc_type": v[0].doc_type if v[0] else None,
                                        "readable": bool(v[0] and v[0].readable),
                                        "fields": v[0].fields if v[0] else {}})
            for k, v in slots.items()}
        details["fields"] = []

    # Rules first, LLM fallback: called only when the rules are unsure; if the LLM fails the rule result stands
    if conf < LLM_THRESHOLD:
        llm = classify_with_llm(email)
        if llm is not None:
            category, decided_by = llm.category, "llm"
            if details is not None:
                details["classification"]["llm"] = llm.model_dump()

    out = {"category": category, "status": "OK", "review_reason": None,
           "has_defect": False, "defect_fields": [], "decided_by": decided_by}

    if category != "BL_COMPARISON":
        return out

    # --- Reliability gate: decide step by step along the four official review_reason values ---
    if not has_si or not has_bl:
        # Attachments present but neither the filename tag nor the content identifies SI/BL: unreadable if no text, else wrong_doc_type
        if unassigned:
            reason = "unreadable" if any(d is None for d, _ in unassigned) else "wrong_doc_type"
            out.update(status="NEEDS_REVIEW", review_reason=reason)
            return out
        # Two kinds of "no attachment" must be told apart:
        #   incomplete request - the sender believes the files are attached and asks to compare
        #                        ("compare the SI and draft BL") but they never arrived
        #                        -> NEEDS_REVIEW / missing_attachment, someone must chase them
        #   ordinary to-do     - the sender is asking for the files ("please send the draft BL for checking");
        #                        there is nothing to compare yet -> OK, not a failed comparison
        if not expects_attachments(email):
            return out
        out.update(status="NEEDS_REVIEW", review_reason="missing_attachment")
        return out

    if si is None or bl is None:                       # non-text: pdf/docx/xlsx
        src = si_src if si is None else bl_src
        if src in ("missing",):
            out.update(status="NEEDS_REVIEW", review_reason="missing_attachment")
        else:
            # OCR / Vision LLM belongs here. Until wired, report unreadable honestly; never guess.
            out.update(status="NEEDS_REVIEW", review_reason="unreadable")
        return out

    if not si.readable or not bl.readable:
        out.update(status="NEEDS_REVIEW", review_reason="unreadable")
        return out

    if si.doc_type != "SI" or bl.doc_type != "BL":
        out.update(status="NEEDS_REVIEW", review_reason="wrong_doc_type")
        return out

    missing = [k for k in FIELD_KEYS if si.fields.get(k) is None or bl.fields.get(k) is None]
    if missing:
        out.update(status="NEEDS_REVIEW", review_reason="missing_value")
        return out

    # --- Deterministic comparison ---
    rep = compare_documents(email["email_id"], si.fields, bl.fields)
    if details is not None:
        details["fields"] = [r.to_dict() for r in rep.results]
        details["report_text"] = rep.render()
    undetermined = [r.field for r in rep.results if r.outcome is Outcome.UNDETERMINED]
    if undetermined:
        out.update(status="NEEDS_REVIEW", review_reason="missing_value")
        return out

    if rep.mismatched_fields:
        out.update(status="MISMATCH", has_defect=True,
                   defect_fields=sorted(rep.mismatched_fields))
    else:
        out.update(status="OK")
    return out


def main(data_dir: str, out_path: str = "submission.json"):
    root = pathlib.Path(data_dir)
    inbox = sorted((root / "inbox").glob("*.json"))
    submission, stats = {}, collections.Counter()

    for f in inbox:
        email = json.load(open(f, encoding="utf-8"))
        rec = decide(email, root)
        submission[email["email_id"]] = rec
        stats[rec["category"]] += 1
        stats["decided_by:" + rec["decided_by"]] += 1
        if rec["category"] == "BL_COMPARISON":
            stats["status:" + rec["status"]] += 1
            if rec["review_reason"]:
                stats["reason:" + rec["review_reason"]] += 1
            for fld in rec["defect_fields"]:
                stats["field:" + fld] += 1

    for k, v in LLM_STATS.items():
        stats["llm:" + k] = v
    for m, n in MODEL_USAGE.items():
        stats["llm_model:" + m] = n

    json.dump(submission, open(out_path, "w", encoding="utf-8"),
              indent=2, ensure_ascii=False)
    return submission, stats


if __name__ == "__main__":
    sub, st = main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "submission.json")
    print(f"Generated {len(sub)} records\n")
    for k in sorted(st): print(f"  {k:32} {st[k]}")
