"""
主流水线 —— 读 inbox → 分类 → 抽取 → 比对 → 生成 submission.json
用法：  python pipeline/run.py <数据目录> [输出文件]
"""
from __future__ import annotations
import json, os, sys, pathlib, collections

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from shipdoc_core.compare import compare_documents, Outcome
from shipdoc_core.fields import FIELD_KEYS
from pipeline.parse_doc import parse_text_document, ParsedDoc
from pipeline.parse_office import office_to_text
from pipeline.classify import classify_email

TEXT_EXT = {".txt"}
OFFICE_EXT = {".pdf", ".docx", ".xlsx"}


def read_attachment(root: pathlib.Path, rel: str) -> tuple[str | None, str]:
    """返回 (text, source)。非纯文本返回 (None, ext) —— 交给 OCR/LLM 层。"""
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
        txt = office_to_text(p)          # pdf/docx/xlsx → "Label: Value" 文本
        return (txt, ext.lstrip(".")) if txt else (None, "unreadable")
    return None, ext.lstrip(".")


def classify_attachments(root, atts):
    si = bl = None
    si_src = bl_src = None
    for a in atts:
        base = os.path.basename(a).upper()
        text, src = read_attachment(root, a)
        if "_SI." in base:
            si, si_src = (parse_text_document(text) if text is not None else None), src
        elif "_BL." in base:
            bl, bl_src = (parse_text_document(text) if text is not None else None), src
    return si, si_src, bl, bl_src


def decide(email, root) -> dict:
    atts = email.get("attachments", []) or []
    si, si_src, bl, bl_src = classify_attachments(root, atts)

    has_si = any("_SI." in os.path.basename(a).upper() for a in atts)
    has_bl = any("_BL." in os.path.basename(a).upper() for a in atts)
    category, decided_by, conf = classify_email(email, has_si, has_bl)

    out = {"category": category, "status": "OK", "review_reason": None,
           "has_defect": False, "defect_fields": [], "decided_by": decided_by}

    if category != "BL_COMPARISON":
        return out

    # --- 可靠性闸门：按 official review_reason 的四种原因逐级判定 ---
    if not has_si or not has_bl:
        out.update(status="NEEDS_REVIEW", review_reason="missing_attachment")
        return out

    if si is None or bl is None:                       # 非纯文本：pdf/docx/xlsx
        src = si_src if si is None else bl_src
        if src in ("missing",):
            out.update(status="NEEDS_REVIEW", review_reason="missing_attachment")
        else:
            # 这里应接 OCR / Vision LLM。未接通时诚实上报 unreadable，不许猜。
            out.update(status="NEEDS_REVIEW", review_reason="unreadable",
                       decided_by="rule")
        return out

    if not si.readable or not bl.readable:
        out.update(status="NEEDS_REVIEW", review_reason="unreadable")
        return out

    if si.doc_type != "SI" or bl.doc_type != "BL":
        out.update(status="NEEDS_REVIEW", review_reason="wrong_doc_type")
        return out

    missing = [k for k in FIELD_KEYS
               if k in si.blanks or k in bl.blanks
               or si.fields.get(k) is None or bl.fields.get(k) is None]
    if missing:
        out.update(status="NEEDS_REVIEW", review_reason="missing_value")
        return out

    # --- 确定性比对 ---
    rep = compare_documents(email["email_id"], si.fields, bl.fields)
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
        if rec["category"] == "BL_COMPARISON":
            stats["status:" + rec["status"]] += 1
            if rec["review_reason"]:
                stats["reason:" + rec["review_reason"]] += 1
            for fld in rec["defect_fields"]:
                stats["field:" + fld] += 1

    json.dump(submission, open(out_path, "w", encoding="utf-8"),
              indent=2, ensure_ascii=False)
    return submission, stats


if __name__ == "__main__":
    sub, st = main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "submission.json")
    print(f"生成 {len(sub)} 条\n")
    for k in sorted(st): print(f"  {k:32} {st[k]}")
