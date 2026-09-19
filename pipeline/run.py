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
from pipeline.classify import classify_email, expects_attachments
from pipeline.classify_llm import classify_with_llm, STATS as LLM_STATS, MODEL_USAGE

TEXT_EXT = {".txt"}
OFFICE_EXT = {".pdf", ".docx", ".xlsx"}
LLM_THRESHOLD = 0.80      # 规则置信度低于此值才调 LLM；BL_COMPARISON 分支全部 ≥0.80，不会调用


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
    """把附件分配到 SI / BL 两个槽位。
    优先级：文件名标记（_SI. / _BL.）> 内容指纹（detect_doc_type）> 都失败 → 留在 unassigned
    返回 (slots, unassigned)：slots = {"SI": (doc, src) | None, "BL": ...}；unassigned = [(doc, src)]
    doc 为 None 表示读不出文本（unreadable / missing）。"""
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
    for doc, src in pending:                                   # 内容指纹兜底
        kind = doc.doc_type if doc is not None else None
        if kind in slots and slots[kind] is None:
            slots[kind] = (doc, src)
        else:
            unassigned.append((doc, src))
    return slots, unassigned


def decide(email, root) -> dict:
    atts = email.get("attachments", []) or []
    slots, unassigned = classify_attachments(root, atts)
    has_si, has_bl = slots["SI"] is not None, slots["BL"] is not None
    si, si_src = slots["SI"] or (None, None)
    bl, bl_src = slots["BL"] or (None, None)
    category, decided_by, conf = classify_email(email, has_si, has_bl)

    # 规则优先、LLM 兜底：只在规则拿不准时调用；LLM 失败则保留规则结果
    if conf < LLM_THRESHOLD:
        llm = classify_with_llm(email)
        if llm is not None:
            category, decided_by = llm.category, "llm"

    out = {"category": category, "status": "OK", "review_reason": None,
           "has_defect": False, "defect_fields": [], "decided_by": decided_by}

    if category != "BL_COMPARISON":
        return out

    # --- 可靠性闸门：按 official review_reason 的四种原因逐级判定 ---
    if not has_si or not has_bl:
        # 有附件但既无文件名标记、内容也认不出是 SI/BL：读不出 → unreadable；读得出 → wrong_doc_type
        if unassigned:
            reason = "unreadable" if any(d is None for d, _ in unassigned) else "wrong_doc_type"
            out.update(status="NEEDS_REVIEW", review_reason=reason)
            return out
        # 两种"没附件"要分开：
        #   incomplete request —— 发件人以为文件已附上、要求比对（"compare the SI and draft BL"），
        #                         文件却没到 → NEEDS_REVIEW / missing_attachment，需要人去催
        #   普通待办           —— 发件人在索要文件（"please send the draft BL for checking"），
        #                         手上本来没有可比对的东西 → OK，不是失败的比对
        if not expects_attachments(email):
            return out
        out.update(status="NEEDS_REVIEW", review_reason="missing_attachment")
        return out

    if si is None or bl is None:                       # 非纯文本：pdf/docx/xlsx
        src = si_src if si is None else bl_src
        if src in ("missing",):
            out.update(status="NEEDS_REVIEW", review_reason="missing_attachment")
        else:
            # 这里应接 OCR / Vision LLM。未接通时诚实上报 unreadable，不许猜。
            out.update(status="NEEDS_REVIEW", review_reason="unreadable")
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
    print(f"生成 {len(sub)} 条\n")
    for k in sorted(st): print(f"  {k:32} {st[k]}")
