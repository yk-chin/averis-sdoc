#!/usr/bin/env python3
"""
eval 闭环 —— 每次改动后跑它，分数进 evals/history.jsonl
用法:  python scripts_eval.py <数据目录> [--score-cli 路径] [--gt 路径]
"""
import json, subprocess, sys, time, pathlib, argparse

def git_sha():
    try: return subprocess.check_output(["git","rev-parse","--short","HEAD"],text=True).strip()
    except Exception: return "nogit"

ap = argparse.ArgumentParser()
ap.add_argument("data_dir")
ap.add_argument("--score-cli", default=None, help="官方 score_cli.py 路径（本地打分）")
ap.add_argument("--gt", default=None, help="ground_truth.json 路径")
ap.add_argument("--server", default=None, help="Docker 打分服务，例：http://localhost:8080")
a = ap.parse_args()

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from pipeline.run import main
sub, stats = main(a.data_dir, "submission.json")
print(f"生成 {len(sub)} 条 → submission.json")

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
        sys.exit(f"❌ 连不上打分服务 {a.server}：{e}\n"
                 f"   检查：docker compose up 那个窗口还开着吗？\n"
                 f"   验证：curl {a.server}/health 应该返回 emails: 520")
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
        d = f"  (上次 {prev[k]:.4f}, {rec[k]-prev[k]:+.4f})" if prev else ""
        flag = " ⚠️ 退步" if prev and rec[k] < prev[k] - 1e-9 else ""
        print(f"  {k:18} {rec[k]:.4f}{d}{flag}")
else:
    print("未打分：请给 --server 或 --score-cli + --gt")
