# Service: vie-admin

LLM usage monitoring, system health dashboard, and cost alerting service.

## Overview

| Property | Value |
|----------|-------|
| Port | 8002 |
| Framework | FastAPI + React (multi-stage Docker) |
| Auth | Bearer: a login session token naming an active admin (signed with `ADMIN_SESSION_SECRET`), or `ADMIN_API_KEY` (machine key, GET only) |
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
| `models.py` | `UsageRecord` Pydantic model (28 fields incl. `cache_*`, `unit`/`audio_seconds` for transcription, and `refusal` / `refusal_category` / `fallback_from` for refusals) + `compute_cache_savings_usd(model, tokens)` (backed by `_CACHE_RATES_USD_PER_M`) and `compute_transcription_cost_usd(model, ...)` (backed by `_TRANSCRIPTION_RATES_USD` — Whisper $0.006/min, Gemini per-token) helpers |
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
| GET | `/usage/by-run` | Pipeline runs grouped by `request_id`, newest first; each run carries `regen_ordinal` (1 = first run, 2+ = regeneration), a best-effort `langfuse_url` (direct trace deep-link, null when unconfigured/unresolvable), `degraded` + `degraded_reasons` from the run's videoSummaryCache row (`partial_extraction`, `media_download_failed` — the runs panel badge reads `degraded · partial` / `degraded · media`), and child calls. Paginated (`days`, `limit`, `offset`). Rows missing `request_id` → "unattributed (legacy)" bucket |
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
`ADMIN_API_KEY`, plus a signed `X-Admin-Actor` naming the session's admin), backing
the DLQ panel on the Health page. `POST /queue/replay` is a mutation, so only a
named admin session may call it (the machine key gets `403`).

| Method | Path | Description |
|--------|------|-------------|
| GET | `/queue/stats` | Queue depth, consumer count, DLQ depth (`dlq.messages`) |
| GET | `/queue/dlq` | Peek dead-lettered messages (`limit` 1–100, default 20; non-destructive) |
| POST | `/queue/replay` | Body `{max: 1–500}` (default 100). Re-publishes up to `max` DLQ messages to `vie.pipeline.jobs`; returns vie-api's `{replayed}` |

Upstream translation: vie-api `401` → `500` ("vie-api rejected the admin
credentials" — a server-side misconfig such as `ADMIN_API_KEY` or
`ADMIN_SESSION_SECRET` differing between the services, the caller already proved admin); `503` (queue disabled /
no channel) passes through as `503`; other `≥500` → `502`; other `4xx` forwarded
as-is. The `detail` carries vie-api's JSON envelope `{error, message[, replayed]}`
unchanged when the body is JSON — a mid-drain `REPLAY_FAILED` keeps its partial
`replayed` count — and a generic `"vie-api error: <status>"` otherwise (raw
non-JSON bodies are never forwarded). A non-JSON 2xx body is a `502`. The call goes through the
shared `call_vie_api` client (below), so a vie-api timeout on `POST /queue/replay` is a `504`
saying the replay may have been applied, not a "vie-api unreachable" `502`: the drain can have run
before the response was lost, so look at the DLQ depth before replaying again.

The UI's "Replay all" uses the real DLQ depth from `/queue/stats` (not the 20-row
peek), capped at 500 per click, and refreshes stats + peek on **settle** (success
or failure), since a timeout can arrive after part of the batch was re-published.

### Videos (`/videos/`)

Thin authenticated proxy over vie-api's `DELETE /api/admin/videos/:youtubeId`
(`X-Admin-Key` from `ADMIN_API_KEY`, plus a signed `X-Admin-Actor` naming the
session's admin), backing the "Delete everywhere" button on the video detail page.
Only a named admin session may call it.

| Method | Path | Description |
|--------|------|-------------|
| DELETE | `/videos/{youtubeId}` | Body `{reason?}`; who purged is the session's admin, taken from the signed session, not from the body (a body `adminId` is ignored). Runs vie-api's global cascade: summarizer purge (Qdrant points, S3 `videos/<id>/`, Redis keys) then Mongo (`userVideos`, `shareLikes`, `shareViews`, `agentNotes`, `idempotencyKeys`, `videoSummaryCache`) and the `videoDeletions` and `adminAudit` audit rows. Returns vie-api's `{youtubeId, summaryIds, counts, warnings}`. |

Same client and upstream translation as the queue proxy (vie-api `401` → `500`, `≥500` →
`502` with the JSON envelope as `detail`, other `4xx` forwarded, a timeout → `504` "may have
been applied" with a 90 s read timeout for the cascade; a `403` means vie-api
found the admin no longer holds `videos:purge`). The body is strict (an unknown field is
`422`) and the reason is trimmed. A failed
summarizer purge surfaces as `502 SUMMARIZER_PURGE_FAILED` with nothing deleted
in Mongo, so the operator simply retries. `llm_usage` rows are kept by design,
so the detail page still shows the cost breakdown afterwards with `video: null`.

### Alerts (`/alerts/`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/alerts/recent` | Recent cost/anomaly alerts |
| GET | `/alerts/config` | Current alert thresholds (read straight from `llm_alert_config`) |
| POST | `/alerts/config` | Body `{cost_threshold_usd, daily_spike_multiplier, failure_rate_threshold, reason}` (strict, `reason` 1–500 characters required). Named admin session only. Proxied to vie-api's `POST /api/admin/alerts/config` (`alerts:write`), which writes the document and the `alerts.config.set` audit row; returns `{before, after, auditId}` |

vie-admin no longer writes thresholds itself: the write goes through vie-api so it is audited and
permissioned like every other admin mutation. The document shape (`_id: "default"`, snake_case fields)
is unchanged, so the evaluator below keeps reading it as before.

**Evaluator alerts** (`src/services/alert_evaluator.py`, polls `llm_usage` every 5 min and writes `llm_alerts` plus the `ALERT_WEBHOOK_URL` POST):

| Type | Severity | Fires when | Suppression |
|------|----------|------------|-------------|
| `daily_spend_spike` | warning | today's spend > `daily_spike_multiplier` × prorated 7-day baseline | 6 h cooldown |
| `daily_global_cost` | critical | Σ `cost_usd` (all users, since UTC midnight) ≥ `ALERT_DAILY_COST_THRESHOLD_USD` (default `20`; `<= 0` disables) | once per UTC day |
| `high_failure_rate` | critical | failure rate over the last hour > `failure_rate_threshold` (≥ 10 calls) | 1 h cooldown |
| `backup_stale` | critical | newest backup manifest older than `BACKUP_MAX_AGE_HOURS` | 6 h cooldown |

`daily_global_cost` is the operator's global spend alarm and nothing more: no request is blocked by global spend (vie-api's minutes ledger is the only gate on what a user can run; there is no per-user dollar cap). It sums the Python ledger schema (`cost_usd`, `timestamp`). It is suppressed per UTC day, not by a rolling window, so the first breach after midnight still alerts.

### Per-User Costs (`/users/`)

Backs the **Users** page in the admin dashboard. Spend is read from `llm_usage` (one row per
LLM call, attributed by `user_id`): nothing writes `userCosts` any more, so the list and the
cost timelines aggregate the ledger of record by user and UTC day (`src/services/user_costs.py`).
A run is counted per distinct summarizer `request_id`; assistant chat adds cost, not videos.
`userCosts` and `userCostAdjustments` are read-only history ([Data Models: userCosts](./DATA-MODELS.md#usercosts)):

- `creditAdjustmentUsd`, the retired dollar credit, exists only there, and the Adjustments tab reads `userCostAdjustments`;
- a day that has no attributed `llm_usage` rows keeps the history row's cost and video count, which covers the time before a run streamed over HTTP was attributed to its user. A day that has attributed rows takes its cost from them and only the credit from history, so nothing is counted twice.

A user is listed when they have spend in the range, a minutes account (`billingAccounts`), or
signed up in the range (a new user has neither yet, and the admin must still find them to grant
minutes). The **Plan** column is `billingAccounts.plan.id`, `free` when the user has no account row.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/users/costs?days=7&limit=50&offset=0` | Users by effective USD over the trailing window, highest first, with email/name (`users`) and `plan` |
| GET | `/users/{user_id}/costs?days=30` | One user's daily breakdown + last 20 audit rows |
| GET | `/users/{user_id}/activity?days=30` | User-360: videos generated (`userVideos` join), assistant LLM calls (`llm_usage` where `user_id`), and daily cost timeline — all in one payload |
| POST | `/users/{user_id}/grant-credit` | Grant minutes: body `{minutes (1–100000), reason (1–500), expiresAt?}`, proxied to vie-api's `POST /api/admin/billing/accounts/{id}/credits` as the session's admin. Answers vie-api's `{before, after, lotId, auditId}`. Named admin session only (the machine key gets `403`) |

`grant-credit` no longer writes anything here. It used to add USD to `userCosts` /
`userCostAdjustments` with a self-attested `adminId` in the body; vie-api's ledger is the only
writer of credit now, and the ledger entries and the `adminAudit` row carry the admin from the
signed session. The body is strict: the retired `amountUsd`, `adminId` and `date` fields are `422`.
`userCosts` and `userCostAdjustments` stay as read-only history (the cost views above); a test
scans the service source and fails if any write to them comes back.

### Billing Account (`/billing/`)

Read-only view of a user's minutes account, behind the **Account** tab in the Users
drawer. Reads the minutes-ledger collections that vie-api's `BillingService` writes
(see [Data Models — Billing Collections](./DATA-MODELS.md#billing-collections-minutes-ledger));
this router only calls `find` / `find_one` and never writes to them. The machine key and
named admin sessions can both call it.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/billing/accounts/{user_id}?limit=50&before=<iso>` | The account (plan, free-video window, plan minutes, spendable credit, caps, open holds), up to 100 credit lots (newest first), open holds, live recurring grants, and one page of the ledger (newest first) |

- **Errors:** `400` for an id that is not 24 hex characters or a `before` that is not an ISO 8601
  timestamp; `404` for an unknown user; `422` for `limit` outside 1–200.
- **Ledger paging:** pass the previous response's `ledgerNextBefore` as `before`; it is `null` on the
  last page. A page never ends inside a run of entries sharing one `createdAt`, so none are skipped.
- **No account yet:** vie-api creates `billingAccounts` rows lazily and vie-admin never writes, so a
  user who has not submitted a video gets `200` with `account: null` and empty lists.
- **Shape:** `AccountDetail` from `packages/types/src/billing.ts` plus `ledgerNextBefore` (ids hex, dates
  ISO 8601 with milliseconds). Fields vie-api omits when absent are `null` here. The UI can switch to
  vie-api's `GET /api/admin/billing/accounts/:userId` without changing.
- **Stored state, not rolled:** vie-api rolls an ended Pro cycle forward in memory before it answers;
  this view shows what is stored. A Pro cycle that ended with no request since reports `0` plan minutes
  remaining (its last totals stay visible) until the user's next request rolls it.
- **Constants:** the free-video window (1 video, 24 h, 30 min) mirrors `FREE_VIDEO` in
  `packages/types/src/billing.ts`; a test fails if they drift.

### Billing Actions and Audit (`/billing/…`, `/audit`)

Proxies to vie-api's admin billing routes (`routes/billing_actions.py`, shared plumbing in
`services/vie_api_client.py`). vie-api's `BillingService` is the only writer of the ledger and of
`adminAudit`; these routes check the request shape and forward it with the session admin's signed
`X-Admin-Actor`, so ledger entries and the audit row name the real admin and the reason. Every
mutation is a named admin session only (`403` for the machine key) and takes a `reason` of 1–500
characters; unknown fields such as `adminId` are `422`. Send one `Idempotency-Key` header per form
submission (8–128 of `A-Za-z0-9_-`) and it is forwarded: a retry after a timeout then applies once.

| Method | Path | Body | vie-api permission |
|--------|------|------|--------------------|
| POST | `/billing/accounts/{user_id}/plan` | `{planId: free\|pro, periodEnd?, reason}` | `plan:set` |
| POST | `/billing/accounts/{user_id}/caps` | `{maxVideoMinutes?, freeVideosPerDay?, maxConcurrent?, blocked?, reason}`; `null` clears a cap | `caps:set` |
| POST | `/billing/accounts/{user_id}/adjust` | `{bucket: plan\|credit, minutes (signed, not 0), reason}` | `billing:grant` |
| POST | `/billing/accounts/{user_id}/recurring-grants` | `{minutes, every: month, active, reason}` | `billing:grant` |
| DELETE | `/billing/accounts/{user_id}/recurring-grants/{grant_id}` | `{reason}` | `billing:grant` |
| GET | `/audit?targetId=&actorId=&limit=&before=` | | `audit:read` (the machine key may read). `before` is vie-api's opaque `nextBefore` cursor (`<ISO>_<id>`, up to 64 characters) |

Granting minutes is `POST /users/{user_id}/grant-credit` (above). vie-api answers are passed through
(`400` validation with its `{error, message}` envelope, `404`, `403` for a demoted admin, `409`
`ACCOUNT_BUSY`); a `504` means vie-api timed out and the change may have been applied, so reload the
account before retrying (or retry with the same `Idempotency-Key`). The same key with a different
request is `422 IDEMPOTENCY_KEY_REUSED`; vie-api's replay protection being down is `503`.

Every call vie-api receives from these proxies carries an `X-Request-ID` (fresh per call), which
vie-api stores on the audit row and which appears in both services' logs for the call. The CORS
allow-list names `Idempotency-Key`. (CORS sits behind the auth middleware, so a cross-origin
preflight would be `401`; the panel is served from the same origin, which is why that is harmless.)

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
- **Users** — cost table; the user drawer (`components/users/UserDetailDrawer.tsx`) has Overview, **Account**, Videos, Assistant and Adjustments (read-only USD history) tabs. The Account tab (`AccountPanel`) is:
  - a **Grant minutes** form (`GrantCreditForm`), always visible;
  - **action buttons** (`AccountActions`, in `AccountTab`'s `actions` slot): **Set plan** (free or Pro, optional term end, default 12 months), **Caps** (max video minutes, free videos per day, runs at once, block; a field left as it was is not sent, an emptied one clears the cap), **Adjust** (plan or credit bucket, signed minutes, never 0) and **Add recurring grant** (minutes a month, active), plus an **Audit log** link filtered to the user;
  - the read-only account view: plan and term, free-video status (ready or next at), plan minutes used and left, spendable credits with a lot table, open holds, recurring grants (each row has a **Delete** button), and the paged ledger with actor, reason and cost.
- **Audit** (`/audit-log`) — the admin action log, newest first: time, action, actor (email when recorded), target, reason, and a **Changes** toggle that shows only the fields that differ between the row's before and after snapshots (`updatedAt` and `version` are left out; they change on every write). Filter by target id or actor id (kept in the URL, so `/audit-log?targetId=<userId>` is linkable); **Load older entries** pages with the `nextBefore` cursor. The page path is not `/audit` because `GET /audit` is the API route.

Every Account action is a dialog (`ActionDialog`) that requires a **reason** (1–500 characters, recorded in the ledger and the audit log) and shows the server's own explanation when it refuses (`describeApiError`). Each submission carries an `Idempotency-Key`; resubmitting the identical request after a failure or timeout reuses it, so a change that did apply is not applied twice. The account (`['billing-account', userId]`) and the audit log are refetched after a success and after any failure that may have followed an applied change (a timeout, a 5xx, a dropped connection), so an uncertain outcome shows the real state; a 4xx refusal changed nothing and refetches nothing. While the request is in flight the dialog cannot be closed (Escape, the backdrop and Cancel are inert), so the operator sees the outcome before they can open a fresh dialog and a fresh key. Escape, Cancel or a click that starts and ends on the backdrop close it otherwise; focus moves into the dialog once when it opens, Tab stays inside, and focus returns to the button that opened it. A plan term end and a lot's expiry are shown in UTC, the zone they are entered in. The ranges the forms check (`CAP_LIMITS`, the 100000-minute and 500-character limits, the 600 Pro minutes) are copies of the server's; `lib/limits-drift.test.ts` reads the server sources and fails when one moves. A session that signed in with the machine key sees the `403` "A named admin session is required" message, since only named admin sessions may change anything.

The action and audit client is `ui/src/lib/billing-actions.ts` (`billingActions`, `auditApi`), with the form-to-request builders and their ranges in `lib/billing-forms.ts` and the before/after comparison in `lib/audit-diff.ts`. Grant minutes keeps `api.users.grantCredit` from `lib/api.ts`.

## Security

- **Two bearer credentials** (`Authorization: Bearer <…>`, checked by `ApiKeyMiddleware` in `src/auth.py`;
  the authenticated actor lands on `request.state.actor`, read by routes through `current_actor` /
  `current_user_actor`):
  - `ADMIN_API_KEY` — the machine key (curl, tooling; also what vie-admin sends vie-api as `X-Admin-Key`).
    It acts as `AdminActor(kind="machine")` and **may only read**: any method other than `GET` is `403`.
  - A session token from `POST /auth/login` (email + bcrypt password, user must be an active admin):
    `base64url(userId|email|expiry).hmac_sha256("session|" + payload)`, 12 h TTL, HMAC-keyed on **`ADMIN_SESSION_SECRET`** — never on
    `ADMIN_API_KEY`, so holding the machine key does not let anyone mint a session for an arbitrary user.
    Rotating `ADMIN_SESSION_SECRET` logs every admin out; rotating `ADMIN_API_KEY` leaves sessions alone
    (it must be rotated on vie-api at the same time). `ADMIN_SESSION_SECRET` also signs the `X-Admin-Actor`
    header sent to vie-api, so it must be the same value on both services (rotate and recreate both).
- **Live admin check** — the session is not trusted until expiry. On every request the middleware checks
  that the token's user is still an active admin (`src/roles.py`, cached per user for **60 s** in-process,
  clock injected in tests): a demoted or soft-deleted admin gets `401` within a minute, and a user who no
  longer exists likewise. If the users collection cannot be read the check fails closed (`503`). The rule
  mirrors vie-api's `effectiveRoles`: a `roles` array is authoritative (even empty), and only a user with no
  `roles` array falls back to the legacy `role: "admin"`; `deletedAt` set means not an admin. Both sides are
  tested against the same cases in `packages/types/test-vectors/admin-auth.json`. Login applies the same rule.
- **Actor propagation** — calls to vie-api for a named admin add `X-Admin-Actor: <userId>.<unixTs>.<hmac>`
  (`src/services/vie_api_client.py`): `hmac = HMAC-SHA256(ADMIN_SESSION_SECRET,
  "actor|METHOD|request-target|userId|unixTs|sha256(body)")` over the method, the exact request-target
  (path plus query, as put on the wire) and the exact body bytes sent. A header is therefore valid for that one
  request only; vie-api accepts it for five minutes, records the signature of every non-GET so it cannot be used
  twice, and re-checks that the user still holds the permission. The `actor|` and `session|` prefixes keep the two
  uses of the secret from standing in for each other. The same vectors file pins both implementations.
  Every non-GET route must depend on `current_user_actor`; `tests/test_authz_matrix.py` fails if one does not.
- **Logs** carry the user id, never the email (login success/rejection, rejected sessions).
- **Startup refusal** — the lifespan calls `validate_secrets()` (`src/config.py`) first. Outside a dev
  `ENVIRONMENT` (`""`, `development`, `dev`, `test`, `local`; any other name counts as production) the
  service refuses to start when `ADMIN_API_KEY` or `ADMIN_SESSION_SECRET` is empty or its dev default,
  when `ADMIN_SESSION_SECRET` is shorter than 32 characters (no length rule on `ADMIN_API_KEY`), or
  when the two are equal; the error names the variable, never its value. Dev environments only log a warning.
- Timing-safe comparison (`hmac.compare_digest`)
- OpenAPI docs disabled (`docs_url`/`openapi_url` are `None`)
- CORS restricted to known origins
- Input validation on all parameters (ObjectId, path params, date formats)
- TTLCache (30s) prevents aggregation query abuse

## MongoDB Collections

| Collection | TTL | Written By | Read By |
|-----------|-----|-----------|---------|
| `llm_usage` | none — financial ledger, retained indefinitely (a legacy 90-day TTL index is dropped at boot) | Callbacks (summarizer, assistant, worker) | Admin |
| `llm_alerts` | None | llm-common callback, admin evaluator, summarizer stall sweeper | Admin |
| `health_history` | 30 days | Admin health poller | Admin |
| `billingAccounts`, `ledgerEntries`, `creditLots`, `recurringGrants` | none | vie-api `BillingService` only | Admin (read-only, `/billing/`) |

## Environment Variables

```
# "" / development / dev / test / local = dev (dev-default secrets only warn);
# anything else = production (validate_secrets() refuses to start). Prod compose: production.
ENVIRONMENT=
# Machine key: raw Bearer on vie-admin + X-Admin-Key to vie-api. Required.
ADMIN_API_KEY=<required>
# Signs login session tokens and the X-Admin-Actor header sent to vie-api (vie-api reads the same
# value). openssl rand -hex 32; must differ from ADMIN_API_KEY; rotating it logs every admin out and
# needs vie-api recreated too. Default dev-admin-session-secret-change-me (dev only).
ADMIN_SESSION_SECRET=<required in production>
MONGODB_URI=mongodb://vie-mongodb:27017/video-insight-engine
VIE_API_URL=http://vie-api:3000
VIE_SUMMARIZER_URL=http://vie-summarizer:8000
VIE_ASSISTANT_URL=http://vie-assistant:8001
ALERT_COST_THRESHOLD_USD=0.50
# Global spend alarm: daily_global_cost alert once per UTC day at this total (USD). <= 0 disables.
ALERT_DAILY_COST_THRESHOLD_USD=20
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
