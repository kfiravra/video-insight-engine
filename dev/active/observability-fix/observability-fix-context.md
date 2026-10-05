# Observability Fix — Context

Last Updated: 2026-08-26

## Why this task exists
User got "lost" about what observability exists. Audit 2026-08-20 mapped it (artifact https://claude.ai/code/artifact/5a6acead-8f0c-439b-986d-fb8e228ec776) and found the system strong but half-activated. This task executes the artifact's §06 "Recommended path".

## Debugging playbook (already works today — pivot key is request id)
1. Get id: `x-request-id` on every response; also admin Usage → Pipeline Runs, Langfuse trace tags, queue payload.
2. `./scripts/find-request.sh <id>` — greps vie-api / vie-summarizer / worker / vie-assistant logs in one shot.
3. Langfuse → Traces → tag `requestId:<id>`: cost, latency, `cacheHit`, `attempt>1` = retries, `promptVersions`, faithfulness < 0.7 = quality suspect.
4. Sentry → tag `requestId:<id>`. Money: Mongo `llm_usage` by `request_id`.

Symptom → move: stuck job → admin Health → Queue (consumers=0 → worker down; DLQ>0 → `GET /api/admin/queue/dlq`, `POST /api/admin/queue/replay`), grep "Producer lock disappeared without DONE". SSE died → errors are HTTP 200 in-band frames; grep `Broker publish failed`. Missing cost rows → grep `worker_llm_usage_callback_registered`, `manual_usage_no_buffer`, `buffer_flush_failed`. Langfuse empty → boot log `langfuse_init enabled=`, then `scripts/activate_langfuse.sh`. Prompt edit ignored → registry label `production` beats local .txt; `docker compose run --rm vie-langfuse-init`.

## Key files
| Area | File |
|---|---|
| Alert evaluator (5-min loop) | `services/admin/src/services/alert_evaluator.py`; loops started in `services/admin/src/main.py:115-116` |
| Health poller (30s, `health_history`) | `services/admin/src/services/health_checker.py:80` |
| Admin config | `services/admin/src/config.py` (`ALERT_WEBHOOK_URL` line 24) |
| llm-common alerts / usage callback / buffer | `packages/llm-common/.../alerts.py`, `callback.py:54` ($0.50), `buffer.py:61,106` |
| API error handler (4xx unlogged) | `api/src/app.ts:151-212` |
| Stall threshold (lazy) | `api/src/services/video.service.ts:38` (`PIPELINE_STALL_THRESHOLD_MS`), `:570` |
| Worker retry / backoff | `services/summarizer/src/config.py:316`, `services/summarizer/src/worker/` |
| SSE-direct pipeline path | `services/summarizer/src/routes/stream.py`, `api/src/routes/stream.routes.ts:73,170` |
| Frontend Sentry | `apps/web/src/main.tsx:12-17` |
| Langfuse clients (copy-paste forks) | `services/{summarizer,assistant}/src/services/observability/langfuse_client.py` |
| Compose healthchecks | `docker-compose.yml` (vie-api gated on `/health`, `/ready` exists in API) |
| Env template | `.env.example:222-227` (SENTRY_*), `:348` (ALERT_WEBHOOK_URL) |
| Docs to sync | `docs/OBSERVABILITY.md`, `docs/SERVICE-ADMIN.md`, `docs/ERROR-HANDLING.md` |

## Gotchas
- Langfuse keys in `.env` are double-quoted — `activate_langfuse.sh` calls it fatal, but compose strips quotes and traces land; only breaks host-run Pydantic reads.
- Producer-lock TTL is 600s; dispatch guard fails open on Redis errors — sweeper must not race a live producer.
- Admin has a checked-in `static/` SPA bundle that is stale for local uvicorn runs; the Vite dev proxy can't log in.
- Worker healthcheck = `/tmp/vie-worker-heartbeat` mtime < 90s — "healthy" ≠ "consuming".

## Skills
admin/summarizer/llm-common → `backend-python`; api → `backend-node`; web → `react-vite`.

## Phase 2 admin map (explorer 2026-08-26 — facts for 2.1/2.2/2.3)
- **Vite proxy** `services/admin/ui/vite.config.ts:18-25` proxies only `/usage,/health,/alerts,/admin`. Routers in `src/main.py:153-160`: `/auth/login`, `/usage`, `/health`, `/alerts`, `/shares`, `/tiers`, `/users`, `/queue`, + `/admin/aggregate-daily`. Missing: `/auth,/shares,/tiers,/users,/queue`. UI client `ui/src/lib/api.ts` (API_BASE='', Bearer from localStorage `admin_api_key`, 401 → reload).
- **Queue**: admin `src/routes/queue.py` proxies only `GET /queue/stats`, `GET /queue/dlq?limit` to vie-api `/api/admin/queue/*` with `X-Admin-Key` (5s timeout; 401→500, transport→502). NO `/queue/replay` proxy (vie-api has `POST /api/admin/queue/replay {max}` → `{replayed}`, `api/src/routes/admin/queue.routes.ts:207`). UI: only `QueueStats.tsx` tiles on HealthPage; hook `useQueueStats` (10s). No DLQ/replay UI.
- **Usage endpoints w/o UI**: `/usage/anomalies` (client only), `/usage/duplicates` (client+hook `useUsageDuplicates`, no renderer), `/usage/by-service` (hook, no renderer), `/health/overview` (hook `useHealthOverview`, no renderer), `/health/history` (client only), `admin.aggregateDaily` (no callers). `/health/overview` (`src/routes/health.py:19-29`): `all([])`→healthy on empty; `timeout` status not treated as down (also in `/health/uptime`).
- **AlertsPage** `ui/src/pages/AlertsPage.tsx`: columns Severity(regex on severity??level??type)/Type/Model/Feature/Cost/Time. Only `high_cost_call` has model/feature; `backup_stale`+`high_cost_call` → "info"; failure/backup/stalled show `$0.0000`; type-specific fields (age_hours, failure_rate, sample_size, baseline_daily_usd, stalled_minutes, youtube_id) have no column. `AlertsBanner.tsx` styles all as danger.
- **Transcription rows**: `UsageRecord` (`packages/llm-common/src/llm_common/models.py`) has `unit` ("tokens"|"audio_seconds"), `audio_seconds`, `duration_ms` (NO latency_ms). Whisper rows: tokens 0, audio_seconds>0. Only `PipelineRunsPanel.tsx:89-93` handles `unit`. "0 tokens" in: `RecentCalls.tsx:36` (data has unit, cell ignores), `VideoDetailPage.tsx:174` + `:116` (typed calls lack unit — `usage.py:430` `/usage/video/{id}` + `api.ts:109-121`), `UsersPage.tsx:410` (`src/routes/users.py:375-388` `_format_assistant_call` drops unit).
- **static/**: served `src/main.py:176-179` at `/` after routers; Dockerfile builds ui→static (image never uses checked-in copy); 4 tracked files last built 2026-05-18 vs ui/src 2026-07-16; `vite.config.ts` `build.outDir: '../static'`, `emptyOutDir`. Fix = rebuild (npm run build in ui/) and stop tracking (user decision — needs git rm).
- **llm_usage_daily**: write-only. Writer `src/services/aggregator.py` via `POST /admin/aggregate-daily` (`main.py:168-173`); UI `api.admin.aggregateDaily` no callers; readers none; docs mention SERVICE-ADMIN.md:26,145, PROJECT-BRIEFING.md:282,463.
- **p95 hosts**: `/usage/stats` `usage.py:110-125` (avg_duration_ms), `/usage/by-feature` `usage.py:238-252` ($group by feature w/ avg_duration_ms), `/usage/by-model` `usage.py:265-278` (no duration). Index `(model,timestamp)`, `timestamp`. Mongo 7 → `$percentile` available.
- **UI tests**: 38 files; pages mock `../hooks/use-admin-api` module + QueryClientProvider wrapper (see `AlertsPage.test.tsx`); `lib/api.test.ts` stubs fetch. Run: `cd services/admin/ui && npx vitest run` (no test script; CI `.github/workflows/ci.yml:251-270`). Admin backend: `services/admin/.venv/bin/python -m pytest` (venv created 2026-08-26; 142 baseline). UI baseline 229.
- **Health poller** `src/services/health_checker.py`: GET /health on api/summarizer/assistant + mongo ping, 30s, in-memory `_current_health`, `health_history` 30d TTL. Statuses healthy/degraded/timeout/down. Admin deps: httpx only (no redis/aio-pika) → RabbitMQ via vie-api `/api/admin/queue/stats` (mgmt API) is the available signal; Redis needs either a dep or an API `/ready` read (api `/ready` checks Mongo+Redis+RabbitMQ).

## Phase 2 infra map (explorer 2026-08-26 — facts for 2.2/2.4/2.5)
- **Compose**: vie-api hc = `wget /health` (`docker-compose.yml:295`, prod `:291` w/ comment "gate LB on /ready"). Only vie-web depends on vie-api, bare-list form (dev `:556`, prod `:497`) = service_started. Qdrant hc = `bash -c "echo > /dev/tcp/localhost/6333"` (image has bash, NO curl/wget/nc); real readiness = `GET /readyz` → "all shards are ready". Dev summarizer depends only on mongodb (prod adds qdrant+redis). vie-admin has no depends_on vie-api.
- **API /ready** (`api/src/routes/health.routes.ts:46-75`): mongodb + redis (+ rabbitmq when decorated), 2s per check, 200 `{status:'ready',checks:{mongodb:'ok',...}}` or 503 `{status:'unavailable'}`. `/health` = `{status:'ok'}` always.
- **/healthz**: only `api/src/app.ts:112` (dev log-suppression branch, never routed) + `llm_common/middleware.py:43` SILENT_PATHS (+ its test). Delete API branch only.
- **Dead API config**: `api/src/config.ts:90` COST_ALERT_SLACK_WEBHOOK, `:96` POSTHOG_API_KEY (+ comment `:95`), TODO `api/src/app.ts:62-64`. Zero other refs.
- **Dead Langfuse exports** (0 prod callers): summarizer `fetch_prompt`, `get_active_prompts` (internal use only), `get_current_trace`, `is_enabled`, `redact_pii`/`truncate_payload` (re-exports; tests import them). Assistant: `fetch_prompt` + `log_score` zero callers anywhere; `get_current_trace`, `is_enabled`, `redact_pii`, `truncate_payload` tests only. Tests in `tests/test_langfuse_client.py` reference many → removing exports needs test edits; scope: drop assistant `fetch_prompt`/`log_score` + summarizer `fetch_prompt`, keep others.
- **Doc drift**: OBSERVABILITY.md:44 `rag_generation` (also PROJECT-BRIEFING.md:385) → real names `library_generation`/`library_agent` (`agent_loop.py:71,93`), `tool:{name}`, `action:{action}`, `query_translate`. SERVICE-ADMIN.md:25 + :144 claim llm_usage 90d TTL → wrong (indefinite; `_drop_legacy_ttl_index`). PROJECT-BRIEFING.md:463 same claim. buffer.py docstring says "explainer".
- **Buffer** (`packages/llm-common/src/llm_common/buffer.py`): `_flush_locked` clears buffer BEFORE insert; on exception rows dropped (`buffer_flush_failed lost_records=N`). No retry. AsyncBuffer lacks error test.
- **Scheduling**: host crontab `30 3 * * *` (INFRASTRUCTURE.md:530-546); backup.sh runs on HOST (docker exec vie-mongodb mongodump; curl+jq for Qdrant snapshots; QDRANT_SKIP=1 in prod). No ofelia/cron in compose. eval.yml cron `0 3 * * 1`.
- **Admin poller**: httpx only; vie-admin reaches vie-redis:6379, vie-rabbitmq:5672/15672 on vie-network. RabbitMQ mgmt API Basic auth from RABBITMQ_URL (api `queue.routes.ts:44-53`); admin gets no RABBITMQ_URL today. Redis needs `redis` pkg OR read api `/ready.checks.redis`.

## Sentry coverage audit (2026-08-29) — why Sentry "looks poor"
- **structlog never reaches Sentry** in any Python service: `PrintLoggerFactory` (summarizer `logging_config.py:81,88`, assistant `:86`, admin unconfigured) → no LogRecord → LoggingIntegration never fires. Assistant + admin log ONLY via structlog → 100% invisible. Summarizer modules using stdlib `logging` (pipeline_runner, worker/runner, pipeline_broker) DO emit events on `logger.error`.
- **Assistant**: zero capture sites; catch-all `server.py:73-79` swallows into structlog; AND the container can't resolve `*.ingest.de.sentry.io` (DNS) — nothing arrives.
- **Admin**: own init (`main.py:34-61`) without `before_send` → unscrubbed; loops log-only.
- **API**: only ≥500 via onError; uncaught/unhandledRejection captured by SDK defaults; pino not bridged; no global user scope.
- **Frontend**: `react-error-boundary` (not Sentry's), no captureException anywhere, no tracing/replay, no `@sentry/vite-plugin` → minified stacks; VITE_SENTRY_DSN still unset.
- **All services**: `SENTRY_RELEASE` empty, env=development everywhere (one DSN for all), no CI release/sourcemap step.
- Pipeline operational failures (transcript, rate-limit, timeout, frames, yt-dlp, assembly, faithfulness=debug, status callback) are warning/info/debug → breadcrumbs at best.
- No `AsyncioIntegration` → bare create_task exceptions silent. Zero metrics/uptime/log shipping.
