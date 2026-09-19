# review_reason and review_detail

`review_reason` in the submission is the organiser's four-value enum and is **never extended**:
`wrong_doc_type | missing_attachment | unreadable | missing_value`. It is what the scorer reads.

`review_detail` (in `/process` and `/report` responses, `evidence.review_detail`) carries the concrete
cause in plain language. It is never written to `submission.json`.

| review_reason | concrete causes that map to it (review_detail examples) |
|---|---|
| `missing_attachment` | body asks for a comparison but neither SI nor BL is attached; body asks for a comparison but the BL (or SI) is not attached |
| `unreadable` | an attachment exists but no text could be read from it; SI/BL attachment could not be parsed (empty, garbled or image-only PDF) |
| `wrong_doc_type` | attachments present but none is recognised as an SI or a BL (e.g. a packing list); attachment tagged SI is a BL |
| `missing_value` | field blank or placeholder on SI/BL (`N/A`, `TBA`, `???`, empty); a comparison verdict the core cannot make: port undecidable (no name, unknown code), company-name similarity in the grey zone 0.75-0.94, count or weight unparseable; a field verdict whose confidence (extraction x comparison) is below the review threshold 0.62 |

Why `missing_value` covers comparator escalations: the organiser's enum has no value for "the comparison
could not be decided"; `missing_value` (a value the pipeline could not use) is the closest official meaning.
The true cause is always present in `review_detail`, so nobody has to guess from the enum.

## Weight tolerance policy (see also compare.py)

A gross-weight difference within `max(0.5 kg, 0.1 % of the SI weight)` is treated as a formatting /
rounding difference, not a defect. This is **our engineering judgement**, not a requirement of the brief:
it absorbs unit conversion (MT, LBS) and rounding while every planted defect in the v2 data is >= 500 kg.
The verdict reason states the difference and the tolerance explicitly, e.g.
`diff 10.00 kg, policy tolerance 22.00 kg (0.1 %, min 0.5 kg) -> treated as a match`.
