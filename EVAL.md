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

## Why Docker rather than score_cli

`/submit` returns only the scoreboard; `ground_truth` is never returned by any endpoint
(`REVEAL_GT` is off by default). This is **black-box scoring**, exactly how the organiser designed it -
we see the score, not the answers, so the system is forced to genuinely generalise.
The final round uses different data; this is what saves you.
