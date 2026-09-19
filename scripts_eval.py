#!/usr/bin/env python3
"""
Eval loop - run after every change; scores go to evals/history.jsonl
Usage:  python scripts_eval.py <data dir> [--score-cli path] [--gt path]
"""
import json, subprocess, sys, time, pathlib, argparse

def git_sha():
    try: return subprocess.check_output(["git","rev-parse","--short","HEAD"],text=True).strip()
    except Exception: return "nogit"

ap = argparse.ArgumentParser()
ap.add_argument("data_dir")
ap.add_argument("--score-cli", default=None, help="path to the official score_cli.py (local scoring)")
ap.add_argument("--gt", default=None, help="path to ground_truth.json")
ap.add_argument("--server", default=None, help="Docker scoring service, e.g. http://localhost:8080")
a = ap.parse_args()

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from pipeline.run import main
sub, stats = main(a.data_dir, "submission.json")
print(f"Generated {len(sub)} records -> submission.json")

result = None
if a.server:
    from urllib import request
    from urllib.error import URLError
    try:
        req = request.Request(a.server.rstrip("/") + "/submit",
                              data=json.dumps(sub).encode(),
                              headers={"Content-Type": "application/json"})
        result = json.loads(request.urlopen(req, timeout=60).read())
    except URLError as e:
        sys.exit(f"Cannot reach the scoring service {a.server}: {e}\n"
                 f"   Check: is the docker compose up window still open?\n"
                 f"   Verify: curl {a.server}/health should return emails: 520")
elif a.score_cli and a.gt:
    out = subprocess.check_output(
        [sys.executable, a.score_cli, "submission.json", "--ground-truth", a.gt, "--json"],
        text=True)
    result = json.loads(out)

if result:
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "sha": git_sha(),
           "final_score": result.get("final_score"),
           "stage1_macro_f1": result["stage1"]["macro_f1"],
           "defect_f1": result["stage3"]["defect_f1"],
           "end_to_end": result["end_to_end"]["rate"],
           "esc_precision": result["reliability"]["escalation_precision"]}
    pathlib.Path("evals").mkdir(exist_ok=True)
    hist = pathlib.Path("evals/history.jsonl")
    prev = None
    if hist.exists():
        lines = [json.loads(l) for l in hist.read_text().splitlines() if l.strip()]
        prev = lines[-1] if lines else None
    with hist.open("a") as f: f.write(json.dumps(rec) + "\n")

    print("\n" + "="*54)
    print(f"  FINAL SCORE  {rec['final_score']:.4f}")
    print("="*54)
    for k in ("stage1_macro_f1","defect_f1","end_to_end","esc_precision"):
        d = f"  (previous {prev[k]:.4f}, {rec[k]-prev[k]:+.4f})" if prev else ""
        flag = " !! regression" if prev and rec[k] < prev[k] - 1e-9 else ""
        print(f"  {k:18} {rec[k]:.4f}{d}{flag}")

    # ---- metrics layer (shipdoc_core.evaluate) -------------------------------------------
    # y_true comes from the confusion matrix the scoring service returns (actual -> predicted counts):
    # an aggregate, expanded into label multisets. No per-email gold is read.
    from shipdoc_core.evaluate import classification_report, field_level_prf
    cats = ["BL_COMPARISON", "SI_REQUEST", "INVOICE_QUERY", "GENERAL", "SPAM"]
    y_true, y_pred = [], []
    for actual, row in result["stage1"]["confusion"].items():
        for predicted, n in row.items():
            y_true += [actual] * n; y_pred += [predicted] * n
    cls = classification_report(y_true, y_pred)
    print(f"\n  Classification (n={len(y_true)}): accuracy {cls['accuracy']:.4f}  macro_f1 {cls['macro_f1']:.4f}")
    w = max(len(c) for c in cats)
    print("  " + " " * (w + 2) + "".join(f"{c[:6]:>8}" for c in cats) + "   <- predicted")
    for a in cats:
        print(f"  {a:>{w}} |" + "".join(f"{cls['confusion'].get(a, {}).get(p, 0):>8}" for p in cats))

    # Field-level PRF on our own hand-annotated golden set (evals/golden_fields.json); gold field sets
    # are not returned by the scoring service. The sample size is part of the output.
    golden_path = pathlib.Path("evals/golden_fields.json")
    field = None
    if golden_path.exists():
        golden = {k: set(v) for k, v in json.load(golden_path.open(encoding="utf-8")).items() if not k.startswith("_")}
        pred = {k: set(sub[k]["defect_fields"]) for k in golden if k in sub}
        m = field_level_prf(golden, pred)
        field = {"n_emails": len(golden), "n_gold_defect_fields": sum(len(v) for v in golden.values()),
                 **m.as_dict()}
        print(f"  Field-level PRF on the hand-annotated golden set (n={field['n_emails']} emails, "
              f"{field['n_gold_defect_fields']} defect fields): P {m.precision:.3f}  R {m.recall:.3f}  F1 {m.f1:.3f}  "
              f"(tp {m.tp} fp {m.fp} fn {m.fn})")
    else:
        print("  Field-level PRF: evals/golden_fields.json not found - skipped")

    json.dump({"ts": rec["ts"], "sha": rec["sha"], "scores": rec, "classification": cls,
               "field_level": field},
              pathlib.Path("evals/metrics_latest.json").open("w", encoding="utf-8"), indent=1)
    print("  -> evals/metrics_latest.json")
else:
    print("Not scored: pass --server or --score-cli + --gt")
