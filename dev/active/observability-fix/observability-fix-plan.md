# Observability Fix — Plan

Last Updated: 2026-08-26
Status: PHASE 1 + 2 DONE (uncommitted, verified 2026-08-26) — Phase 3 optional. Sweeper implemented in **summarizer** (not admin) — see tasks.md 1.2 for why.
Source: 4-agent observability audit 2026-08-20. Reference artifact: **VIE Observability Map** — https://claude.ai/code/artifact/5a6acead-8f0c-439b-986d-fb8e228ec776 (memory: `observability-audit-20260820`).

## Goal (user's words)
"Get our monitoring and observability to max — use logs, metrics and traces correctly so the system heals itself automatically and we can trace and track a bug in seconds."

## Executive Summary
The core is strong and LIVE (verified 2026-08-20): Langfuse traces (300, 52 prompts synced), request-id end-to-end (`scripts/find-request.sh <id>` is the pivot), backend Sentry with PII scrubber, `llm_usage` cost ledger + admin dashboard :8002, compose healthchecks incl. worker heartbeat file, faithfulness judge + golden-eval CI gates.

What's wrong is **activation and completion**, not missing infrastructure: alerts are computed and never delivered, stall detection is lazy, 4xx are never logged, retry backoff is dead config, frontend Sentry is off, and the admin UI is half-wired. A Prometheus/Grafana/Loki stack is the *last* step, not the first.

## Current State (ranked half-wired findings — all re-verified 2026-08-26)

| # | Finding | Where |
|---|---|---|
| 1 | Alerts evaluated every 5 min but never delivered — `ALERT_WEBHOOK_URL` unset in `.env` → Mongo `llm_alerts` only | `services/admin/src/services/alert_evaluator.py`, `packages/llm-common/.../alerts.py`, `services/admin/src/config.py:24` |
| 2 | Stall detection lazy — 30-min threshold only fires when the same video is resubmitted; no sweeper | `api/src/services/video.service.ts:38,570` |
| 3 | 4xx never logged — every branch returns before `request.log.error` | `api/src/app.ts:151-212` |
| 4 | `WORKER_RETRY_BACKOFF_SECONDS` dead — worker retries republish instantly | `services/summarizer/src/config.py:316`, `services/summarizer/src/worker/` |
| 5 | Frontend Sentry off (`VITE_SENTRY_DSN` absent from `.env.example`); `SENTRY_RELEASE` present in `.env.example` (added f55d84c) but unset | `apps/web/src/main.tsx:12,17`, `.env.example:222-227` |
| 6 | Compose gates API on `/health` (liveness) not `/ready`; vie-web `depends_on` has no condition | `docker-compose.yml` vie-api healthcheck |
| 7 | SSE-direct pipeline path: zero Sentry coverage; SSE errors are HTTP 200 in-band frames | `services/summarizer/src/routes/stream.py`, `api/src/routes/stream.routes.ts:73,170` |
| 8 | Assistant never reads the prompt registry (`fetch_prompt` has 0 callers) | `services/assistant/.../langfuse_client.py:359` |
| 9 | Admin half-wired: Vite dev proxy misses `/auth,/users,/queue`; stale `static/` bundle; DLQ peek/replay + `/usage/anomalies|duplicates` + `/health/overview` no UI; aggregate alerts render "info"/empty cols; transcription rows "0 tokens"; `llm_usage_daily` rollup orphaned | `services/admin/` |
| 10 | `$0.50` cost-alert threshold hardcoded; usage buffer drops rows on flush failure | `llm-common callback.py:54`, `buffer.py:61,106` |
| 11 | Doc drift: OBSERVABILITY.md `rag_generation` (real: `library_generation`/`library_agent`); SERVICE-ADMIN.md 90-day TTL (removed); ERROR-HANDLING.md alert table aspirational | `docs/` |
| 12 | Dead config: `COST_ALERT_SLACK_WEBHOOK`, `POSTHOG_API_KEY`; `/healthz` referenced but unrouted; dead Langfuse exports | `api/src/config.ts` |

**Genuinely missing:** metrics stack (no `/metrics`, p95s — averages only), log aggregation/retention (3×10 MB json-file per container), push alert channel, committed scheduler (backup cron is dev-box-only), active job sweeper, circuit breakers, CI security scanning.

## Target State
1. Every alert the system already computes reaches a phone/chat channel within 5 min.
2. A stuck job is detected and re-dispatched or failed+alerted without human action (real self-healing).
3. Any client-reported error (4xx or 5xx, API or SSE path, browser or backend) is findable by request-id in logs and Sentry.
4. Admin dashboard exposes every backend endpoint it has; no misleading panels.
5. p95 latency per stage available from existing `llm_usage.duration_ms`.
6. Docs match code; dead config gone.

## Implementation Phases

### Phase 1 — Close the loop (hours, no new services) 🔴
- **1.1 Deliver alerts** — set `ALERT_WEBHOOK_URL` (Discord/Slack/ntfy) in `.env`; verify `alert_evaluator` + llm-common high-cost-call alert actually POST; make the `$0.50` threshold a config var. AC: a forced spend-spike alert arrives in the channel.
- **1.2 Active stall sweeper** — new periodic loop in admin (pattern: `services/admin/src/main.py:115-116` `health_poller_loop`/`alert_evaluator_loop`) scanning `videoSummaryCache` for `processing` rows older than `PIPELINE_STALL_THRESHOLD_MS` (30 min) → re-dispatch (via API) or mark `failed` + alert. AC: a row artificially aged past threshold is swept within one cycle; alert emitted.
- **1.3 Honor `WORKER_RETRY_BACKOFF_SECONDS`** — exponential delay before republish on retry (attempt-scaled). AC: unit test asserts sleep/delay call with expected backoff.
- **1.4 Log 4xx at warn** — one line in `api/src/app.ts` error handler with `statusCode`, error `code`, `requestId`, route; add pino `redact` config (authorization, cookie, tokens). AC: a 400/401/429 appears in logs; Sentry-scrubber test parity for pino redact.
- **1.5 Frontend Sentry + release** — add `VITE_SENTRY_DSN`/`VITE_SENTRY_RELEASE` to `.env.example` + compose build args; set `SENTRY_RELEASE` (git sha) for backend. AC: a thrown browser error lands in Sentry tagged with release.
- **1.6 SSE-direct Sentry capture** — `capture_exception` in the failure handler of `stream.py` (and API stream route if applicable) with requestId tag. AC: forced pipeline failure on the non-queue path shows in Sentry.

### Phase 2 — Complete the half-wired (1–2 days)
- **2.1 Admin UI** — fix Vite dev proxy (`/auth`, `/users`, `/queue`, …); wire DLQ peek/replay, `/usage/anomalies`, `/usage/duplicates`, `/health/overview` into the UI; fix aggregate-alert severity/columns (`backup_stale` understated); fix transcription "0 tokens" rows; rebuild or remove stale `static/` bundle; delete or wire `llm_usage_daily` rollup.
- **2.2 Health completeness** — admin poller adds Redis, RabbitMQ, worker heartbeat; compose gates vie-api on `/ready`; vie-web `depends_on: condition: service_healthy`; Qdrant healthcheck hits HTTP not raw TCP.
- **2.3 Cheap p95s** — percentile aggregations on `llm_usage.duration_ms` per feature/model in existing admin usage endpoints + a dashboard column.
- **2.4 Commit schedulers** — backup cron + stall sweeper as compose-level definitions (ofelia or cron sidecar) instead of dev-box crontab.
- **2.5 Docs + dead code** — sync OBSERVABILITY.md / SERVICE-ADMIN.md / ERROR-HANDLING.md; delete `COST_ALERT_SLACK_WEBHOOK`, `POSTHOG_API_KEY`, `/healthz` refs, dead Langfuse exports; usage buffer retries-then-drops with a counter.

### Phase 3 — Optional
- **3.1** Monitoring compose profile: Grafana + Loki (solves log retention/search), optionally Prometheus + cadvisor.
- **3.2** Auto-replay policy for transient-error DLQ messages (poison messages must stay dead).
- **3.3** Assistant registry-first prompt loading; shared observability package to end the langfuse_client copy-paste fork.

## Risks / constraints
- Working-tree rule: no commit/stash/push without explicit current-turn verb. Commit per green phase.
- Stall sweeper must respect the producer-lock TTL (600s) and the dispatch guard — never re-dispatch a row whose producer is still alive.
- Editing `pipeline-version.json` breaks bind mounts (not expected here). Never run the 3 test suites in parallel.
- Summarizer tests: `services/summarizer/.venv/bin/python -m pytest`. Web typecheck: `pnpm exec tsc -b` (root `--noEmit` is hollow).
