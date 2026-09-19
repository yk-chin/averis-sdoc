# ShipDoc

Averis × Monash Hackathon 2026 — shipping-document intake for a BPO documentation team.

An email arrives. ShipDoc classifies it, parses the attached Shipping Instruction (SI) and draft Bill of Lading (BL), compares the seven fields that matter, and escalates only what a person genuinely needs to look at — **without creating false alarms**.

**Live API:** https://shipdoc-api-705106212012.asia-southeast1.run.app (Cloud Run, Singapore; LLM via Vertex AI)

## Results on the organiser's v2 dataset (520 emails)

| Axis | Score |
|---|---|
| final_score | **1.0000** |
| Email classification (macro-F1, 5 classes) | 1.0000 |
| Defect detection (F1) | 1.0000 |
| End-to-end (right email, right fields) | 1.0000 (46/46) |
| Escalation precision / recall | 1.000 / 1.000 (20 of 20) |

Trajectory: 0.766 (rules only) → 0.890 (LLM fallback) → 0.891 (intent-aware escalation) → 1.000 (comparison fixes). See `evals/history.jsonl` and `evals/progress.png`.

Six semantics-preserving perturbations of the dataset (label synonyms, company-suffix spelling, weight units, UN/LOCODE vs port names, untagged attachment names) all score 1.000 after hardening — `docs/PERTURBATION_REPORT.md`.

## How it works

```
email ──► rules (body only)  ──conf ≥ 0.80──► category
              │ conf < 0.80
              ▼
          Gemini (structured output, pydantic-validated, model fallback chain, cached)
                                                     │
attachments ──► filename tag > content fingerprint ──► SI / BL
                                                     │
          deterministic core: alias resolution → normalisation → field-by-field compare
                                                     │
          OK  |  MISMATCH + defect fields  |  NEEDS_REVIEW (missing_attachment / unreadable /
                                              wrong_doc_type / missing_value)
```

Why a deterministic core: most teams hand the SI and BL to an LLM and ask "what differs". That is not reproducible, not auditable, and hallucinates false alarms. Here the LLM only classifies emails when the rules are unsure; every comparison is a pure function with evidence (raw value, normalised value, reason, confidence) that can be explained line by line.

| Module | Role |
|---|---|
| `shipdoc_core/fields.py` | The seven-field ontology, label aliases, and **near-miss traps** (Place of Receipt ≠ Port of Loading) |
| `shipdoc_core/normalize.py` | Company names (suffixes, "on behalf of"), ports (names + UN/LOCODE), counts, weights with unit conversion |
| `shipdoc_core/compare.py` | Deterministic comparator → `FieldResult` with evidence, `ComparisonReport` |
| `shipdoc_core/evaluate.py` | PRF, confusion, **confidence calibration (ECE)**, **cost-sensitive threshold optimisation** |
| `pipeline/classify.py` | Rule-based email classification (body only — subjects are deliberately misleading in the data) and the attachment-intent check |
| `pipeline/classify_llm.py` | Gemini fallback via `google-genai`: `LLM_PROVIDER=aistudio` (API key) or `vertex` (service-account ADC) |
| `pipeline/parse_doc.py`, `parse_office.py` | txt / pdf / docx / xlsx → "Label: Value" → fields |
| `pipeline/run.py` | End-to-end pipeline → `submission.json` |
| `api/main.py`, `api/tasks.py`, `api/store.py` | FastAPI service; Cloud Tasks batch processing with 3 retries, a Firestore dead-letter queue and manual retry ("handle processing failures visibly and allow retries") |

Design invariants:
1. `compare.py` and `normalize.py` never import an LLM or network library.
2. When unsure → `UNDETERMINED` → a human. Never guess.
3. Formatting-only differences must be `NORMALIZED_MATCH`, never a mismatch.
4. Every verdict carries a reason and the before/after values.

## Setup

```bash
python -m pip install -r requirements.txt pytest
cp .env.example .env          # add GEMINI_API_KEY for local runs (see comments in the file)
```

The organiser's dataset is **not** in this repository (`.gitignore`). Put the bundle's `inbox/` and `attachments/` under `./data/`:

```
data/
  inbox/          520 × *.json
  attachments/    250 files (txt / pdf / xlsx / docx)
```

## Run

```bash
python pipeline/run.py ./data submission.json      # pipeline → submission.json
python -m pytest tests/ -q                          # 73 tests
python -m uvicorn api.main:app --port 8090          # the API locally
```

## Self-evaluation (black box)

We score ourselves against the organiser's Docker scoring service (`sdoc-hackathon-docker`, `docker compose up`) through its `POST /submit` endpoint:

```bash
python scripts_eval.py ./data --server http://localhost:8080
```

The script builds `submission.json`, posts it, and appends the returned scores to `evals/history.jsonl`. **We read only the aggregate scores** (`final_score`, `stage1_macro_f1`, `defect_f1`, `end_to_end`, `esc_precision`). We never read, parse, or copy `ground_truth.json`; the service keeps `REVEAL_GT` off and the ground truth is used server-side only. That is the black-box setup the organiser designed — we see the score, not the answers, so the system has to genuinely generalise.

Other evaluation scripts:

- `scripts_calibration.py` — reliability diagram + ECE, cost-sensitive threshold sweep, score progress (`evals/*.png`). The cost constants at the top are placeholders until Averis confirms real ratios.
- `scripts_perturb.py` — the perturbation tests (`docs/PERTURBATION_REPORT.md`).

## API

| Endpoint | Auth | Description |
|---|---|---|
| `GET /` | — | Test page (works on a phone) |
| `GET /health` | — | Liveness; `?deep=1` makes one real LLM call |
| `POST /process` | — (rate-limited per IP) | One email → `decision` + `evidence` (classification basis, parsed attachments, seven `FieldResult`s, readable report) |
| `POST /batch` | `X-API-Key` | Up to 200 emails, one Cloud Tasks task each (3 attempts, exponential backoff); duplicates by email_id + content hash are skipped |
| `GET /batch/{id}` | — | Per-email status of a batch |
| `GET /report/{id}` | — | Result by idempotency key or email_id (Firestore) |
| `GET /failures` | — | Dead-letter queue: emails that failed all 3 attempts, with reason and original input |
| `POST /failures/{key}/retry` | `X-API-Key` | Retry a dead-lettered email |

Request body: `{"email_id", "from", "subject", "body", "attachments": [{"name", "content_base64"} or {"name", "text"}]}`.

Auth is tiered by cost: single-email processing needs no credentials (10 requests / minute / IP); batch processing and dead-letter retries require `X-API-Key`, because one batch can consume the LLM quota.

Deployment details, runtime identity, and the redeploy command: `docs/DEPLOY.md`.

## Future roadmap (not implemented)

- **OCR / vision for scanned PDFs.** Image-only PDFs are currently escalated as `unreadable`. In the organiser's data the five unreadable documents are gold `unreadable`, so this is deliberately out of scope for the hackathon build.
- **LLM extraction fallback** for documents the deterministic parser cannot read (non-tabular layouts, free-text letters). Today: `unreadable` / `missing_value` escalation, never a guess.
- **Learned confidence calibration.** Extraction and verdict confidences are fixed per outcome kind; `scripts_calibration.py` measures them but nothing is fitted yet.
- **Firestore-transaction idempotency** across instances (today: idempotency key + status check, adequate at hackathon scale).

## Documents

- `docs/FINAL_ROUND_RISKS.md` — the dataset-shape assumptions behind each fix and what breaks if the final-round data differs
- `docs/PERTURBATION_REPORT.md` — perturbation tests before / after hardening
- `EVAL.md` — the evaluation loop
