"""
Latency / throughput numbers for docs/PERFORMANCE.md. Standard library only.
==========================================================================
  python scripts/bench.py local   --api http://localhost:8090      # POST /process at concurrency 1 / 8 / 20
  python scripts/bench.py cloud   --api https://...run.app          # GET /health, GET /report/{id}, GET /reports p50/p95
  python scripts/bench.py pipeline ./data                            # in-process: 520 emails end to end, rules vs cached LLM

Local: run the API with RATE_LIMIT_PER_MIN=100000 so the per-IP limiter does not shape the result.
Every number is written to evals/bench.json with the machine / mode it was measured on.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import pathlib
import platform
import statistics
import sys
import time
from urllib import request

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SI = "SHIPPING INSTRUCTION\nShipper: ABC CO LTD\nConsignee: XYZ LLC\nNotify: XYZ LLC\nPOL: SINGAPORE\nPOD: PORT KLANG\nContainer Count: 3\nGross Weight: 22000 KG\n"
BL = "BILL OF LADING (DRAFT)\nShipper: ABC Co Ltd\nConsignee: XYZ LLC\nNotify: XYZ LLC\nPort of Loading: SINGAPORE\nPort of Discharge: PORT KLANG\nTotal Containers: 4\nGross Wt (kgs): 22,000\n"
RULE_EMAIL = {"email_id": "bench", "from": "a@b", "subject": "TO CONFIRM DOCS", "body": "Attached are the SI and draft BL. Please check the details and confirm.",
              "attachments": [{"name": "b_SI.txt", "text": SI}, {"name": "b_BL.txt", "text": BL}]}


def pct(xs, p):
    xs = sorted(xs); k = max(0, min(len(xs) - 1, round(p / 100 * (len(xs) - 1))))
    return round(xs[k], 1)


def summary(ms: list[float]) -> dict:
    return {"n": len(ms), "p50_ms": pct(ms, 50), "p95_ms": pct(ms, 95), "p99_ms": pct(ms, 99), "mean_ms": round(statistics.mean(ms), 1),
            "max_ms": round(max(ms), 1)}


def call(url: str, method="GET", body=None, headers=None, timeout=120) -> tuple[int, float]:
    data = None if body is None else json.dumps(body).encode()
    req = request.Request(url, data=data, method=method, headers={"Content-Type": "application/json", **(headers or {})})
    t0 = time.perf_counter()
    try:
        with request.urlopen(req, timeout=timeout) as r:
            r.read(); code = r.status
    except Exception as e:                                       # noqa: BLE001
        code = getattr(e, "code", 0)
    return code, (time.perf_counter() - t0) * 1000


def bench_local(api: str) -> dict:
    out = {}
    for conc in (1, 8, 20):
        n = 60 if conc == 1 else 120
        with cf.ThreadPoolExecutor(conc) as ex:
            res = list(ex.map(lambda i: call(f"{api}/process", "POST", dict(RULE_EMAIL, email_id=f"bench-{conc}-{i}")), range(n)))
        ms = [t for c, t in res if c == 200]
        errs = sum(1 for c, _ in res if c != 200)
        t_total = sum(t for _, t in res) / 1000 / conc
        out[f"process_rules_c{conc}"] = {**summary(ms), "errors": errs, "throughput_rps": round(n / max(t_total, 1e-6), 1)}
        print(f"  POST /process (rules, no LLM) concurrency {conc:2}: {out[f'process_rules_c{conc}']}")
    return out


def bench_cloud(api: str) -> dict:
    out = {}
    for name, path in (("health", "/health"), ("report", "/report/email_004"), ("reports_520", "/reports?limit=1000&prefix=email_")):
        res = [call(f"{api}{path}") for _ in range(30)]
        ms = [t for c, t in res if c == 200]
        out[name] = {**summary(ms), "errors": sum(1 for c, _ in res if c != 200)}
        print(f"  GET {path}: {out[name]}")
    res = [call(f"{api}/process", "POST", dict(RULE_EMAIL, email_id=f"bench-cloud-{i}")) for i in range(5)]   # under the 10/min anonymous limit
    out["process_rules"] = {**summary([t for c, t in res if c == 200]), "errors": sum(1 for c, _ in res if c != 200)}
    print(f"  POST /process (rules): {out['process_rules']}")
    return out


def bench_pipeline(data: str) -> dict:
    from pipeline.run import main as run_main
    out = {}
    for label, env in (("rules_only", {"LLM_PROVIDER": "", "VISION": "off"}), ("hybrid_cached_llm", {"VISION": "off"})):
        saved = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        try:
            t0 = time.perf_counter(); sub, _ = run_main(data, str(ROOT / ".cache" / "bench_submission.json")); dt = time.perf_counter() - t0
        finally:
            for k, v in saved.items():
                if v is None: os.environ.pop(k, None)
                else: os.environ[k] = v
        out[label] = {"emails": len(sub), "total_s": round(dt, 2), "per_email_ms": round(dt / len(sub) * 1000, 2), "emails_per_s": round(len(sub) / dt, 1)}
        print(f"  pipeline {label}: {out[label]}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["local", "cloud", "pipeline"])
    ap.add_argument("target", nargs="?", help="data dir for pipeline mode")
    ap.add_argument("--api")
    a = ap.parse_args()
    if a.mode == "local":
        res = bench_local(a.api.rstrip("/"))
    elif a.mode == "cloud":
        res = bench_cloud(a.api.rstrip("/"))
    else:
        res = bench_pipeline(a.target)
    path = ROOT / "evals" / "bench.json"
    doc = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    doc[a.mode] = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "machine": f"{platform.node()} {platform.machine()} {platform.python_version()}",
                   "target": a.api or a.target, **res}
    path.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    print(f"-> {path}")


if __name__ == "__main__":
    main()
