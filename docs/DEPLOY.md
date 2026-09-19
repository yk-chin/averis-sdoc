# Deployment: Cloud Run (asia-southeast1) + Vertex AI

Service: `https://shipdoc-api-705106212012.asia-southeast1.run.app`　Project: `eco-world-296707`

| Component | Value |
|---|---|
| Image | `Dockerfile`, `python:3.12-slim`, non-root (uid 10001), built from source by Cloud Build |
| Runtime identity | Service account `shipdoc-run@eco-world-296707.iam.gserviceaccount.com`: `roles/aiplatform.user` + `roles/secretmanager.secretAccessor` |
| LLM | `LLM_PROVIDER=vertex`, `GCP_LOCATION=global` (the Singapore region lacks some models), chain `gemini-3.5-flash-lite;gemini-3.5-flash;gemini-3.6-flash`. **No API key anywhere in the cloud** - authentication is the service account's ADC |
| Secret | `API_TOKEN` <- Secret Manager `shipdoc-api-token:latest`; POST endpoints require `X-API-Key` |
| Sizing | 1 vCPU / 1 GiB / concurrency 20 / timeout 300 s / 0-3 instances |
| Upload scope | `.gcloudignore`: only `api/ pipeline/ shipdoc_core/ Dockerfile requirements.txt` - **no data/ .env .cache/** |

## Endpoints

| | Auth | Description |
|---|---|---|
| `GET /` | - | Test page, works on a phone |
| `GET /health` | - | `?deep=1` makes one real LLM call to verify Vertex authentication |
| `POST /process` | X-API-Key | One email -> `decision` (the submission record) + `evidence` (rule/LLM classification basis, parsed attachments, the seven `FieldResult`s, readable report) |
| `POST /batch` | X-API-Key | `{"emails":[...]}`, <= 200 |
| `GET /report/{email_id}` | - | Most recent result; lives in instance memory, 404 once the instance is recycled |

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

- `/report` lives in instance memory only; it is lost with multiple instances or scale-to-zero. Persistence (Firestore/GCS) is not implemented.
- No OCR: scanned PDFs are escalated as `unreadable`.
- The LLM cache `.cache/` lives inside the container and is cleared when the instance is recycled (each cold start pays again).
