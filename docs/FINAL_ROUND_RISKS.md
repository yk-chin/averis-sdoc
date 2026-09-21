# Risk register

Status vocabulary: **Mitigated** (evidence in the repo), **Open** (known, not fixed), **Accepted** (known, will not fix),
**Partially** (fix in code, operational step incomplete). Every Mitigated row points at a commit, a test or an eval file;
statuses were re-verified against the code on 2026-09-21 (file:line in the Evidence column). Fixes for Open items are
scheduled after the preliminary round under the hold-out protocol in `EVAL.md`.

## Pipeline and comparison core

| ID | Risk | Status | Evidence | Residual risk |
|---|---|---|---|---|
| R1 | LLM availability: 288/520 emails need a classification call; free-tier quota was exhausted on 19 Sep | **Mitigated** | Vertex AI through the service account in the cloud (`docs/DEPLOY.md`), `GET /health?deep=1` exercises it live; model fallback chain + 60 s cooldown (`pipeline/classify_llm.py`); on total failure the rules decide and the evidence says so (`decided_by: rule`). AI Studio (`LLM_PROVIDER=aistudio`) exists as a second provider but switching is by environment variable, not automatic | Ablation `rules_only` = 0.8433 (`evals/ablation.json`) is the floor if every model is down; `min-instances=1` during judging |
| R2 | Attachment routing by filename tag only | **Mitigated** | Content-fingerprint fallback (`pipeline/run.py:57`); perturbation P5 (all tags removed) = 1.000 (`evals/perturbation.json`) | An attachment whose text carries neither heading nor recognisable fields is left unassigned → `wrong_doc_type` escalation (safe direction) |
| R3 | Party similarity exactly on the 0.75 threshold (`APRIL FINE PAPER TRADING` vs `… (MIDDLE EAST) FZE`) | **Mitigated** | Structural prefix rule before the score (`shipdoc_core/compare.py:119-123`), `tests/test_core.py` | Pairs that are neither prefix-related nor clearly apart still land in the grey zone → a person (by design) |
| R4 | UN/LOCODE table size | **Open** | 47 entries (`len(_LOCODE_NAME)`, `shipdoc_core/normalize.py`), grown from 22; P4b = 1.000 on the organiser's codes | Codes outside the table are compared as names — see R-P1. Production answer: the full UNECE list, versioned |
| R5 | PDF interleave artefact recognised for one label only | **Accepted** | `pipeline/parse_doc.py:49-53`, honestly labelled as a data-artefact patch; all v2 cases pass; the eight-line patch is not a structural gap | A different interleaved label in the final data → garbled value → grey zone / escalation, not a silent pass |
| R6 | Value on the line after `Label:` | **Mitigated** | Record model takes the next non-label line (`pipeline/parse_doc.py:92-105`, confidence capped 0.9); tests in `tests/test_pipeline_rules.py` | Multi-line addresses beyond one continuation line are cut (party names survive; addresses are stripped anyway) |
| R7 | The no-attachment comparison-request rule branch never reaches the LLM | **Accepted** | `pipeline/classify.py:91` ("DELIBERATELY FINAL"), rule P/R 1.0 on the 96 emails on that path; test proves the LLM is not called | One invoice email phrased as "please confirm the documents" would be misclassified (≈ 0.003 macro-F1) |
| R8 | Subject ignored by the rules | Accepted | `own_text()` reads the body; the LLM prompt includes the subject | none observed |
| R9 | Quoted-mail truncation on `From:` / `Original Message` | Accepted | `pipeline/classify.py:36`; not present in v2 | A body with "From:" mid-sentence loses its tail |
| R10 | Word lists in the missing-attachment intent check | Accepted | `DOCS_REQUESTED_PAT` / `DOCS_IN_HAND_PAT` (`pipeline/classify.py:42-46`); ambiguity escalates (safe) | esc_precision, never a silent OK |
| R11 | SI_REQUEST semantics inferred from gold consistency | Accepted | v2 macro-F1 1.0; same generator in the final | Low |
| R12 | Weight tolerance 0.1 % / 0.5 kg | Accepted | `shipdoc_core/compare.py:41-42`, policy stated in every reason and in `docs/REVIEW_REASONS.md`; smallest planted defect 500 kg | A 10 kg real difference would pass as formatting |
| R13 | Port-name equivalence by token-set containment | Accepted | `ports_match` (`shipdoc_core/normalize.py:394`) | `PORT` alone never occurs |
| R14 | Same name, different code → MISMATCH | Accepted | name takes precedence; internal inconsistency treated as a defect | Two legitimate codes for one port would false-alarm (rare) |
| R15 | Rule-layer quality without the LLM | Accepted | ablation rules-only 0.8433, macro-F1 0.48 | This is R1's floor, not a target |
| R16 | Time budget on the free tier | Mitigated | Vertex, `LLM_MIN_INTERVAL=0`; 520 emails in 3.8 s with a warm cache (`docs/PERFORMANCE.md`) | Cold cache after a container restart: 288 calls |
| R17 | Scanned / image-only PDFs | **Mitigated (proposal only)** | `pipeline/vision.py`: Gemini vision proposal capped at 0.60 < review threshold, provisional comparison, decision stays `unreadable`; `tests/test_vision.py`; 512/513/514 verified live | A proposal never auto-passes, so a scan-heavy final lowers end-to-end but cannot false-alarm; corrupt files (511/515) stay unreadable |

## Found by the hold-out (`evals/holdout_result_llm.json`; frozen, not tuned on)

| ID | Risk | Status | Evidence | Residual risk |
|---|---|---|---|---|
| **R-P1** | A bare UN/LOCODE outside our table is compared as a *name* — even when the other side carries the same code (`COLOMBO (LKCMB)` vs `LKCMB`) | **Open** | holdout_003 / holdout_010 false alarms; `ports_match` name-first logic; violates invariant 2 ("unsure → a human") | Planned post-prelim: fail-closed — unknown code on one side → `UNDETERMINED` (a person), equal codes → match; holdout_010 additionally needs the full table |
| **R-P2** | Legal forms and connectors beyond our suffix table: `K.K.` ↔ `KABUSHIKI KAISHA`, `&` ↔ `AND` | **Open** | holdout_001 / 009 / 014 (K.K.), 012 / 016 (`&`) | Post-prelim: connector normalisation and a few legal-form synonyms; production: ISO 20275 entity-legal-form codes |
| **R-P3** | When one field is undecidable, a confident mismatch in another field is escalated but not listed in `defect_fields` | **Open** | `pipeline/run.py` returns `escalate("missing_value", …)` before `mismatched_fields` is read; holdout_012 (gross weight) / 016 (shipper) | The email still reaches a person (no silent miss); needs one v2 black-box check before changing, because it alters the submission record shape for grey-zone emails |
| R-P4 | Label abbreviations outside the alias list (`Shpr`, `Cnee`, `G.Wt.`) | Open (roadmap) | holdout_007 → `missing_value` escalation, never a guess | A deterministic shipping-abbreviation table; a constrained LLM label mapper (output limited to the seven keys or none, confidence capped 0.60) |

## Service, security, operations

| ID | Risk | Status | Evidence | Residual risk |
|---|---|---|---|---|
| R-S1 | Anonymous `POST /process` could shadow an organiser email (`/report/{email_id}` returns the newest doc; the console lists prefix `email_`) | **Mitigated** | `RESERVED_PREFIX` check in `api/main.py process()` → 422 without a valid API key; `tests/test_api_security.py` | Keyed callers can still overwrite a dataset email on purpose (that is the refresh path) |
| R-S2 | Reviewer identity (verified email) shown publicly by `GET /report/{id}` | **Mitigated** | `_public_view()` masks `review.reviewer` / `identity.email`, drops request ids; `tests/test_api_security.py` | The authenticated review response keeps the full identity by design |
| R-S3 | Anonymous uploads kept indefinitely | **Mitigated** | `expire_at = now + 24 h` on anonymous `/process` documents only (test); Firestore TTL policy `reports.expire_at` enabled 21 Sep (`docs/SECURITY.md`) | TTL deletion runs within ~24 h of expiry, not at the second |
| R-S4 | One shared API key for batch / DLQ / chaos; reviewer name client-asserted | Mitigated | Separate review-only token; OIDC bearer → verified identity; `identity` recorded in the audit trail (commit `2f6d578`) | Production: every read behind an identity (`docs/SECURITY.md`) |
| R-D1 | Unpinned dependencies (`>=` ranges), no lock file | **Open** | `requirements.txt`; `google-auth` now declared directly | No rebuild during the judging window; a lock file is to be generated inside a Linux container (Windows `pip freeze` drags platform packages) |
| R-A1 | `/reports` reads all documents on every call | **Accepted** | 608 ms p50 at 520 docs (`evals/bench.json`); console ISR 15 s | Scaling path in `docs/PERFORMANCE.md` (server-side filters, composite index, cursor pagination, `count()`) |
| R-A2 | Cloud Tasks concurrency fixed at 3 | **Accepted** | Quota-bound by design: keeps first-time Vertex calls under the model's rate limit; 520 emails in ≈ 8 min, 0 failures | Derivation of the rate knob in `docs/PERFORMANCE.md` |
| R-A3 | Cold start 3–6 s | Mitigated (judging window) | `min-instances=1` set 20 Sep (`docs/DEPLOY.md`) | Costs ≈ USD 1.5–2 / day; switch back after judging |
| R-B1 | SI/BL pairing not checked (a BL for booking A compared with the SI of booking B) | Open (roadmap) | No booking-reference field in the ontology | Planned: a non-verdict `pairing_warning` when booking refs differ |
| R-B2 | Incoterms / LC awareness for prioritisation | Open (roadmap) | Subjects carry `LC`, `DP`, `CFR`, `OA`; not read | An LC shipment with a mismatch should escalate first (`docs/ROI.md`) |

## Order of work after the preliminary round

1. R-P1 (fail-closed unknown code), then R-P2, then R-P3 — each with one v2 black-box check; the hold-out becomes a development set from the first of these commits (`EVAL.md`).
2. A second hold-out authored by someone who has not read the rules.
3. R-D1 lock file in a Linux container; R-B1 pairing warning.
