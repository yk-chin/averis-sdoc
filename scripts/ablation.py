"""
Ablation: is the hybrid design a shortcut or the better answer?
=================================================================
Three configurations, each scored black-box through the organiser's service (aggregate score only,
never the ground truth), written to evals/ablation.json - never to history.jsonl.

  rules_only  the deterministic pipeline with the LLM disabled (LLM_PROVIDER unset, VISION=off)
  llm_only    every email classified by Gemini (rules bypassed), and for BL_COMPARISON the two document
              texts handed to Gemini with the seven field definitions, asking for the submission record
              directly (status, defect_fields, review_reason). No deterministic comparison at all.
  hybrid      the shipped pipeline (rules first, LLM when unsure, deterministic comparison, vision proposals)

Usage:  python scripts/ablation.py ./data --server http://localhost:8080 [--modes rules_only,llm_only,hybrid]
Needs the LLM env (LLM_PROVIDER=vertex ...) for llm_only and hybrid. The llm_only comparisons are cached in
.cache/ablation_llm.json so a re-run is free.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import time
from typing import Optional
from urllib import request

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
CACHE = ROOT / ".cache" / "ablation_llm.json"
CATEGORIES = ["BL_COMPARISON", "SI_REQUEST", "INVOICE_QUERY", "GENERAL", "SPAM"]


# ----------------------------------------------------------------------------- scoring
def submit(server: str, sub: dict) -> dict:
    req = request.Request(server.rstrip("/") + "/submit", data=json.dumps(sub).encode(),
                          headers={"Content-Type": "application/json"})
    r = json.loads(request.urlopen(req, timeout=120).read())
    return {"final_score": r.get("final_score"), "stage1_macro_f1": r["stage1"]["macro_f1"],
            "defect_f1": r["stage3"]["defect_f1"], "end_to_end": r["end_to_end"]["rate"],
            "esc_precision": r["reliability"]["escalation_precision"]}


# ----------------------------------------------------------------------------- the three configurations
def run_pipeline(data: str, out: pathlib.Path, env_extra: dict) -> dict:
    """Run pipeline/run.py in a subprocess so the LLM/vision switches are clean per configuration."""
    env = dict(os.environ, **env_extra)
    subprocess.run([sys.executable, str(ROOT / "pipeline" / "run.py"), data, str(out)], check=True, env=env,
                   stdout=subprocess.DEVNULL)
    return json.loads(out.read_text(encoding="utf-8"))


def llm_only(data: str, out: pathlib.Path) -> tuple[dict, dict]:
    from pydantic import BaseModel, Field
    from pipeline import classify_llm as llm
    from pipeline.run import read_attachment
    from shipdoc_core.fields import FIELDS

    class Verdict(BaseModel):
        status: str = Field(description="OK | MISMATCH | NEEDS_REVIEW")
        review_reason: Optional[str] = Field(None, description="wrong_doc_type | missing_attachment | unreadable | missing_value, or null")
        defect_fields: list[str] = Field(default_factory=list, description="field keys that genuinely differ (formatting differences are not defects)")

    field_defs = "\n".join(f"- {f.key}: {f.label} (aliases: {', '.join(f.aliases[:5])})" for f in FIELDS)
    system = ("You compare a Shipping Instruction (SI) with a draft Bill of Lading (BL) for a freight documentation team. "
              "Decide the submission record for the email. Fields:\n" + field_defs +
              "\nRules: formatting-only differences (units, thousands separators, company suffix spelling, port name vs "
              "UN/LOCODE, address lines) are NOT defects. status=MISMATCH with defect_fields when a field genuinely differs; "
              "status=OK when all seven agree; status=NEEDS_REVIEW with review_reason when a document is missing "
              "(missing_attachment), unreadable, not an SI/BL (wrong_doc_type) or a field is blank (missing_value). "
              "Return JSON only.")

    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    client = llm._get_client()
    if client is None:
        sys.exit(f"llm_only needs the LLM: {llm._client_err}")
    from google.genai import types
    root = pathlib.Path(data)
    sub, stats = {}, {"classify_calls": 0, "compare_calls": 0, "classify_fallbacks": 0, "compare_failures": 0, "cache_hits": 0}

    for f in sorted((root / "inbox").glob("*.json")):
        email = json.load(open(f, encoding="utf-8"))
        cls = llm.classify_with_llm(email)                     # always the LLM, no rule gate (cached by content)
        stats["classify_calls"] += 1
        if cls is None:
            stats["classify_fallbacks"] += 1
            category = "GENERAL"
        else:
            category = cls.category
        rec = {"category": category, "status": "OK", "review_reason": None, "has_defect": False,
               "defect_fields": [], "decided_by": "llm"}
        if category == "BL_COMPARISON":
            docs = []
            for rel in email.get("attachments") or []:
                text, src = read_attachment(root, rel)
                docs.append(f"### attachment {os.path.basename(rel)} ({src})\n{text if text else '[no text could be extracted]'}")
            prompt = (f"Email subject: {email.get('subject','')}\nEmail body:\n{email.get('body','')}\n\n" +
                      ("\n\n".join(docs) if docs else "[no attachments]"))
            key = hashlib.sha256(("ablation-v1" + system + prompt).encode()).hexdigest()
            if key in cache:
                v = cache[key]; stats["cache_hits"] += 1
            else:
                v = None
                for model in llm._available_models():
                    try:
                        llm._pace()
                        stats["compare_calls"] += 1
                        resp = client.models.generate_content(
                            model=model, contents=prompt,
                            config=types.GenerateContentConfig(system_instruction=system, response_mime_type="application/json",
                                                               response_schema=Verdict, temperature=0.0, max_output_tokens=512))
                        parsed = resp.parsed if isinstance(resp.parsed, Verdict) else Verdict.model_validate_json(resp.text or "")
                        v = parsed.model_dump(); break
                    except Exception as e:                      # noqa: BLE001
                        if llm._err_kind(e) == "gone":
                            llm._cooldown_until[model] = float("inf")
                        else:
                            time.sleep(3.0)
                if v is None:
                    stats["compare_failures"] += 1
                    v = {"status": "NEEDS_REVIEW", "review_reason": "unreadable", "defect_fields": []}
                cache[key] = v
                CACHE.parent.mkdir(exist_ok=True); CACHE.write_text(json.dumps(cache, indent=1), encoding="utf-8")
            status = v.get("status") if v.get("status") in ("OK", "MISMATCH", "NEEDS_REVIEW") else "NEEDS_REVIEW"
            reason = v.get("review_reason") if v.get("review_reason") in ("wrong_doc_type", "missing_attachment", "unreadable", "missing_value") else None
            fields = sorted(x for x in (v.get("defect_fields") or []) if x in {ff.key for ff in FIELDS})
            if status == "MISMATCH" and not fields:
                status = "OK"
            if status == "NEEDS_REVIEW" and reason is None:
                reason = "missing_value"
            rec.update(status=status, review_reason=reason if status == "NEEDS_REVIEW" else None,
                       has_defect=status == "MISMATCH", defect_fields=fields if status == "MISMATCH" else [])
        sub[email["email_id"]] = rec
        if len(sub) % 50 == 0:
            print(f"  llm_only: {len(sub)} emails ({stats['compare_calls']} comparison calls)", flush=True)
    out.write_text(json.dumps(sub, indent=2), encoding="utf-8")
    return sub, stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("data"); ap.add_argument("--server", required=True)
    ap.add_argument("--modes", default="rules_only,llm_only,hybrid")
    a = ap.parse_args()
    modes = [m.strip() for m in a.modes.split(",") if m.strip()]
    work = ROOT / ".cache" / "ablation"; work.mkdir(parents=True, exist_ok=True)
    results = {}
    for mode in modes:
        t0 = time.time(); extra = {}
        print(f"== {mode}", flush=True)
        if mode == "rules_only":
            sub = run_pipeline(a.data, work / "rules_only.json", {"LLM_PROVIDER": "", "VISION": "off"})
        elif mode == "hybrid":
            sub = run_pipeline(a.data, work / "hybrid.json", {})
        elif mode == "llm_only":
            sub, extra = llm_only(a.data, work / "llm_only.json")
        else:
            sys.exit(f"unknown mode {mode}")
        scores = submit(a.server, sub)
        from collections import Counter
        results[mode] = {**scores, "elapsed_s": round(time.time() - t0, 1),
                         "categories": dict(Counter(v["category"] for v in sub.values())),
                         "statuses": dict(Counter(v["status"] for v in sub.values() if v["category"] == "BL_COMPARISON")),
                         **extra}
        print("   " + "  ".join(f"{k} {v:.4f}" for k, v in scores.items()), flush=True)

    out = ROOT / "evals" / "ablation.json"
    payload = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "note": "aggregate scores from the organiser's /submit; "
               "rules_only = LLM and vision disabled; llm_only = Gemini classifies every email and decides every "
               "BL comparison from the raw document texts; hybrid = the shipped pipeline", "results": results}
    out.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    print(f"\n{'configuration':14} {'final':>8} {'macro_f1':>9} {'defect_f1':>10} {'end_to_end':>11} {'esc_prec':>9}")
    for m, r in results.items():
        print(f"{m:14} {r['final_score']:8.4f} {r['stage1_macro_f1']:9.4f} {r['defect_f1']:10.4f} {r['end_to_end']:11.4f} {r['esc_precision']:9.4f}")
    print(f"-> {out}")


if __name__ == "__main__":
    main()
