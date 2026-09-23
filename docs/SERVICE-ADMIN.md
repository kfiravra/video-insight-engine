# Service: vie-admin

LLM usage monitoring, system health dashboard, and cost alerting service.

## Overview

| Property | Value |
|----------|-------|
| Port | 8002 |
| Framework | FastAPI + React (multi-stage Docker) |
| Auth | API key (Bearer token) |
| Database | MongoDB (shared with other services) |

## Architecture

```
┌─────────────────────────────┐
│      vie-admin :8002        │
│  ┌───────────┐ ┌──────────┐│
│  │  FastAPI   │ │ React UI ││
│  │  Backend   │ │ (static) ││
│  └─────┬─────┘ └──────────┘│
│        │                    │
│  Reads from MongoDB:        │
│  • llm_usage (no TTL)       │
│  • llm_alerts               │
│  • health_history (30d TTL) │
│        │                    │
│  Polls (30s): /health on    │
│  api/summarizer/assistant,  │
│  Mongo ping, Qdrant /readyz,│
│  Redis+RabbitMQ via api     │
│  /ready, worker via queue   │
│  consumers                  │
│  Proxies vie-api            │
│  /api/admin/queue/* (DLQ)   │
└─────────────────────────────┘
```

## Shared Package: packages/llm-common

Python package providing automatic LLM call tracking via LiteLLM callbacks.

| Module | Purpose |
|--------|---------|
| `models.py` | `UsageRecord` Pydantic model (25 fields incl. `cache_*`, plus `unit`/`audio_seconds` for transcription) + `compute_cache_savings_usd(model, tokens)` (backed by `_CACHE_RATES_USD_PER_M`) and `compute_transcription_cost_usd(model, ...)` (backed by `_TRANSCRIPTION_RATES_USD` — Whisper $0.006/min, Gemini per-token) helpers |
| `context.py` | `ContextVar`s for feature, request_id, video_id, user_id, video_summary_id |
| `buffer.py` | `SyncBuffer` (threading.Timer) + `AsyncBuffer` (asyncio). Flushes every 5 s or 50 rows. A failed flush **retains** its rows (bounded to 500, oldest shed and counted in `buffer_flush_failed`) and suppresses size-triggered flushes for one interval so a Mongo outage doesn't turn every `add()` into an inline blocking `insert_many` — the periodic flush keeps retrying |
| `callback.py` | `MongoDBUsageCallback(CustomLogger)` with cost alerting; `register_active_buffer` + `record_manual_usage(record)` for out-of-band emits (transcription bypasses LiteLLM) |

Registered in:
- `services/summarizer/src/main.py` (sync mode)
- `services/assistant/src/server.py` (async mode)

## API Endpoints

### Usage Analytics (`/usage/`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/usage/stats` | Aggregated totals (filters: days, feature, provider, service) |
| GET | `/usage/daily` | Daily breakdown for time-series charts |
| GET | `/usage/by-feature` | Cost per feature, sorted desc |
| GET | `/usage/by-model` | Cost per model, sorted desc |
| GET | `/usage/by-service` | Cost per service |
| GET | `/usage/by-video` | Top videos by total cost (latest cache version only — `isLatest` lookup avoids duplicate rows from versioned/dedup'd docs) |
| GET | `/usage/by-run` | Pipeline runs grouped by `request_id`, newest first; each run carries `regen_ordinal` (1 = first run, 2+ = regeneration), a best-effort `langfuse_url` (direct trace deep-link, null when unconfigured/unresolvable), and child calls. Paginated (`days`, `limit`, `offset`). Rows missing `request_id` → "unattributed (legacy)" bucket |
| GET | `/usage/video/{video_id}` | Per-video feature breakdown |
| GET | `/usage/anomalies` | Expensive calls above threshold |
| GET | `/usage/recent` | Cursor-based pagination (before_id) |
| GET | `/usage/duplicates` | Group by prompt_hash, find duplicates |

### Health Monitoring (`/health/`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Basic health check (no auth required) |
| GET | `/health/services` | Current health of all services |
| GET | `/health/overview` | Aggregated system status — `healthy` / `degraded` (any timeout/degraded) / `down` (any down) / `unknown` (no poll yet) |
| GET | `/health/history` | Historical health snapshots |
| GET | `/health/uptime` | Uptime percentage calculation |

`redis` and `rabbitmq` are observed only through vie-api's `GET /ready`. When
vie-api itself is unreachable (or answers non-JSON) they report `unknown` with
`error: "vie-api /ready unreachable: …"` — not `down` — so an api restart is not
charged against their uptime in `health_history`. A vie-api timeout still
reports them as `timeout`; `rabbitmq` is `unknown` when `USE_QUEUE_PIPELINE=false`
omits it from `/ready`.

### Queue (`/queue/`)

Thin authenticated proxy over vie-api's `/api/admin/queue/*` (`X-Admin-Key` from
`ADMIN_API_KEY`), backing the DLQ panel on the Health page.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/queue/stats` | Queue depth, consumer count, DLQ depth (`dlq.messages`) |
| GET | `/queue/dlq` | Peek dead-lettered messages (`limit` 1–100, default 20; non-destructive) |
| POST | `/queue/replay` | Body `{max: 1–500}` (default 100). Re-publishes up to `max` DLQ messages to `vie.pipeline.jobs`; returns vie-api's `{replayed}` |

Upstream translation: vie-api `401` → `500` ("vie-api rejected admin key" — a
server-side misconfig, the caller already proved admin); `503` (queue disabled /
no channel) passes through as `503`; other `≥500` → `502`; other `4xx` forwarded
as-is. The `detail` carries vie-api's JSON envelope `{error, message[, replayed]}`
unchanged when the body is JSON — a mid-drain `REPLAY_FAILED` keeps its partial
`replayed` count — and a generic `"vie-api error: <status>"` otherwise (raw
non-JSON bodies are never forwarded). A non-JSON 2xx body is a `502`.

The UI's "Replay all" uses the real DLQ depth from `/queue/stats` (not the 20-row
peek), capped at 500 per click, and refreshes stats + peek on **settle** (success
or failure), since a timeout can arrive after part of the batch was re-published.

### Videos (`/videos/`)

Thin authenticated proxy over vie-api's `DELETE /api/admin/videos/:youtubeId`
(`X-Admin-Key` from `ADMIN_API_KEY`), backing the "Delete everywhere" button on
the video detail page.

| Method | Path | Description |
|--------|------|-------------|
| DELETE | `/videos/{youtubeId}` | Body `{reason?, adminId?}` (`adminId` is forwarded as `x-admin-id` for the audit row, self-attested). Runs vie-api's global cascade: summarizer purge (Qdrant points, S3 `videos/<id>/`, Redis keys) then Mongo (`userVideos`, `shareLikes`, `shareViews`, `agentNotes`, `idempotencyKeys`, `videoSummaryCache`) and a `videoDeletions` audit row. Returns vie-api's `{youtubeId, summaryIds, counts, warnings}`. |

Same upstream translation as the queue proxy (vie-api `401` → `500`, `≥500` →
`502` with the JSON envelope as `detail`, other `4xx` forwarded). A failed
summarizer purge surfaces as `502 SUMMARIZER_PURGE_FAILED` with nothing deleted
in Mongo, so the operator simply retries. `llm_usage` rows are kept by design,
so the detail page still shows the cost breakdown afterwards with `video: null`.

### Alerts (`/alerts/`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/alerts/recent` | Recent cost/anomaly alerts |
| GET | `/alerts/config` | Current alert thresholds |
| POST | `/alerts/config` | Update alert thresholds |

### Per-User Costs (`/users/`)

Backs the **Users** page in the admin dashboard. Reads `userCosts` (per-user
daily aggregates) and `userCostAdjustments` (admin audit log). See
[Data Models — userCosts](./DATA-MODELS.md#usercosts).

| Method | Path | Description |
|--------|------|-------------|
| GET | `/users/costs?days=7&limit=50&offset=0` | Top users by effective USD over the trailing window (joins `users` for email/name/tier) |
| GET | `/users/{user_id}/costs?days=30` | One user's daily breakdown + last 20 audit rows |
| GET | `/users/{user_id}/activity?days=30` | User-360: videos generated (`userVideos` join), assistant LLM calls (`llm_usage` where `user_id`), and daily cost timeline — all in one payload |
| POST | `/users/{user_id}/grant-credit` | Apply a signed adjustment; `amountUsd > 0` grants credit (stored as negative), `< 0` is a manual charge. Validated: `|amountUsd| ≤ 1000`, `reason` 1–500 chars, optional `date` (`YYYY-MM-DD`) |

**Audit caveat:** the admin service authenticates with a shared
`ADMIN_API_KEY`, so `adminId` in the grant-credit request body is
self-attested. Acceptable for a single-admin team; swap for per-admin
credentials if you need a forgery-resistant audit log.

## Frontend

React SPA served as static files from the same container.

| Technology | Version |
|------------|---------|
| React | 19.x |
| React Router | 7.x |
| TanStack Query | 5.x |
| Recharts | 3.x |
| Tailwind CSS | 4.x |

### Pages

- **Dashboard** — StatsCards, ServiceHealth, CostChart, FeatureBreakdown, AlertsBanner
- **Usage** — CostChart, ModelBreakdown, FeatureBreakdown, RecentCalls
- **Health** — ServiceHealth, 7-day uptime grid
- **Alerts** — Alert thresholds, recent alerts table

## Security

- API key auth via `Authorization: Bearer <key>` header
- Timing-safe comparison (`hmac.compare_digest`)
- OpenAPI docs disabled in production
- CORS restricted to known origins
- Input validation on all parameters (ObjectId, path params, date formats)
- TTLCache (30s) prevents aggregation query abuse

## MongoDB Collections

| Collection | TTL | Written By | Read By |
|-----------|-----|-----------|---------|
| `llm_usage` | none — financial ledger, retained indefinitely (a legacy 90-day TTL index is dropped at boot) | Callbacks (summarizer, assistant, worker) | Admin |
| `llm_alerts` | None | llm-common callback, admin evaluator, summarizer stall sweeper | Admin |
| `health_history` | 30 days | Admin health poller | Admin |

## Environment Variables

```
ADMIN_API_KEY=<required>
MONGODB_URI=mongodb://vie-mongodb:27017/video-insight-engine
VIE_API_URL=http://vie-api:3000
VIE_SUMMARIZER_URL=http://vie-summarizer:8000
VIE_ASSISTANT_URL=http://vie-assistant:8001
ALERT_COST_THRESHOLD_USD=0.50
# Langfuse — read-only, only to build "Open in Langfuse" run deep-links.
# Project id auto-resolved from the keys when LANGFUSE_PROJECT_ID is blank.
# All optional: unset → /usage/by-run simply returns langfuse_url=null.
LANGFUSE_BASE_URL=https://cloud.langfuse.com
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
LANGFUSE_PROJECT_ID=
```

## Testing

```bash
# Backend tests
cd services/admin && python3 -m pytest tests/ -v

# Frontend unit tests
cd services/admin/ui && npx vitest run

# E2E tests (3 viewports: desktop, tablet, mobile)
cd services/admin/ui && npx playwright test
```
