# Final-Round Risk Checklist

> Status: final_score 1.0000 on the v2 dataset (classification / defects / end-to-end / escalation all full marks).
> This checklist answers one question: **which of today's fixes depend on the specific shape of this dataset, and how do they break on different data?**
> Each item states: assumption -> location -> what happens if it fails (which metric) -> likelihood -> recommended action.
> Ordered by impact x likelihood x cost to fix.

---

## P0 - must do before the final

### R1. LLM availability: a single point of failure for the whole classification layer
- **Assumption**: at least one Gemini model is reachable when the eval runs.
- **Location**: `pipeline/classify_llm.py`; the 288 emails (55 %) with `conf < 0.80` in `run.py` depend on it entirely.
- **If it fails**: everything falls back to rules -> macro-F1 drops from 1.00 to **0.58** (final -0.13). Measured today: on the free key `gemini-3.6-flash` / `3.5-flash` returned 503/429 almost the whole time; 286/288 emails were carried by `3.5-flash-lite`; Wi-Fi dropped for 40 minutes mid-run and 126 emails failed.
- **Likelihood**: high. Free tier + a single Wi-Fi connection; final-day conditions are out of our control.
- **Actions**:
  1. **Switch to Vertex** (`.env` already has `GCP_PROJECT`; set `LLM_PROVIDER=vertex`, run `gcloud auth application-default login` locally) - paid quota, no rate limiting, `LLM_MIN_INTERVAL=0`. **The Vertex path has never been exercised and must be smoke-tested in advance.**
  2. Keep the AI Studio key as a second provider (the code is currently either/or; automatic switching needs ~15 lines).
  3. **The first thing** to do with the final data is a full pipeline run to fill the cache, before any debugging.

### R2. Attachment routing relies entirely on the filename tags `_SI.` / `_BL.`
- **Assumption**: SI/BL attachment filenames contain `_SI.` / `_BL.` (100 % of the 250 v2 attachments do).
- **Location**: `run.py` `classify_attachments()` / `has_si` / `has_bl` in `decide()`.
- **If it fails** (`SI_5RSG-19787.pdf`, `draft-bl.docx`, `docs.pdf`): `has_si = has_bl = False` -> every comparison request becomes `missing_attachment` or (if the body is a request for files) OK -> **end-to-end goes to zero, esc_precision collapses**. The most brittle single thread in the whole chain.
- **Likelihood**: medium. Same organiser, same generator, probably reused; but the existence of `edgecases.py` shows they deliberately deform things.
- **Action**: when the filename tag fails, fall back to a **content fingerprint** - `parse_doc.detect_doc_type()` already recognises SI/BL from the text; `classify_attachments()` just needs "filename first, content as fallback". ~15 lines + 3 tests. **Recommended now.**

### R3. Company-name similarity sits exactly on the threshold
- **Assumption**: different legal entities have similarity <= 0.75; spelling variants of the same entity >= 0.94.
- **Location**: `shipdoc_core/compare.py` `PARTY_FUZZY_LOW = 0.75` / `HIGH = 0.94`.
- **Measured**: `APRIL FINE PAPER TRADING` vs `APRIL FINE PAPER TRADING (MIDDLE EAST) FZE` has similarity **exactly 0.750** and is judged MISMATCH only thanks to `<=` (email_145, confirmed a gold defect). Among the 27 entities in the data pool this is the only pair on the grey-zone edge.
- **If it fails**: the same pair spelled slightly differently in the final (one extra comma) -> 0.76 -> UNDETERMINED -> `NEEDS_REVIEW/missing_value` -> that email is **an e2e miss and an escalation false positive at once**, hurting two axes.
- **Likelihood**: high. Both entities are in the generator's shipper pool; the final will almost certainly contain them again.
- **Action**: add a deterministic rule before the fuzzy match - **one name is a prefix of the other and the extra part contains a legal qualifier** (`(MIDDLE EAST)`, `FZE`, `SDN BHD`, `PTE LTD` ...) -> MISMATCH directly (different legal entities). No threshold change, no effect on other pairs. ~10 lines + 2 tests.

---

## P1 - recommended before the final, cheap

### R4. The port-code table has only 22 entries
- **Assumption**: port values look like `NAME, COUNTRY (CODE)` and the name is always present.
- **Location**: `normalize.py` `_LOCODE_NAME`; after today's change to **name over code**, the code only matters when one side has no name.
- **Measured**: 32 codes occur in the data, **24 are not in the table**. Nothing broke today only because every value carried a name.
- **If it fails** (one side writes only `KEMBA`, the other `MOMBASA, KENYA`): the bare unknown code is treated as a name `KEMBA` -> not equal to `MOMBASA` -> **false MISMATCH**.
- **Action**: add the 24 codes from the data using their **real UN/LOCODE** meanings (they cannot be reverse-engineered from the data - mappings such as `AUFRE->BUSAN`, `KEMBA->TUTICORIN` are the organiser's planted defects). 10 minutes.

### R5. `_deinterleave` recognises one interleave prefix only
- **Assumption**: the PDF character-interleave artefact only ever hits the `Notify Party/Intermediate Consignee` label.
- **Location**: `pipeline/parse_doc.py` `INTERLEAVED_LABEL = ^Party/Intermediate\s+Cons…`.
- **If it fails** (interleave on `Shipper/Exporter` or `Consignee`): the value becomes garbage -> fuzzy similarity ~0.3 -> **false MISMATCH**, and 3 emails at a minimum (the generator plants 3-5 of each edge case).
- **Action**: generalise the test - value has both cases, >= 3 upper-case letters remain after dropping lower-case, and the lower-case letters in order are a substring of a known label alias -> drop lower-case and recover. ~15 lines + tests. Medium cost, recommended.

### R6. Value on the line after `Label:`
- **Assumption**: `Label: Value` on one line (100 % of v2; only 3 empty-value lines, all placeholders).
- **Location**: `parse_doc.py` `LABEL_LINE`; an empty value -> `BLANK` -> `missing_value` escalation.
- **If it fails** (`Shipper:\nABC CO LTD`): all seven fields count as "missing" -> the whole email becomes `NEEDS_REVIEW/missing_value` -> **e2e miss + escalation false positive**, and systematically (every email).
- **Action**: when the value is empty and the next line is not a label line, take the next line as the value. ~6 lines + 1 test.

### R7. The rule branch for comparison requests has no LLM review
- **Assumption**: a body matching `COMPARE_PAT` (check/verify/confirm ... BL/documents) is always BL_COMPARISON.
- **Location**: `classify.py` returns 0.80 >= threshold - **the only non-attachment path that never goes to the LLM**.
- **If it fails** (an invoice email says "please confirm the documents for invoice 123"): misclassified as BL_COMPARISON, and with no attachments -> intent check -> most likely OK; one misclassification costs ~0.003 macro-F1.
- **Likelihood**: medium-low. None of the 75 v2 invoice emails match.
- **Action**: acceptable; or lower the no-attachment confidence to 0.79 so the LLM reviews it, at +91 calls per run. **Recommended once on Vertex.**

---

## P2 - awareness only, no action for now

### R8. `own_text()` ignores the subject entirely
- The rule layer never looks at the subject (only when the body is empty). If a final-round class had bodies like "See subject", the rules would be unsure -> LLM (which does see the subject). Covered, acceptable.

### R9. Quoted-mail truncation
- `QUOTED_PAT` cuts at `From:` / `_____` / `Original Message`. A body containing "From: Port Klang" mid-sentence would lose its tail. Not present in v2. Acceptable.

### R10. Word lists in the missing-attachment intent check
- `DOCS_REQUESTED_PAT` = send/provide/share/forward/resend/issue; `DOCS_IN_HAND_PAT` = attached/enclosed/compare/check/verify/confirm. Both hit or neither hit -> **escalate by default** (the safe direction: only esc_precision suffers, never final). Acceptable.

### R11. The SI_REQUEST semantic assumption
- Labelling the 95 "Please find Shipping instruction for X" emails SI_REQUEST was **inferred** from gold consistency, not defined by the organiser. Verified correct on v2; same generator in the final, low risk.

### R12. Weight tolerance 0.1 %
- The smallest planted weight defect in v2 is 500 kg (distribution: 500x3 / 1000x6 / 2000x3), far above the tolerance. A 10 kg difference in the final would be missed. Low likelihood - the generator uses round thousands/hundreds.

### R13. Port-name equivalence = token-set containment
- `PORT KLANG WESTPORT ⊇ PORT KLANG` is the same port. In theory `PORT SAID` vs `PORT` would match too - but a bare `PORT` never occurs. Acceptable.

### R14. Same name, different code -> MISMATCH
- `SINGAPORE (SGSIN)` vs `SINGAPORE (SGSIN)` is fine; two legitimate codes for the same port (rare) would false-alarm. Acceptable.

### R15. Rule-layer quality without the LLM
- `INVOICE_PAT` is hit by "3 Original invoice" inside SI bodies - the main reason R1 degrades to 0.58. Getting 0.8+ without an LLM would need the regex to exclude the "Documents Required" block. This is R1's fallback plan; unnecessary if Vertex is stable.

### R16. Time budget
- At `LLM_MIN_INTERVAL=6.5`, 520 emails take ~31 minutes; a re-run after a network drop resumes from cache. Zero on Vertex.

### R17. Scanned / image-only PDFs
- No OCR. An image PDF -> `office_to_text` empty -> `unreadable` escalation (safe direction). All 5 v2 unreadable cases were caught. If the final uses many scans, e2e falls but nothing false-alarms.

---

## Suggested order of work

| # | Item | Estimate | Needs |
|---|---|---|---|
| 1 | R1 Vertex smoke test | 10 min | `gcloud` login on this machine (you) |
| 2 | R2 content-fingerprint fallback for attachments | 20 min | - |
| 3 | R3 legal-qualifier rule | 15 min | - |
| 4 | R4 extend the port-code table | 10 min | - |
| 5 | R6 value on the next line | 10 min | - |
| 6 | R5 generalise the interleave fix | 20 min | - |
| 7 | Full regression: 51 tests + eval still 1.0 | 5 min | - |

After every step run `python scripts/eval.py .\data --server http://localhost:8080`; roll back immediately if any axis drops.
