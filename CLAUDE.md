# CLAUDE.md - ShipDoc project conventions

## Current state (2026-09-19, tag `day1-hardened`, see git log for commits)

**Score**: final_score **1.0000** on the v2 dataset, all four axes full marks (stage1_macro_f1 / defect_f1 / end_to_end / esc_precision).
Trajectory: 0.7660 (rules baseline) -> 0.8896 (LLM fallback) -> 0.8914 (intent-aware escalation) -> 1.0000 (three comparison fixes: ports / ON BEHALF OF / PDF interleave).

**Perturbation tests** (`scripts_perturb.py`, `docs/PERTURBATION_REPORT.md`): all six perturbations P1-P5 at 1.0.
Before hardening P4b = 0.68 (LOCODE table too small) and P5 = 0.30 (attachments recognised by filename only); fixed by R2 / R4.

**Architecture**:
- Classification: rules first (`pipeline/classify.py`, reads only the email's own body) -> confidence < 0.80 goes to Gemini (`pipeline/classify_llm.py`, pydantic validation, model fallback chain, content-hash cache in `.cache/`). On v2: rule 232 / llm 288.
- Attachments: filename tag > content fingerprint > both fail NEEDS_REVIEW (`pipeline/run.py`).
- Comparison: `shipdoc_core/` (pure functions, no LLM). Port name over LOCODE; company names cut at `|` and `ON BEHALF OF`; LOCODE table 45 entries.
- Escalation: when attachments are missing, route by intent - "compare the SI and draft BL" -> `missing_attachment`; "please send the draft BL" -> OK; unsure -> escalate.

**Known, not done** (`docs/FINAL_ROUND_RISKS.md`): R1 Vertex smoke test (needs gcloud login on this machine), R3 legal-qualifier rule, R5 interleave generalisation, R6 value on the next line.
**Cost parameters**: cost_missed=8 / false_alarm=1 / review=0.35 at the top of `scripts_calibration.py` are placeholders; Averis to confirm at Workshop 2 on 21 Sep.

**Tests**: `python -m pytest tests/ -q` -> 55 passed.

## The only progress metric

Run after every code change:

```
python scripts_eval.py .\data --server http://localhost:8080
```

Results are appended to `evals/history.jsonl`. Roll back immediately if any axis drops. Perturbation runs use `scripts_perturb.py` and **do not** write history.jsonl.

## Hard constraints

- **Never read `ground_truth.json`, never read the data generator** (`sdoc-hackathon-docker/data_v2/*.py`). Only aggregate scores via `/submit`; no per-email probing.
- **`data/`, `submission*.json`, `.env`, `.cache/` never enter the repo** (already in `.gitignore`).
- Changes to `shipdoc_core/` must be: pure functions, no LLM, unit-tested, eval run first to confirm no drop.
- Rules are written from business semantics only; patches aimed at a few specific emails must say so honestly in a comment (e.g. `parse_doc._deinterleave`).

## This machine

- Python: `C:\Users\Admin\AppData\Local\Programs\Python\Python312\python.exe` (not on PATH).
- Scoring service: no Docker; the organiser's `server/app.py` runs directly under uvicorn on 8080 (data pointed at `sdoc-hackathon-docker/data_v2`). `/health` must return `emails: 520`.
- LLM: `.env` has `LLM_PROVIDER=aistudio` (free tier, `LLM_MIN_INTERVAL=6.5`), model chain `gemini-3.6-flash,gemini-3.5-flash,gemini-3.5-flash-lite`; for delivery switch to `vertex` (`GCP_PROJECT` already set).
- The user is not comfortable with the command line: report the result after every step; on errors paste the full error and do not improvise fixes.
