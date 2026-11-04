---
name: vie-api
description: Repo facts for the vie-api gateway (Fastify + TypeScript) and the shared @vie/types and @vie/shared packages. Loads on its own when files under api/ or packages/ are touched. Use when adding routes, services, repositories, auth, queue publishing, caching, or changing shared types.
paths:
  - "api/**"
  - "packages/types/**"
  - "packages/shared/**"
---

# vie-api

Fastify 4 + TypeScript on Node 20, MongoDB 7, Redis 7, RabbitMQ. Layer-based layout under `api/src/`:
`routes/` (Zod schema attach + reply mapping, no controller layer) → `services/` (business logic,
throws domain errors, no HTTP) → `repositories/` (`toEntity()` mapping, returns domain objects).
`plugins/` holds `mongodb`, `redis`, `rabbitmq`, `auth`; `schemas/` Zod; `config.ts` is the
Zod-validated env and the only way to read configuration; `container.ts` wires DI. Tests sit next
to sources as `*.test.ts`. Conventions: [.claude/rules/typescript.md](../../rules/typescript.md).

## Verify

```
pnpm --filter @vie/types --filter @vie/shared build   # first, api imports their dist
pnpm --filter @vie/api typecheck && pnpm --filter @vie/api lint && pnpm --filter @vie/api test
```

Tests use `mongodb-memory-server` (`src/test/global-setup.ts`); CI also starts Mongo and Redis and
needs `JWT_SECRET`, `JWT_REFRESH_SECRET`. `/check` runs the right subset.

## Facts that bite

- The `vie-api` container runs the image's `dist`; the `src` bind mount changes nothing. After api
  edits: `pnpm docker:build:api` and recreate, or run `pnpm dev:api` locally.
- Widening a type in `packages/types/src/vie-response.ts` needs the matching change in the
  summarizer's Pydantic models (`grep domain_types.py`), or extraction output fails validation.
- `packages/shared/src/config/pipeline-version.json` is read by api (`config.ts`, docker path first)
  and summarizer. One value, never overridden by env. A bump creates new dedupKey rows for every
  video; `?bypassCache=true` on submit is the surgical refresh ([docs/IDEMPOTENCY.md](../../../docs/IDEMPOTENCY.md)).
- Mongo runs as single-node replica set `rs0`. In-container URIs work as is; host tools need
  `?directConnection=true`.
- Redis response cache keys are `vie:response:v<N>:<ytid>`; the version segment follows
  `PIPELINE_VERSION`.
- Queue publishing: the `rabbitmq` plugin owns one lazy, reconnecting `ConfirmChannel`
  (`fastify.rabbitmq.getChannel()`, connect mutex). Topology (exchange, queue, DLX, DLQ) is asserted
  from `queue-topology.ts` and mirrored byte-for-byte by `services/summarizer/src/worker/topology.py`;
  RabbitMQ refuses to start a consumer whose queue arguments differ. `QueuePublisher` validates with
  Zod and resolves only after publisher confirms; `QueuePublishError` maps to 503 (the DB row already
  exists, so retry is idempotent). Priority queues are tier-based and not strictly FIFO.
- The SSE stream drives the pipeline: `POST /api/videos` only registers; a client that never consumes
  the stream never runs the pipeline. New `phase` values must be added to both `VALID_SSE_PHASES`
  and `SSE_PHASE_MAP` or the web UI freezes.
- Auth lives here: JWT access 15 min, refresh 7 days in an HttpOnly cookie; `INTERNAL_SECRET` guards
  service-to-service calls; `ADMIN_API_KEY` and `ADMIN_EMAILS` gate admin routes; rate limits via
  `RATE_LIMIT_*`, `SUBMIT_BURST_LIMIT_PER_HOUR`; CORS from
  `FRONTEND_URL` plus `CORS_ADDITIONAL_ORIGINS` (add `http://localhost:5273` for Playwright).
- Cross-user dedup and request idempotency keys are documented in [docs/IDEMPOTENCY.md](../../../docs/IDEMPOTENCY.md); GDPR
  deletion runs through `VideoCascadeService` ([docs/GDPR.md](../../../docs/GDPR.md)). Summaries never expire.
- Cache failures degrade to the DB; never let Redis break a request. Health returns 503 when Mongo
  or Redis is down.

## Read before changing

| Area | Doc |
|---|---|
| Endpoints and envelopes | [docs/API-REFERENCE.md](../../../docs/API-REFERENCE.md), [docs/SERVICE-API.md](../../../docs/SERVICE-API.md) |
| Collections, VIEResponse | [docs/DATA-MODELS.md](../../../docs/DATA-MODELS.md) |
| Auth, rate limits, CORS | [docs/SECURITY.md](../../../docs/SECURITY.md) |
| Error codes, retry, DLQ | [docs/ERROR-HANDLING.md](../../../docs/ERROR-HANDLING.md) |
| Dedup, PIPELINE_VERSION | [docs/IDEMPOTENCY.md](../../../docs/IDEMPOTENCY.md) |
| Billing plans and roles | `scripts/migrate-billing*.ts`, `dev/active/billing-roles/` while active |
