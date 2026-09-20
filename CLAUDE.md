# CLAUDE.md - ShipDoc project conventions

## Current state (2026-09-19, tag `prelim-hardened`, see git log for commits)

**Score**: final_score **1.0000** on the organiser's v2 dataset and on six semantics-preserving perturbations of it (all four axes: stage1_macro_f1 / defect_f1 / end_to_end / esc_precision). Not a claim about unseen data.
Trajectory: 0.7660 (rules baseline) -> 0.8896 (LLM fallback) -> 0.8914 (intent-aware escalation) -> 1.0000 (three comparison fixes: ports / ON BEHALF OF / PDF interleave).

**Perturbation tests** (`scripts/perturb.py`, `docs/PERTURBATION_REPORT.md`): all six perturbations P1-P5 at 1.0.
Before hardening P4b = 0.68 (LOCODE table too small) and P5 = 0.30 (attachments recognised by filename only); fixed by R2 / R4.

**Architecture**:
- Classification: rules first (`pipeline/classify.py`, reads only the email's own body) -> confidence < 0.80 goes to Gemini (`pipeline/classify_llm.py`, pydantic validation, model fallback chain, content-hash cache in `.cache/`). On v2: rule 232 / llm 288.
- Attachments: filename tag > content fingerprint > both fail NEEDS_REVIEW (`pipeline/run.py`).
- Comparison: `shipdoc_core/` (pure functions, no LLM). Port name over LOCODE; company names cut at `|` and `ON BEHALF OF`; LOCODE table 45 entries.
- Escalation: when attachments are missing, route by intent - "compare the SI and draft BL" -> `missing_attachment`; "please send the draft BL" -> OK; unsure -> escalate.
- Confidence routing: the parser gives every field an extraction confidence (exact label 1.0, stripped qualifier 0.95, fuzzy label = ratio, value from next line <= 0.9, interleave recovery <= 0.7); `compare_documents(si_conf, bl_conf)` caps each verdict with it; `needs_human_review` (verdict confidence < 0.62, or UNDETERMINED / missing) -> NEEDS_REVIEW. LLM classification confidence is carried into the evidence (`decision_confidence`). Metrics layer: `scripts/eval.py` prints the confusion matrix (from the service's aggregate) and field-level PRF on the hand-annotated `evals/golden_fields.json` (n=11) -> `evals/metrics_latest.json`.
- API / async (`api/`): FastAPI on Cloud Run (asia-southeast1), LLM via Vertex with the service account (no API key in the cloud), auth tiered by cost: `POST /process` anonymous with a per-IP rate limit (`RATE_LIMIT_PER_MIN`, default 10), `X-API-Key` from Secret Manager only on `POST /batch` / retry / chaos. `POST /batch` = one Cloud Tasks task per email (queue `shipdoc-process`, 3 attempts, backoff 5s-60s); third failure -> Firestore `dead_letter` {FAILED, reason, input}; `GET /failures`, `POST /failures/{key}/retry`; idempotency key = email_id + content hash. Locally `TASKS_MODE=inline` + `STORE=memory`. Demo fault injection: `fail_times` on an email or `POST /admin/chaos`. Live: https://shipdoc-api-705106212012.asia-southeast1.run.app - demo script in `docs/DEPLOY.md`. Demo page = `api/index.html` (Apple-style design tokens, light/dark, inline SVG icons) + `api/static/fonts/` (self-hosted Source Sans 3 variable font, mounted at `/static`).

**Console** (`web/`, Next.js 15.5.25, live at https://averis-sdoc-k3ce.vercel.app, Vercel project `averis-sdoc-k3ce` root dir `web`, env `API_BASE`; auto-deploys on push to main): `/` inbox (`GET /reports?prefix=email_`, ISR 15 s), `/emails/[id]` diff board with the raw → normalised popover + "Show normalisation" switch (localStorage), `/queues` exception vs incomplete (client polling `/api/reports?status=NEEDS_REVIEW`), `/eval` from `web/public/evals/` (`npm run sync-evals` copies from `evals/`; the PNGs are git-ignored at the source but committed under `web/public`). No UI library; tokens identical to `api/index.html`. Dataset loaded into Firestore once with `scripts/load_cloud.py` (520 rows; category totals 220/60/75/125/40; MISMATCH 46, NEEDS_REVIEW 20).

**Known, not done** (`docs/FINAL_ROUND_RISKS.md`): R5 interleave generalisation - deliberately left as an honestly-labelled 8-line patch (a data artefact, not a structural gap). Done: R1 (Vertex, local ADC + Cloud Run), R3 (prefix relation between party names -> MISMATCH, before the similarity score), R6 (record model in `parse_doc`: value after the colon, else the next non-label line).
**Cost parameters**: cost_missed=8 / false_alarm=1 / review=0.35 at the top of `scripts/calibration.py` are placeholders; Averis to confirm at Workshop 2 on 21 Sep.

**Tests**: `python -m pytest tests/ -q` -> 78 passed (66 pipeline/core + 12 api layer).

**Language**: everything in the repo and every demo-facing string is English; only literal dataset samples such as "Gross Weight毛重(KGS)" keep their Chinese.

## The only progress metric

Run after every code change:

```
python scripts/eval.py .\data --server http://localhost:8080
```

Results are appended to `evals/history.jsonl`. Roll back immediately if any axis drops. Perturbation runs use `scripts/perturb.py` and **do not** write history.jsonl.

## Hard constraints

- **Never read `ground_truth.json`, never read the data generator** (`sdoc-hackathon-docker/data_v2/*.py`). Only aggregate scores via `/submit`; no per-email probing.
- **`data/`, `submission*.json`, `.env`, `.cache/` never enter the repo** (already in `.gitignore`).
- Changes to `shipdoc_core/` must be: pure functions, no LLM, unit-tested, eval run first to confirm no drop.
- Rules are written from business semantics only; patches aimed at a few specific emails must say so honestly in a comment (e.g. `parse_doc._deinterleave`).

## This machine

- Python: `C:\Users\Admin\AppData\Local\Programs\Python\Python312\python.exe` (not on PATH).
- Scoring service: no Docker; the organiser's `server/app.py` runs directly under uvicorn on 8080 (data pointed at `sdoc-hackathon-docker/data_v2`). `/health` must return `emails: 520`.
- LLM: `.env` has `LLM_PROVIDER=aistudio` (free tier, `LLM_MIN_INTERVAL=6.5`), model chain `gemini-3.6-flash,gemini-3.5-flash,gemini-3.5-flash-lite`. The free quota was exhausted on 2026-09-19 (429 on all models); for local LLM runs use Vertex through env vars: `LLM_PROVIDER=vertex GCP_PROJECT=eco-world-296707 GCP_LOCATION=global LLM_MIN_INTERVAL=0` (ADC is logged in).
- gcloud: `C:\Users\Admin\AppData\Local\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd` (project and run/region already set; avoid arguments with spaces from Git Bash).
- Never `taskkill python.exe`: it also kills the scoring server. Stop processes by PID.
- The user is not comfortable with the command line: report the result after every step; on errors paste the full error and do not improvise fixes.
