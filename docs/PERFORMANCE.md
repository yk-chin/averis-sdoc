# Performance

Measured 2026-09-20 with `scripts/bench.py` (standard library only); raw numbers in `evals/bench.json`.
Machine for the local rows: the development laptop (Windows 11, Python 3.12). Cloud rows: the live Cloud Run
service in asia-southeast1 measured from Malaysia (network round trip included).

## Pipeline, in process (520 organiser emails, end to end)

| Configuration | Total | Per email | Throughput |
|---|---|---|---|
| Rules only (LLM and vision off) | 5.41 s | 10.4 ms | 96 emails/s |
| Hybrid, LLM answers served from the content-hash cache | 3.79 s | 7.3 ms | 137 emails/s |

The deterministic core is not the bottleneck; a cold LLM call is (≈ 0.8–2 s per uncached classification on
`gemini-3.5-flash-lite`, 288 of 520 emails need one on the first pass, none on the second).

## API, local (`POST /process`, rules-only email, `RATE_LIMIT_PER_MIN` raised for the test)

| Concurrency | p50 | p95 | p99 | Throughput | Errors |
|---|---|---|---|---|---|
| 1 | 17 ms | 34 ms | 43 ms | 51 req/s | 0 |
| 8 | 36 ms | 50 ms | 56 ms | 215 req/s | 0 |
| 20 | 102 ms | 135 ms | 141 ms | 195 req/s | 0 |

Single uvicorn worker; the service is CPU-bound on JSON + regex, saturating around 200 req/s per core.
(A first run against `localhost` showed a flat 2.06 s per request: Windows tries IPv6 `::1` first and falls back.
Measure against `127.0.0.1`; the number above is the API's.)

## API, Cloud Run (1 vCPU / 1 GiB, concurrency 20, `min-instances=1` during judging)

| Endpoint | n | p50 | p95 | p99 |
|---|---|---|---|---|
| `GET /health` | 30 | 85 ms | 170 ms | 502 ms |
| `GET /report/{id}` (Firestore read) | 30 | 98 ms | 137 ms | 144 ms |
| `GET /reports?limit=1000` (520 docs, filtered in Python) | 30 | 608 ms | 696 ms | 707 ms |
| `POST /process` (rules-only email) | 5 | 110 ms | 129 ms | 129 ms |

Network round trip Malaysia → Singapore is ≈ 70–90 ms of every row. `/reports` reads all 520 documents on each
call; the console caches it for 15 s (ISR), so users do not pay it per page view. A projection (`select()`) or a
summary document would bring it under 200 ms if the inbox grows.

## Batch (Cloud Tasks)

Loading the 520 organiser emails: three batches, 0 failures, ≈ 8 minutes wall clock at
`max-concurrent-dispatches=3` (≈ 65 emails/min). That limit is deliberate: it keeps 288 first-time Vertex calls
under the model's rate limit; the 3-attempt / dead-letter policy is the safety net if the limit is hit anyway.

## Cold start

Before `min-instances=1`, a cold request to Cloud Run took 3–6 s (Python + FastAPI + google-cloud clients import);
warm requests are the table above. `min-instances=1` is set for the judging window (docs/DEPLOY.md).

## Scaling path

**`/reports` (today: one ordered read of every document, filtered in Python; 608 ms at 520 docs).**
Move the filters server-side: `where(category ==)`, `where(status ==)` with a composite index
`(category, status, updated DESC)` — plus `(status, updated DESC)` for the queues page — and cursor pagination with
`start_after(last_updated)` at a page size the console actually renders (50). Counts for the header tiles come from
Firestore's `count()` aggregation query, not from reading rows. **Not** a single summary document: Firestore sustains
roughly one write per second per document, so a shared counter touched by every task would contend during a batch
(200 tasks in a few minutes); if pre-aggregation is ever needed, use sharded counters (N shards, sum on read).
Expected: sub-100 ms per page independent of inbox size.

**Cloud Tasks dispatch (today: `max-concurrent-dispatches=3`, chosen so 288 first-time Vertex calls stay under the
model's rate limit).** The right knob is the rate: `max_dispatches_per_second ≈ 0.8 × Q / 60`, where Q is the model's
requests-per-minute quota (0.8 leaves headroom for retries and the console's own calls). The useful concurrency then
follows from Little's law, `L = λ × W`: at λ = 2 requests/s and W ≈ 1.5 s per uncached call, L ≈ 3 in flight — which
is why 3 was the correct setting for the current quota, and why it scales linearly with Q. For guaranteed capacity
(a customer SLA rather than a shared quota), Vertex AI Provisioned Throughput reserves model capacity per project;
the dispatch rate is then set from the reserved throughput instead of the shared limit.

**Pipeline compute** is not on the critical path: 7–10 ms per email in process, ≈ 200 req/s per Cloud Run vCPU;
horizontal scaling is `max-instances`, and the deterministic core is stateless.
