# Security notes (demo mode) and the production plan

## Public surface today

| Endpoint | Auth | What an anonymous caller gets |
|---|---|---|
| `GET /` , `GET /static/*` | none | demo page, fonts |
| `GET /health` | none | status, version, provider names; no project id, no internal counters. `?deep=1` makes one LLM call |
| `POST /process` | none, 10 / min / IP | the decision and evidence for the email *they* sent. `email_id` may **not** start with `email_` (reserved namespace, HTTP 422) unless a valid `X-API-Key` is presented |
| `GET /report/{id}`, `GET /reports` | none | decisions and evidence with the sender masked (`a***@domain`); review shows `reviewer` (masked if it is an address), `identity.method`, `identity.verified`, `identity.email` masked; never a traceback or a request id |
| `GET /batch/{id}` | none | per-email status of a batch (unguessable id) |
| `POST /batch`, `GET /failures*`, `POST /failures/{key}/retry`, `POST /admin/chaos` | `X-API-Key` (Secret Manager) | original inputs and tracebacks live only behind the key |
| `POST /report/{id}/review` | review token (review-only) or Google OIDC bearer | the audit trail; with OIDC the reviewer is the token's verified email |
| `POST /tasks/process` | OIDC (Cloud Tasks) or `X-API-Key` | internal |

## Rules

- **Reserved namespace.** `email_*` is the organiser's inbox. Anonymous `POST /process` cannot write into it, so the
  console's inbox (`/reports?prefix=email_`, 520 rows) and `/report/email_NNN` cannot be shadowed by a visitor.
- **Masking.** Every public response masks mailbox addresses to first character + `***` + domain. Stored documents
  are unchanged.
- **Retention.** Anonymous `POST /process` documents carry `expire_at = now + 24 h`; a Firestore TTL policy on the
  `reports` collection group (`expire_at`) removes them. Documents written through `/batch` (the dataset) or with
  the API key carry no `expire_at` and are kept.
- **Credentials.** No API key in the cloud for the LLM (service-account ADC); `API_TOKEN` and `REVIEW_TOKEN` come
  from Secret Manager; the review token cannot batch, retry or toggle chaos.
- **Tracing.** `X-Request-Id` on every response; JSON log line per request; the id is stored on the report but not
  shown publicly.

## Production plan (not implemented)

- Every read behind an identity (Google OIDC / IAP or the customer's IdP); no anonymous endpoints.
- Tenant isolation: one Firestore namespace (or database) per customer; the reserved-prefix rule becomes a per-tenant
  namespace.
- Immutable audit log: review entries appended to a write-once collection with the verified identity, never updated
  in place; reviewer identity derived from the token only.
- Retention by contract, not a demo TTL; attachments encrypted at rest with customer-managed keys.
