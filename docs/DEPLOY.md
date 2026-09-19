# Deployment: Cloud Run (asia-southeast1) + Vertex AI

Service: `https://shipdoc-api-705106212012.asia-southeast1.run.app`　Project: `eco-world-296707`

| Component | Value |
|---|---|
| Image | `Dockerfile`, `python:3.12-slim`, non-root (uid 10001), built from source by Cloud Build |
| Runtime identity | Service account `shipdoc-run@eco-world-296707.iam.gserviceaccount.com`: `roles/aiplatform.user` + `roles/secretmanager.secretAccessor` |
| LLM | `LLM_PROVIDER=vertex`, `GCP_LOCATION=global` (the Singapore region lacks some models), chain `gemini-3.5-flash-lite;gemini-3.5-flash;gemini-3.6-flash`. **No API key anywhere in the cloud** - authentication is the service account's ADC |
| Secret | `API_TOKEN` <- Secret Manager `shipdoc-api-token:latest`; required as `X-API-Key` on `POST /batch`, `POST /failures/{key}/retry`, `POST /admin/chaos` only. `POST /process` is anonymous and rate-limited |
| Sizing | 1 vCPU / 1 GiB / concurrency 20 / timeout 300 s / 0-3 instances |
| Upload scope | `.gcloudignore`: only `api/ pipeline/ shipdoc_core/ Dockerfile requirements.txt` - **no data/ .env .cache/** |
| Async batch | Cloud Tasks queue `shipdoc-process` (asia-southeast1): `max-attempts=3`, `min-backoff=5s`, `max-backoff=60s`, `max-doublings=3`. Tasks call `POST /tasks/process` with an OIDC token for the service account; the handler verifies audience + email |
| Persistence | Firestore native `(default)` in asia-southeast1: collections `reports`, `jobs`, `dead_letter`, `settings`. Composite indexes: `dead_letter(status asc, updated desc)`, `reports(email_id asc, updated desc)` (the store falls back to in-memory filtering until they are built) |
| Extra SA roles | `roles/cloudtasks.enqueuer`, `roles/datastore.user`, `roles/iam.serviceAccountUser` on itself (to mint OIDC tokens for tasks) |

## Endpoints

| | Auth | Description |
|---|---|---|
| `GET /` | - | Demo page, works on a phone |
| `GET /static/*` | - | Self-hosted Source Sans 3 (`api/static/fonts/`, OFL 1.1) for the demo page |
| `GET /health` | - | Liveness and auth summary; no project id, model chain or internal counters. `?deep=1` makes one real LLM call |
| `POST /process` | - (rate-limited: `RATE_LIMIT_PER_MIN`, default 10, per client IP) | One email -> `decision` (the submission record) + `evidence` (rule/LLM classification basis, parsed attachments, the seven `FieldResult`s, readable report) |
| `POST /batch` | X-API-Key | `{"emails":[...]}`, <= 200. One Cloud Tasks task per email; returns `batch_id` immediately. Same email (email_id + content hash) already queued/done -> `duplicate`, not re-processed |
| `GET /batch/{batch_id}` | - | Per-email status (QUEUED / PROCESSING / RETRYING / DONE / FAILED), attempts, errors |
| `GET /failures` | X-API-Key | Dead-letter queue: emails that failed all 3 attempts, with reason and attempts (`?all=1` includes RECOVERED) |
| `GET /failures/{key}` | X-API-Key | One dead-letter item with the original input and traceback |
| `POST /failures/{key}/retry` | X-API-Key | Re-enqueue from the stored input (`clear_fault` strips demo fault injection). Success marks the item RECOVERED |
| `POST /admin/chaos?enabled=true\|false` | X-API-Key | Demo switch: every task attempt fails while enabled |
| `POST /tasks/process` | OIDC (Cloud Tasks) or X-API-Key | One attempt of one email; 503 asks Cloud Tasks to retry, 200 on success or after dead-lettering |
| `GET /report/{id}` | - | Result by idempotency key or email_id, persisted in Firestore; includes `review` (if any) and `effective_decision` |
| `POST /report/{id}/review` | X-API-Key | Human review: `{decision: confirmed|corrected, corrected_fields?, reviewer_note?, reviewer}` -> audit trail stored under `reports/{key}.review`; AI decision never overwritten; not used for submission.json |

Request body: `{"email_id","from","subject","body","attachments":[{"name","content_base64"} or {"name","text"}]}`.
Attachment names containing `_SI.`/`_BL.` are routed by name, otherwise by content fingerprint.

## Redeploy (after a code change)

```powershell
gcloud run deploy shipdoc-api --source . --region asia-southeast1 --platform managed --allow-unauthenticated `
  --service-account shipdoc-run@eco-world-296707.iam.gserviceaccount.com `
  --set-env-vars "LLM_PROVIDER=vertex,GCP_PROJECT=eco-world-296707,GCP_LOCATION=global,GEMINI_MODEL=gemini-3.5-flash-lite;gemini-3.5-flash;gemini-3.6-flash,LLM_MIN_INTERVAL=0,APP_VERSION=<git sha>" `
  --set-secrets API_TOKEN=shipdoc-api-token:latest `
  --memory 1Gi --cpu 1 --concurrency 20 --timeout 300 --min-instances 0 --max-instances 3
```

Rotate the token: `gcloud secrets versions add shipdoc-api-token --data-file=<file>`, then redeploy (or `gcloud run services update shipdoc-api --update-secrets API_TOKEN=shipdoc-api-token:latest`).

## Running the same code locally

`.env` with `LLM_PROVIDER=aistudio` + `GEMINI_API_KEY`; `python -m uvicorn api.main:app --port 8090`. Without `API_TOKEN` the POST endpoints are open.

## Known limitations

- No OCR: scanned PDFs are escalated as `unreadable`.
- The LLM cache `.cache/` lives inside the container and is cleared when the instance is recycled (each cold start pays again).

## Environment (Cloud Run)

`TASKS_MODE=cloud` `TASKS_QUEUE=shipdoc-process` `TASKS_LOCATION=asia-southeast1` `TASKS_SA=shipdoc-run@eco-world-296707.iam.gserviceaccount.com` `STORE=firestore` `SERVICE_URL=<service url>` in addition to the LLM variables. Locally the defaults are `TASKS_MODE=inline` (a thread with the same 3-attempt policy) and `STORE=memory`.

One-off resources:

```powershell
gcloud services enable cloudtasks.googleapis.com firestore.googleapis.com
gcloud firestore databases create --location=asia-southeast1 --type=firestore-native
gcloud tasks queues create shipdoc-process --location=asia-southeast1 --max-attempts=3 --min-backoff=5s --max-backoff=60s --max-doublings=3
gcloud firestore indexes composite create --collection-group=dead_letter --field-config=field-path=status,order=ascending --field-config=field-path=updated,order=descending
gcloud firestore indexes composite create --collection-group=reports --field-config=field-path=email_id,order=ascending --field-config=field-path=updated,order=descending
```

## Demo script: failure -> dead letter -> retry -> success

1. Open the service URL, paste the API key (batch only - single-email processing in section 1 needs none), click **Send demo batch**: three emails - `healthy`, `flaky` (`fail_times=2`, fails twice then succeeds on attempt 3) and `broken` (`fail_times=99`).
2. Watch the table: attempts climb 1 -> 2 -> 3 with the queue's backoff (~5 s, ~10 s); `broken` ends **FAILED** and appears in the dead-letter table with its reason.
3. Click **Send the same batch again**: `duplicates` - nothing is re-processed (idempotency).
4. Click **Retry** on the dead-letter row: it re-enqueues from the stored input (fault cleared) and turns **DONE** within seconds; the item shows **RECOVERED** under "show recovered too".
5. Optional: **Chaos ON** makes every attempt fail (simulated outage); **Chaos OFF** then **Retry** recovers them.
