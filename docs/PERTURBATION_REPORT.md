# Perturbation Report

> Purpose: answer "is the 1.0 on the v2 dataset generalisation or fitting?"
> Method: perturb a **copy** of the dataset without changing its meaning, re-run the pipeline, score through the organiser's `/submit`.
> Script: `scripts_perturb.py` (copies under `.cache/perturb/`, original `data/` read-only, ground_truth never read, `history.jsonl` not written).
> Date: 2026-09-19 · Code: before hardening `65d498b` -> after hardening `88d7eed` (tag `day1-hardened`)

## Perturbations

| # | What is perturbed | Why it is "semantics-preserving" |
|---|---|---|
| P1 | Field labels replaced by synonyms, a different alias on SI and BL (Port of Loading -> Load Port -> POL ...), rotated per email | Industry-standard labels for the same field |
| P2 | Company-suffix spelling: variant A on SI (`Co Ltd` / `Pte Ltd` / `Sdn. Bhd.` / `L.L.C.`), variant B on BL (`Company Limited` / `Pte. Limited` / `Sendirian Berhad`) | Same legal entity |
| P3 | Weight units: SI converted to MT (2 decimals), BL to LBS (1 decimal) | Numerically equivalent, error < 0.1 % tolerance |
| P4a | Ports: SI keeps the name only (UN/LOCODE removed), BL untouched | The name is the port |
| P4b | Ports: BL keeps the UN/LOCODE only (e.g. `KEMBA`) - only for values whose name and code agree in the real LOCODE table | The code is the port; the organiser's "name changed, code kept" defects are left alone so no defect is erased |
| P5 | Attachment filenames lose their `_SI` / `_BL` tags (`email_001_SI.txt` -> `email_001_doc_a.txt`), inbox references updated | File contents unchanged |

Coverage: P1-P4 modify txt / xlsx / docx attachments (222/250); **PDF attachments (28) are not perturbed**; P4 only touches txt because port values in xlsx/docx carry no code. P5 covers all 250 attachments.

## Results: before vs after hardening

| Perturbation | final before | final after | Detail before | Detail after |
|---|---|---|---|---|
| baseline (unperturbed) | 1.0000 | 1.0000 | all four axes 1.0 | all four axes 1.0 |
| P1 field-label synonyms | 1.0000 | 1.0000 | - | - |
| P2 company-suffix spelling | 1.0000 | 1.0000 | - | - |
| P3 weight units MT / LBS | 1.0000 | 1.0000 | - | - |
| P4a ports, SI name only | 1.0000 | 1.0000 | - | - |
| **P4b ports, BL LOCODE only** | **0.6805** | **1.0000** | defect_f1 0.653 · end_to_end 0.500 | all four axes 1.0 |
| **P5 attachment names without `_SI`/`_BL`** | **0.3000** | **1.0000** | defect_f1 0.000 · end_to_end 0.000 · esc_P 0.155 | all four axes 1.0 |

(four axes = stage1_macro_f1 / defect_f1 / end_to_end / esc_precision)

## The two confirmed weak points and their fixes

**P5 -> R2 attachment routing by filename only.** Once the filenames change, `has_si / has_bl` are all False, none of the 109 comparisons happen, end-to-end goes to zero and 129 emails are wrongly escalated.
Fix (`pipeline/run.py` `classify_attachments()`): filename tag > content fingerprint `detect_doc_type()` > both fail -> unassigned; a missing slot is escalated as NEEDS_REVIEW (`unreadable` if the file could not be read, `wrong_doc_type` if it could but is not SI/BL).

**P4b -> R4 a UN/LOCODE table of only 22 entries.** When a BL writes the bare code `KEMBA`, an unknown code is treated as a port name and differs from the SI's `MOMBASA` -> false alarm; 23 of the 46 defect emails got an inexact field set.
Fix (`shipdoc_core/normalize.py` `_LOCODE_NAME`): +23 real UN/LOCODEs. **Only entries confirmed as genuine industry data, never reverse-engineered from the dataset** - mappings like `AUFRE->BUSAN`, `KEMBA->TUTICORIN` in the data are the organiser's planted defects; `IDBUA` (Buatan) could not be confirmed as an official code and was left out.

Each fix carries regression tests (55 tests in total, all green); the normal eval stays at 1.0 on all four axes.

## Conclusion

- Alias resolution, company-name normalisation, weight-unit conversion and port-name matching **all hold** under perturbation - this part is genuine generalisation.
- Attachment naming and the LOCODE table were **structural assumptions** that the v2 data never triggered; only a perturbation test could expose them. Both are fixed and now guarded by P5 / P4b.
- Not covered: content perturbation of PDF attachments (28); label-value-on-next-line layout (R6); PDF interleave artefacts on other labels (R5). See `docs/FINAL_ROUND_RISKS.md`.

---

### For slides (English summary)

**Perturbation testing.** To check whether our 1.0 on the v2 set reflects generalisation rather than fitting, we re-scored the pipeline on six semantics-preserving perturbations of a copy of the dataset (original data untouched, ground truth never read): label synonyms rotated per document (P1), company-suffix spelling varied differently on SI vs BL (P2), weights converted to MT on SI and LBS on BL (P3), UN/LOCODE stripped from SI (P4a), BL reduced to LOCODE only (P4b), and attachment filenames stripped of their `_SI`/`_BL` tags (P5). P1–P4a held at 1.000. P4b fell to 0.68 and P5 to 0.30, exposing two structural assumptions the normal evaluation could never trigger: a 22-entry LOCODE table and filename-based attachment routing. After adding 23 verified UN/LOCODEs and a content-fingerprint fallback for attachments (55 unit tests, normal eval unchanged), all six perturbations score 1.000.
