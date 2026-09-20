# The eval loop - Docker route (fixed commands)

## One-off: start the scoring service

> The docker package must be unzipped under **Documents or your user folder**, not in `/tmp` or
> any path Docker cannot share; otherwise the mount is empty and `/health` shows `emails: 0`.

```powershell
cd C:\Users\<you>\Documents\sdoc-hackathon-docker
docker compose up --build
```

The first run pulls images and takes a few minutes. Once the service is up, **keep that window open**.

## Verify the service is alive (in another terminal)

```powershell
curl http://localhost:8080/health
```

You must see `"emails": 520`. If it is `0`, the mount path is wrong - move the whole docker folder
under `C:\Users\<you>\` and start again.

## Run this after every code change

```powershell
cd C:\Users\<you>\Documents\averis
python scripts/eval.py .\data --server http://localhost:8080
```

It builds `submission.json` -> POSTs it to `/submit` -> gets the scores back ->
appends them to `evals\history.jsonl` -> **compares with the previous run and flags any axis that regressed**.

## Threats to validity, and what we do about them

1. **Adaptive overfitting.** 33 scoring queries over 26 code versions were made against the same 520-email set. Even black-box (aggregate score only), repeated querying leaks information (the "reusable holdout" problem). We do not claim the 1.0 generalises.
2. **Small samples.** The field-level golden set is n = 11 emails / 15 defect fields. Every such metric is reported with a Wilson 95 % interval (`shipdoc_core.evaluate.wilson_interval`): 15/15 → 0.80–1.00.
3. **Author bias.** The golden set and the hold-out set were annotated by the team that wrote the rules. The hold-out mitigates *tuning* bias (never used to change code), not annotation bias; an Averis-annotated sample would be the real test.

Counter-measures, in order of strength: the six perturbations (`docs/PERTURBATION_REPORT.md`), the ablation (`scripts/ablation.py`), and the hold-out protocol below.

## Hold-out protocol

- `scripts/holdout_build.py` writes `evals/holdout/` (41 emails, attachments, `gold.json`) deterministically. Gold is written from the business truth of each case at authoring time.
- `scripts/holdout_eval.py` runs the pipeline on it and scores with **our re-implementation** of the four axes (the organiser's formulas are not published); `final` is the unweighted mean. Rules-only by default (reproducible without credentials); `--llm` for the shipped hybrid.
- Results are committed as they come out (`evals/holdout_result*.json`, misses listed). **No code change is made in response to a hold-out result.** If a miss is fixed for other reasons, a new hold-out is authored before it is counted.

## Why Docker rather than score_cli

`/submit` returns only the scoreboard; `ground_truth` is never returned by any endpoint
(`REVEAL_GT` is off by default). This is **black-box scoring**, exactly how the organiser designed it -
we see the score, not the answers, so the system is forced to genuinely generalise.
The final round uses different data; this is what saves you.
