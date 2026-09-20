"""
Score the pipeline on the independent hold-out set (evals/holdout) against our own gold labels.
================================================================================================
The four axes are OUR re-implementation of the organiser's names (their exact formulas are not published):
  stage1_macro_f1  macro F1 over the five categories
  defect_f1        F1 over (email, field) defect pairs, gold BL_COMPARISON emails
  end_to_end       share of gold BL_COMPARISON emails whose (status, review_reason, defect_fields) match exactly
  esc_precision    among predicted NEEDS_REVIEW: share whose gold is NEEDS_REVIEW (recall also reported)
final = unweighted mean of the four. Every miss is listed, so the number can be argued with.
Never writes history.jsonl. Usage: python scripts/holdout_eval.py [--llm]  (--llm keeps the LLM on; default rules only,
so the score is reproducible without credentials; the LLM-on number is reported separately when asked for).
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
HOLD = ROOT / "evals" / "holdout"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", action="store_true", help="keep LLM_PROVIDER from the environment (default: rules only)")
    ap.add_argument("--out", default=str(ROOT / "evals" / "holdout_result.json"))
    a = ap.parse_args()
    if not a.llm:
        os.environ["LLM_PROVIDER"] = ""
    os.environ.setdefault("VISION", "off")
    from pipeline.run import decide
    from shipdoc_core.evaluate import classification_report, field_level_prf, wilson_interval

    gold = {k: v for k, v in json.loads((HOLD / "gold.json").read_text(encoding="utf-8")).items() if not k.startswith("_")}
    pred, misses = {}, []
    for eid, g in gold.items():
        email = json.loads((HOLD / "inbox" / f"{eid}.json").read_text(encoding="utf-8"))
        details = {}
        p = decide(email, HOLD, details=details)
        pred[eid] = p
        diff = {k: (g[k], p[k]) for k in ("category", "status", "review_reason", "defect_fields") if g[k] != p[k]}
        if diff:
            misses.append({"email_id": eid, "diff": diff, "review_detail": details.get("review_detail", [])})

    cls = classification_report([gold[e]["category"] for e in gold], [pred[e]["category"] for e in gold])
    bl = [e for e in gold if gold[e]["category"] == "BL_COMPARISON"]
    fl = field_level_prf({e: set(gold[e]["defect_fields"]) for e in bl}, {e: set(pred[e]["defect_fields"]) for e in bl})
    e2e_hits = sum(1 for e in bl if (gold[e]["status"], gold[e]["review_reason"], gold[e]["defect_fields"]) ==
                   (pred[e]["status"], pred[e]["review_reason"], pred[e]["defect_fields"]))
    end_to_end = e2e_hits / len(bl)
    pred_esc = [e for e in gold if pred[e]["status"] == "NEEDS_REVIEW"]
    gold_esc = [e for e in gold if gold[e]["status"] == "NEEDS_REVIEW"]
    esc_tp = sum(1 for e in pred_esc if gold[e]["status"] == "NEEDS_REVIEW")
    esc_p = esc_tp / len(pred_esc) if pred_esc else 0.0
    esc_r = esc_tp / len(gold_esc) if gold_esc else 0.0
    final = (cls["macro_f1"] + fl.f1 + end_to_end + esc_p) / 4
    result = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "llm": a.llm, "n_emails": len(gold), "n_bl_comparison": len(bl),
              "final_score": round(final, 4), "stage1_macro_f1": cls["macro_f1"], "defect_f1": fl.f1,
              "defect_prf": fl.with_ci(), "end_to_end": round(end_to_end, 4), "end_to_end_ci95": list(wilson_interval(e2e_hits, len(bl))),
              "esc_precision": round(esc_p, 4), "esc_recall": round(esc_r, 4),
              "per_class": cls["per_class"], "confusion": cls["confusion"], "misses": misses,
              "note": "our re-implementation of the four axes on a self-authored set; final = unweighted mean"}
    pathlib.Path(a.out).write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"hold-out ({len(gold)} emails, LLM {'on' if a.llm else 'off'}): final {final:.4f}  macro_f1 {cls['macro_f1']:.4f}  "
          f"defect_f1 {fl.f1:.4f}  end_to_end {end_to_end:.4f} ({e2e_hits}/{len(bl)})  esc_p {esc_p:.4f} esc_r {esc_r:.4f}")
    for m in misses:
        print(f"  miss {m['email_id']}: {m['diff']}")
    print(f"-> {a.out}")


if __name__ == "__main__":
    main()
