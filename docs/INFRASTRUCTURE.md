# Infrastructure

Docker setup, networking, and environment configuration.

---

## Services Overview

| Service        | Image/Build             | Port       | Purpose            |
| -------------- | ----------------------- | ---------- | ------------------ |
| vie-web        | ./apps/web              | 5173       | React frontend     |
| vie-api        | ./api                   | 3000       | Node.js backend    |
| vie-summarizer | ./services/summarizer   | 8000       | Python service     |
| vie-assistant  | ./services/assistant    | 8001       | Python RAG + chat  |
| vie-admin      | ./services/admin        | 8002       | Admin dashboard    |
| vie-mongodb    | mongo:7                 | 27017      | Database           |
| vie-redis      | redis:7-alpine          | 6379       | Response cache (summarizer) + pipeline lock (summarizer) + dispatch guard (api) |
| vie-qdrant     | qdrant/qdrant:latest    | 6333/6334  | Vector DB (RAG)    |
| vie-rabbitmq   | rabbitmq:3.13-mgmt      | 5672/15672 | Job queue (AMQP + Management UI) |
| vie-summarizer-worker | (shares vie-summarizer image) | —  | RabbitMQ consumer for video pipeline jobs |

---

## Caching layers

Process once, reuse forever. Three layers absorb identical work across users so the same YouTube video costs the LLM provider once, not N times.

| Layer | Store   | Keyed by              | Holds                                |
| ----- | ------- | --------------------- | ------------------------------------ |
| L0    | Redis   | `vie:api:dispatched:<videoSummaryId>` | Dispatch guard (api). SET NX EX, 900s TTL. Released on terminal FAILED (internal.routes.ts) or on dispatch throw (dispatch-guard.service.ts catch). Fail-open on Redis error. See [IDEMPOTENCY.md](./IDEMPOTENCY.md#dispatch-guard-publish-layer). |
| L1    | Redis   | `vie:response:<PIPELINE_VERSION>:<youtube_id>` | Full VIEResponse JSON (instant serve). Written by the summarizer; for non-English videos the **translation phase** owns the write (assembly phase intentionally skips Redis when `ctx.language != "en"`). Allowlisted top-level keys only: `youtubeId`, `title`, `creator`, `channel`, `duration`, `thumbnailUrl`, `status`, `meta`, `tabs`, `language`, `isRTL`, `sourceLanguage` (`_SAFE_KEYS` in `services/summarizer/src/services/cache/response_cache.py`). |
| L2    | MongoDB | `youtubeId` field     | Persistent video summary + assembled tabs |
| L3    | S3      | `youtube_id` prefix   | Extracted scene frames (skip re-extraction) |

### Shared vs per-user

System caches are shared across users (same video → same summary). User data is **never** cached at the system level.

| Data                | Cache scope | Why                                       |
| ------------------- | ----------- | ----------------------------------------- |
| VIEResponse         | Shared (L1) | Same video deterministically yields same output |
| Video summaries     | Shared (L2) | Persistent across restarts                |
| System expansions   | Shared (L2) | Same section ⇒ same explanation           |
| Video frames        | Shared (L3) | Frame extraction is expensive             |
| User chats          | Per-user (uncached) | Personalised, contextual responses |
| Folders / notes     | Per-user (uncached) | Identity-bound state               |

### Concurrent submission

Two users submitting the same video simultaneously do not double-bill. The flow is layered:

1. **Cross-user dedup at the cache row** — `VideoRepository.upsertCacheByDedupKey()` collapses concurrent submits with the same content-addressed `dedupKey` onto a single `videoSummaryCache` row via a partial unique index. Exactly one caller sees `wasInsert: true` and is responsible for dispatching.
2. **Dispatch guard (L0)** — the publisher acquires a Redis lock (`vie:api:dispatched:<videoSummaryId>`) before publishing to RabbitMQ. Belt-and-suspenders against API replicas racing outside Mongo's serialization window.
3. **Pipeline lock (summarizer)** — even if (1) and (2) both fail open, the summarizer's per-video lock (`pipeline_event_stream.acquire_lock`) is the last line of defense: the second consumer attaches to the SSE event stream and receives the same result once the first run reaches `status: "completed"`.

**Same Redis instance** for all three uses (api dispatch guard + summarizer response cache + summarizer pipeline lock). Operational coherence: `REDIS_URL` in `api/src/config.ts` and `services/summarizer/src/config.py` must resolve to the same instance.

Cache invalidation is **versioned, not time-based**: same video always produces the same summary at a given `PIPELINE_VERSION`. Bumping the version invalidates both the cross-user `dedupKey` and the per-user idempotency hash atomically (see [`IDEMPOTENCY.md`](./IDEMPOTENCY.md)).

---

## Docker Compose

```yaml
services:
  # ═══════════════════════════════════════════════
  # INFRASTRUCTURE
  # ═══════════════════════════════════════════════

  vie-mongodb:
    image: mongo:7
    container_name: vie-mongodb
    restart: unless-stopped
    ports:
      - "27017:27017"
    volumes:
      - vie_mongodb_data:/data/db
    environment:
      MONGO_INITDB_DATABASE: video-insight-engine
    networks:
      - vie-network

  vie-redis:
    image: redis:7-alpine
    container_name: vie-redis
    restart: unless-stopped
    ports:
      - "6379:6379"
    volumes:
      - vie_redis_data:/data
    command: redis-server --appendonly yes
    networks:
      - vie-network

  vie-qdrant:
    image: qdrant/qdrant:latest
    container_name: vie-qdrant
    restart: unless-stopped
    ports:
      - "6333:6333"
      - "6334:6334"
    volumes:
      - vie_qdrant_data:/qdrant/storage
    networks:
      - vie-network

  # ═══════════════════════════════════════════════
  # APPLICATION SERVICES
  # ═══════════════════════════════════════════════

  vie-api:
    build:
      context: .
      dockerfile: api/Dockerfile
    container_name: vie-api
    restart: unless-stopped
    ports:
      - "3000:3000"
    environment:
      NODE_ENV: development
      PORT: 3000
      MONGODB_URI: mongodb://vie-mongodb:27017/video-insight-engine
      SUMMARIZER_URL: http://vie-summarizer:8000
      ASSISTANT_URL: http://vie-assistant:8001
      JWT_SECRET: ${JWT_SECRET:-dev-secret-change-in-production}
      JWT_REFRESH_SECRET: ${JWT_REFRESH_SECRET:-dev-refresh-secret-change-in-production}
      FRONTEND_URL: ${FRONTEND_URL:-http://localhost:5173}
      INTERNAL_SECRET: ${INTERNAL_SECRET:-dev-internal-secret-change-me}
    networks:
      - vie-network
    depends_on:
      vie-mongodb:
        condition: service_healthy

  vie-summarizer:
    build:
      context: .
      dockerfile: services/summarizer/Dockerfile
    container_name: vie-summarizer
    restart: unless-stopped
    ports:
      - "8000:8000"
    environment:
      PYTHONUNBUFFERED: 1
      MONGODB_URI: mongodb://vie-mongodb:27017/video-insight-engine
      LLM_PROVIDER: ${LLM_PROVIDER:-anthropic}
      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY:-}
      REDIS_URL: redis://vie-redis:6379
      QDRANT_HOST: vie-qdrant
      QDRANT_PORT: 6333
      S3_BUCKET: ${S3_BUCKET:-vie-transcripts}
      AWS_REGION: ${AWS_REGION:-us-east-1}
    networks:
      - vie-network
    depends_on:
      vie-mongodb:
        condition: service_healthy

  vie-assistant:
    build:
      context: .
      dockerfile: services/assistant/Dockerfile
    container_name: vie-assistant
    restart: unless-stopped
    ports:
      - "8001:8001"
    environment:
      PYTHONUNBUFFERED: 1
      MONGODB_URI: mongodb://vie-mongodb:27017/video-insight-engine
      LLM_PROVIDER: ${LLM_PROVIDER:-anthropic}
      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY:-}
      QDRANT_HOST: vie-qdrant
      QDRANT_PORT: 6333
    networks:
      - vie-network
    depends_on:
      vie-mongodb:
        condition: service_healthy

  vie-admin:
    build:
      context: .
      dockerfile: services/admin/Dockerfile
    container_name: vie-admin
    restart: unless-stopped
    ports:
      - "8002:8002"
    environment:
      PYTHONUNBUFFERED: 1
      MONGODB_URI: mongodb://vie-mongodb:27017/video-insight-engine
      ADMIN_API_KEY: ${ADMIN_API_KEY:-dev-admin-key-change-me}
      VIE_API_URL: http://vie-api:3000
      VIE_SUMMARIZER_URL: http://vie-summarizer:8000
      VIE_ASSISTANT_URL: http://vie-assistant:8001
    networks:
      - vie-network
    depends_on:
      vie-mongodb:
        condition: service_healthy

  vie-web:
    build:
      context: .
      dockerfile: apps/web/Dockerfile
    container_name: vie-web
    restart: unless-stopped
    ports:
      - "5173:5173"
    environment:
      VITE_API_URL: http://localhost:3000/api
      VITE_WS_URL: ws://localhost:3000/ws
    networks:
      - vie-network
    depends_on:
      - vie-api

networks:
  vie-network:
    driver: bridge

volumes:
  vie_mongodb_data:
  vie_redis_data:
  vie_qdrant_data:
```

---

## Environment Variables

### .env.example

```bash
# ════════════════════════════════════════════════════
# VIDEO INSIGHT ENGINE
# ════════════════════════════════════════════════════
# Copy to .env and fill in values

# ────────────────────────────────────────────────────
# LLM Provider Configuration
# ────────────────────────────────────────────────────
LLM_PROVIDER=anthropic          # anthropic, openai, or gemini
LLM_FAST_PROVIDER=              # Optional: separate provider for fast model
LLM_FALLBACK_PROVIDER=          # Optional: fallback if primary fails

# Provider API Keys (set for providers you use)
ANTHROPIC_API_KEY=sk-ant-api03-xxxxxxxxxxxxx
OPENAI_API_KEY=                 # Required if using OpenAI
GOOGLE_API_KEY=                 # Required if using Gemini

# ────────────────────────────────────────────────────
# JWT Authentication
# ────────────────────────────────────────────────────
JWT_SECRET=change-this-to-a-long-random-string
JWT_REFRESH_SECRET=change-this-to-another-random-string
JWT_EXPIRES_IN=15m
JWT_REFRESH_EXPIRES_IN=7d

# ────────────────────────────────────────────────────
# Internal Service URLs
# ────────────────────────────────────────────────────
SUMMARIZER_URL=http://localhost:8000
INTERNAL_SECRET=change-this-for-inter-service-auth

# ────────────────────────────────────────────────────
# Redis — shared across api (dispatch guard) and summarizer
# (response cache + per-video pipeline lock). MUST resolve
# to the same instance in both services.
# ────────────────────────────────────────────────────
REDIS_URL=redis://vie-redis:6379

# TTL on the api's dispatch-guard key (vie:api:dispatched:<videoSummaryId>).
# Must exceed the summarizer's PIPELINE_LOCK_TTL_SECONDS (default 600s).
DISPATCH_GUARD_TTL_SECONDS=900

# ────────────────────────────────────────────────────
# Qdrant (Vector DB for RAG)
# ────────────────────────────────────────────────────
QDRANT_HOST=vie-qdrant
QDRANT_PORT=6333

# ────────────────────────────────────────────────────
# S3 Media Storage (Frames, Transcripts)
# ────────────────────────────────────────────────────
S3_BUCKET=vie-transcripts
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_ENDPOINT_URL=              # Optional: LocalStack for local dev
# Presigned URL TTL. Must be ≥ the api's FRAME_URL_TTL_SECONDS (21600)
# or SSE frame URLs 403 mid-session.
S3_PRESIGNED_URL_EXPIRY=21600

# ────────────────────────────────────────────────────
# Admin Panel
# ────────────────────────────────────────────────────
ADMIN_API_KEY=change-this-admin-key

# ────────────────────────────────────────────────────
# Frontend URLs (for production)
# ────────────────────────────────────────────────────
# VITE_API_URL=https://api.yourdomain.com/api
# VITE_WS_URL=wss://api.yourdomain.com/ws
```

> This block is a condensed copy — **`.env.example` at the repo root is authoritative** and carries the full commented list (Sentry, eval account, tuning knobs).

### Frame pipeline & summarizer tuning (2026-08)

New/changed vars introduced by the yt-dlp-403 fix and the two-pass frame pipeline. All are passed through the compose `x-summarizer-env` block (both `docker-compose.yml` and `docker-compose.prod.yml`):

| Var | Default | Purpose |
|-----|---------|---------|
| `YTDLP_PLAYER_CLIENTS` | `android` | yt-dlp player clients for all video/audio downloads. YouTube 403s the web client's URLs from some environments. **Never mix in `default`** — a merged format list lets bestvideo pick a 403ing web DASH format. Metadata/subtitle extraction deliberately doesn't use it. |
| `SCENE_HIRES_ENABLED` | `true` | Pass-2 720p refinement of selected frames |
| `SCENE_S3_PREFIX` | `scenes-v3` | Versioned frame-cache prefix — bump to invalidate the S3 frame cache |
| `SCENE_HIRES_TIMEOUT` | `90` | Stream-URL refinement budget (s) |
| `SCENE_HIRES_FALLBACK_TIMEOUT` | `180` | Local ≤720p download fallback budget (s) |
| `FRAME_TIER_ENABLED` | `true` | Adaptive visual tiers (high/standard/low from `domains.json` `visualCriticality`) |
| `TRANSCRIPT_CLEANING_TIMEOUT` | `30` | Transcript-cleaning LLM call timeout (was hardcoded) |
| `HF_TOKEN` | empty | Optional Hugging Face Hub token for SentenceTransformer pulls |
| `S3_PRESIGNED_URL_EXPIRY` | `21600` (was 3600) | Must stay ≥ api `FRAME_URL_TTL_SECONDS` |
| `EVAL_USER_EMAIL` / `EVAL_USER_PASSWORD` | `eval@vie.local` / none | Eval-runner local account — auto-registered on first run; no default password by design (also a CI secret) |
| `SENTRY_DSN` / `SENTRY_ENVIRONMENT` / `SENTRY_RELEASE` | empty | Optional Sentry error reporting (see [OBSERVABILITY.md](./OBSERVABILITY.md)) |
| `VITE_SENTRY_DSN` / `VITE_SENTRY_RELEASE` | empty | Browser Sentry — compose **build args**, baked into the web bundle (rebuild `vie-web` after changing) |
| `ALERT_WEBHOOK_URL` | empty | Every `llm_alerts` row is also POSTed here as JSON (Slack/Discord/ntfy). Empty = Mongo-only, nothing pushes |
| `ALERT_COST_THRESHOLD_USD` | `0.50` | Per-call `high_cost_call` alert threshold (`llm_common/callback.py`) |
| `QDRANT_URL` (vie-admin) | `http://vie-qdrant:6333` | Admin health poller's Qdrant `/readyz` probe |
| `BACKUP_KEEP` / `BACKUP_MAX_AGE_HOURS` | `14` / `26` | Backups retained by `scripts/backup.sh` / the `vie-backup-cron` sidecar; age past which vie-admin's `backup_stale` dead-man's switch trips |
| `STALL_SWEEP_ENABLED` / `STALL_SWEEP_INTERVAL_SECONDS` / `STALL_THRESHOLD_MINUTES` | `true` / `300` / `30` | Summarizer stall sweeper — fails `processing` rows with no progress + no live producer lock, alerts `pipeline_stalled` |
| `WORKER_RETRY_BACKOFF_SECONDS` | `5` | Worker retry delay, doubled per attempt (5 → 10 → 20 s), capped at 120 s (worker service env, not `x-summarizer-env`) |

---

## Network Topology

```
┌──────────────────────────────────────────────────────────────────────┐
│                   vie-network (Docker bridge)                         │
│                                                                       │
│  External Access:                                                     │
│  ├── :5173 → vie-web (Frontend)                                      │
│  ├── :3000 → vie-api (API)                                           │
│  └── :8002 → vie-admin (Admin Dashboard)                             │
│                                                                       │
│  Internal Only:                                                       │
│  ├── vie-mongodb:27017    (Database)                                 │
│  ├── vie-redis:6379       (Response Cache)                           │
│  ├── vie-qdrant:6333/6334 (Vector DB)                                │
│  ├── vie-summarizer:8000  (Pipeline)                                 │
│  └── vie-assistant:8001   (RAG Chat)                                 │
│                                                                       │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Service Dependencies

```
vie-mongodb ─────┬──────────────────────────────────────────┐
vie-redis ───────┤                                          │
vie-qdrant ──────┤                                          │
                 │                                          │
                 ▼                                          ▼
           vie-api ◄──────────────────────────────── vie-assistant
                 │
                 ├──────────► vie-web
                 ├──────────► vie-admin
                 │
                 ▼
          vie-summarizer ──► vie-redis, vie-qdrant, S3
```

Startup order:

1. vie-mongodb, vie-redis, vie-qdrant (infrastructure, parallel)
2. vie-assistant (needs MongoDB, Qdrant)
3. vie-api (needs MongoDB)
4. vie-summarizer (needs MongoDB, Redis, Qdrant)
5. vie-admin (needs MongoDB)
6. vie-web (needs vie-api)

---

## Commands

### Development

```bash
# Start all services
docker-compose up -d

# View logs
docker-compose logs -f

# View specific service logs
docker-compose logs -f vie-api

# Rebuild after changes
docker-compose up -d --build vie-api

# Stop all
docker-compose down

# Stop and remove volumes (fresh start)
docker-compose down -v
```

### Health Checks

```bash
# API
curl http://localhost:3000/health

# Summarizer
curl http://localhost:8000/health

# MongoDB
docker exec vie-mongodb mongosh --eval "db.runCommand('ping')"

# RabbitMQ (management UI)
open http://localhost:15672   # default creds vie / vie-dev
curl -u vie:vie-dev http://localhost:15672/api/queues/%2F/vie.pipeline.jobs

# RabbitMQ (AMQP)
docker exec vie-rabbitmq rabbitmq-diagnostics ping
```

---

## RabbitMQ Job Queue

The summarizer pipeline runs from a durable RabbitMQ queue (when `USE_QUEUE_PIPELINE=true`).
`POST /api/videos` publishes a job; `vie-summarizer-worker` consumes it.

**Topology** (declared by both publisher and consumer with identical args):

| Object | Type | Args |
| --- | --- | --- |
| `vie.pipeline` | exchange (direct) | `durable: true` |
| `vie.pipeline.jobs` | queue | `x-max-priority: 10`, `x-message-ttl: 3600000`, `x-dead-letter-exchange: vie.pipeline.dlx`, `x-dead-letter-routing-key: video.process.dead` |
| `vie.pipeline.dlx` | exchange (direct) | `durable: true` |
| `vie.pipeline.dlq` | queue | `durable: true` |
| Routing key | `video.process` | (main) / `video.process.dead` (DLQ) |

**Payload** (Zod-validated on publisher, Pydantic-validated on consumer):

```jsonc
{
  "videoSummaryId": "ObjectId hex",
  "youtubeId": "11-char string",
  "url": "https://...",
  "userId": "ObjectId hex | null",
  "tier": "free | pro | team",
  "priority": 1 | 5,                  // free=1, pro/team=5
  "providers": { "default": "...", "fast": "...", "fallback": "..." } | null,
  "bypassCache": false,
  "requestId": "uuid",
  "attempt": 1,                       // 1-indexed; incremented on republish
  "createdAt": "ISO-8601"
}
```

**Retry policy** — on consumer error the worker waits
`WORKER_RETRY_BACKOFF_SECONDS × 2^(attempt-1)` (default 5 → 10 → 20 s, capped at
120 s; the original message stays unacked during the wait so a crash mid-wait is
redelivered, not lost), then republishes to the main queue with
`attempt = attempt + 1` (up to `WORKER_MAX_RETRIES`, default 3); after the cap the
message is nacked with `requeue=False`, which the DLX routes into `vie.pipeline.dlq`.

**SSE preservation** — the worker acquires the same Redis pipeline lock that the
SSE producer uses (`pipeline_event_stream.acquire_lock`). When a client opens
`/api/videos/:id/stream`, the existing SSE handler sees the lock held and attaches
as a consumer of the Redis Streams event log — no frontend changes.

**Admin endpoints** (require `X-Admin-Key` header):

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/admin/queue/stats` | Depth, in-flight, DLQ depth (RabbitMQ management API) |
| GET | `/api/admin/queue/dlq` | List messages in `vie.pipeline.dlq` |
| POST | `/api/admin/queue/replay` | Re-publish all DLQ messages back to `vie.pipeline.jobs` |

---

## MongoDB Setup

Collections are created automatically. To set up indexes:

```javascript
// Connect to MongoDB
mongosh "mongodb://localhost:27017/video-insight-engine"

// System cache indexes
db.videoSummaryCache.createIndex({ youtubeId: 1 }, { unique: true })
db.videoSummaryCache.createIndex({ status: 1 })

db.systemExpansionCache.createIndex(
  { videoSummaryId: 1, targetType: 1, targetId: 1 },
  { unique: true }
)

// User data indexes
db.users.createIndex({ email: 1 }, { unique: true })

db.folders.createIndex({ userId: 1, path: 1 })
db.folders.createIndex({ userId: 1, parentId: 1 })

db.userVideos.createIndex({ userId: 1, videoSummaryId: 1 }, { unique: true })
db.userVideos.createIndex({ userId: 1, folderId: 1 })

```

---

## Backup & Restore

Data lives in two stores: MongoDB (summaries, users, ledger) and Qdrant (RAG vectors — rebuildable via re-ingest but expensive). Redis and RabbitMQ hold only transient state and are not backed up.

### Taking a backup

```bash
./scripts/backup.sh                 # → backups/<UTC-timestamp>/
BACKUP_KEEP=30 ./scripts/backup.sh  # keep 30 most recent (default 14)
```

Produces `mongo-video-insight-engine.archive.gz` (mongodump `--archive --gzip`), one `qdrant-<collection>.snapshot` per collection (Qdrant snapshot API; server-side copy deleted after download), and a `manifest.json`. `backups/` is gitignored. For off-host durability, sync the directory to the existing S3 bucket (e.g. `aws s3 sync backups/ s3://<bucket>/backups/`).

Both scripts work unchanged against the auth-enabled prod stack: mongodump/mongorestore auth args are resolved inside the Mongo container from its own `MONGO_INITDB_ROOT_*` env, so no secrets cross the host boundary and the same commands run on the authless dev stack. Pruning deletes only timestamp-named (`YYYYMMDDTHHMMSSZ`) directories under the backup root (newest `BACKUP_KEEP` kept), so unrelated dirs under a custom root are never touched. An unreachable Qdrant aborts with an explicit error; set `QDRANT_SKIP=1` for a deliberate Mongo-only backup (vectors are re-derivable).

### Scheduling

**Committed scheduler (preferred):** the `vie-backup-cron` sidecar
(`scripts/backup-cron/Dockerfile` + `crontab`) runs `scripts/backup.sh` at
03:30 UTC from inside the stack. It is behind a compose profile because it
mounts the docker socket (root-equivalent on the host — `mongodump` runs via
`docker exec vie-mongodb`):

```bash
docker compose --profile backup up -d --build vie-backup-cron   # start the scheduler
docker compose --profile backup run --rm --no-deps vie-backup-cron \
  bash -c 'cd /repo && QDRANT_URL=http://vie-qdrant:6333 ./scripts/backup.sh'   # run one now
```

Output lands in `./backups/<UTC-timestamp>/` (same bind mount vie-admin's
dead-man's switch reads), log in `backups/backup.log`. `BACKUP_KEEP` (default
14) controls pruning. In prod, Qdrant publishes no host port — the sidecar
reaches it on the compose network, so no `QDRANT_SKIP` is needed. The service
runs with `init: true` (tini as PID 1): busybox `crond` ignores SIGTERM on its
own, so without it `docker stop` would wait 10 s and SIGKILL a dump mid-write.

**Legacy host crontab (2026-07-14 → 2026-08-26):** the same schedule used to
live in the dev-box user crontab (`30 3 * * * … ./scripts/backup.sh`) with a
Windows Task Scheduler mirror for the WSL2-asleep case. Remove it once the
sidecar is running (`crontab -e`) so the two don't double-back-up.

### Staleness alarm (dead-man's switch)

Scheduling alone fails silently (dead cron, stopped VM, failing script), so
the vie-admin alert evaluator (`services/admin/src/services/alert_evaluator.py`)
checks the newest `backups/*/manifest.json` timestamp every 5 minutes and
raises a `backup_stale` alert (Mongo `llm_alerts` + `ALERT_WEBHOOK_URL`, 6 h
cooldown) when it is older than `BACKUP_MAX_AGE_HOURS` (default 26 — daily
cron plus slack) or when no backup exists at all. Both compose files mount
the host `backups/` dir read-only into vie-admin at `/backups` (`BACKUP_DIR`).
A missing/unmounted dir disables the check, so a box without the mount never
false-alarms. Applies on next vie-admin recreate.

### Restoring

```bash
./scripts/restore.sh backups/<timestamp>                    # into the live stack (mongorestore --drop)
MONGO_CONTAINER=scratch ./scripts/restore.sh backups/<ts>   # drill against a scratch container
```

Qdrant snapshots upload with `priority=snapshot` (snapshot data wins); a failed upload aborts with an error naming the collection and `QDRANT_URL`. The script prints per-collection document counts at the end for verification.

### Restore drill — executed 2026-07-06

Procedure: inserted a marker doc → `./scripts/backup.sh` → started scratch `mongo:7` container → `MONGO_CONTAINER=scratch-mongo-drill ./scripts/restore.sh backups/20260706T184141Z`.

Result: **33 documents restored, 0 failed**; all index metadata (unique keys, TTL `expireAfterSeconds`, compound indexes) recreated; marker doc verified in the scratch instance. Qdrant had no collections at drill time (post-flush state); snapshot upload path exercised as no-op. Re-run the drill after the next Qdrant re-ingest.

### Restore drill — executed 2026-07-14 (first with live Qdrant collections)

Procedure: `./scripts/backup.sh` against the live stack (snapshot API +
mongodump are read-only; taken mid LLM-eval run) → scratch
`qdrant/qdrant:v1.18.2` (`vie-qdrant-restore-drill`, port 16333) + scratch
`mongo:7` (`vie-mongo-restore-drill`) →
`QDRANT_URL=http://localhost:16333 MONGO_CONTAINER=vie-mongo-restore-drill ./scripts/restore.sh backups/20260714T104418Z`
→ scratch containers removed.

Result: **Mongo: 78,973 documents restored, 0 failed** (incl. 78,948
`health_history`), all indexes recreated. **Qdrant: 3 collections restored**
(`eval_retrieval_*`), restored names/point counts/vector config (384-dim
Cosine) matched live exactly. Caveat: the eval collections held 0 points at
snapshot time (the eval run creates and clears them), so the Qdrant leg
exercised snapshot download/upload and collection recreation but not bulk
point data — re-run after `transcript_chunks` is re-ingested for a
points-bearing drill. `restore.sh` needed no changes: `QDRANT_URL` /
`MONGO_CONTAINER` env overrides already retarget it.

---

## Single-Replica Assumptions

Several correctness mechanisms are **process-local**. The compose files run
exactly one replica of each service; scaling any of these to N>1 requires the
listed change first:

| Mechanism | Where | Breaks at N>1 because | Fix before scaling |
| --- | --- | --- | --- |
| WebSocket registry (`Map<userId, Set<WebSocket>>`) | `api/src/plugins/websocket.ts` | broadcasts only reach sockets connected to the same process | Redis pub/sub fan-out (or sticky sessions + fan-out) |
| Soft-delete verdict cache | `api/src/utils/soft-delete-cache.ts` | a deletion processed on replica A stays cached as "active" on replica B for the TTL | shared cache (Redis) or accept the TTL skew |
| Rate-limit counters (in-memory store) | `api/src/plugins/rate-limit.ts` | each replica keeps its own counters → effective limit multiplies | Redis store for @fastify/rate-limit |
| SSE proxy attach (`/api/videos/:id/stream`) | `api/src/routes/stream.routes.ts` | fine per-request, but the LB must not buffer (`X-Accel-Buffering: no`) | any non-buffering LB; no shared state |
| Summarizer pipeline lock + response cache | Redis (`vie:*`) | already shared via Redis — safe | — |
| RabbitMQ worker | `vie-summarizer-worker` | safe — competing consumers is the design | just scale the worker |
| Admin health poller + alert evaluator | `services/admin/src/services/*.py` | N replicas → duplicate health snapshots and duplicate alerts (cooldowns make dupes rare, not impossible) | leader election, or keep admin at 1 replica |

vie-assistant is stateless per-request (state in Mongo/Qdrant) and can scale
freely. `/ready` on vie-api reports per-instance readiness (Mongo, Redis, and
the broker when `USE_QUEUE_PIPELINE=true`); `/health` is liveness only.

---

## Production Deployment (decided 2026-07-07: full self-host compose)

Production runs the same containers as dev from **`docker-compose.prod.yml`**
on a single Docker host. The earlier Railway/Vercel split-hosting plan was
dropped — one box, one compose file, no per-platform config drift.

Key differences from the dev compose:

- **Mandatory secrets** — every secret is `${VAR:?...}`; compose refuses to
  interpolate without them (verified: `docker compose -f docker-compose.prod.yml config`
  fails fast naming the missing variable). `api/src/config.ts` additionally
  refuses well-known dev-default secrets when `NODE_ENV=production`.
- **Authenticated datastores** — Mongo root user/pass, Redis `--requirepass`,
  RabbitMQ user/pass. Qdrant is unauthenticated but network-internal only.
- **No published datastore ports** — Mongo/Redis/Qdrant/RabbitMQ are reachable
  only on `vie-network`. Published surface: vie-web (`80`), vie-api (`3000`),
  and vie-admin on **loopback only** (`127.0.0.1:8002` — reach via
  `ssh -L 8002:127.0.0.1:8002 <host>`).
- **No source bind-mounts** — images are immutable; only the shared config
  JSONs (`pipeline-version.json`, `domains.json`) are mounted read-only
  because the prod images don't bake them.
- **`restart: always`**, `mem_limit` on every service, healthchecks everywhere
  (worker uses the heartbeat-file check), `USE_QUEUE_PIPELINE` defaults **true**.
- **TLS** — terminate at a reverse proxy (Caddy/Traefik/nginx) in front of
  ports 80/3000 and keep `TRUST_PROXY=1` (prod compose default) so `req.ip`
  derives from `X-Forwarded-For`.

### Staging boot procedure

```bash
# 1. Create the env file (never commit it)
cp .env.example .env.prod             # then fill EVERY ${VAR:?} secret:
                                      # MONGO_ROOT_USER/PASS, REDIS_PASSWORD,
                                      # RABBITMQ_USER/PASS, JWT_SECRET,
                                      # JWT_REFRESH_SECRET, INTERNAL_SECRET,
                                      # ADMIN_API_KEY, PADDLE_WEBHOOK_SECRET,
                                      # FRONTEND_URL, VITE_API_URL, VITE_WS_URL

# 2. Validate interpolation (fails fast on any missing secret)
docker compose --env-file .env.prod -f docker-compose.prod.yml config --quiet

# 3. Build + boot
docker compose --env-file .env.prod -f docker-compose.prod.yml build
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d

# 4. Verify
docker compose -f docker-compose.prod.yml ps          # all healthy, no 0.0.0.0 datastore binds
curl -fsS http://127.0.0.1:3000/health                # liveness
curl -fsS http://127.0.0.1:3000/ready                 # readiness (mongo/redis/broker)
curl -fsS http://127.0.0.1/                           # web serves
```

Status: config validation verified on the dev box (2026-07-12). A full staging
boot needs a host with ~16 GB free RAM (mem_limits sum to ~17 GB worst-case) —
run the procedure above on the target box before the first real deploy.

### SSR for Shared Pages

Shared video summaries are accessible at `/s/:slug`. The reverse proxy routes
these requests to vie-api for server-side rendering of Open Graph metadata:

```
Browser ──► reverse proxy ──► /s/:slug rewrite ──► vie-api
                                                      │
                                                      ▼
                                                GET /api/share/:slug/ssr
                                                Returns full HTML with OG tags
```

- vie-web (nginx) serves the SPA for all other routes (client-side routing)
- Route `/s/*` at the reverse proxy to `vie-api:3000/api/share/:slug/ssr`
- The SSR response sets `s-maxage=60` + stale-while-revalidate for any caching proxy
- This enables rich link previews on social platforms (Twitter, Discord, Slack)
- (The `vercel.json` left over from the abandoned Vercel plan was removed on
  2026-09-17; the single-host deploy is documented in `DEPLOY.md`.)

### CI/CD

- **GitHub Actions** runs tests on push to `main` / `dev-*` branches and on PRs to `main`
- Four parallel jobs: api, web, summarizer, assistant
- All jobs must pass before merge (fail-fast)
- See `.github/workflows/ci.yml` for configuration
- The `docker-build` job also validates `docker-compose.prod.yml` interpolation (`config -q` with dummy values for the required vars — proves the `${VAR:?}` guards without booting)
- The e2e workflow (`e2e.yml`) appends `CORS_ADDITIONAL_ORIGINS=http://localhost:5273` to its generated `.env` so the Playwright webServer origin passes CORS

---

## Implementation History

This section documents the MVP implementation phases that were followed to build the system.

### Phase Overview

| Phase | Focus           | Status |
| ----- | --------------- | ------ |
| 1     | Infrastructure  | Done |
| 2     | Summarizer      | Done |
| 3     | API             | Done |
| 4     | Frontend Core   | Done |
| 5     | Assistant       | Done |

### Phase 1: Infrastructure

**Goal:** All containers running and communicating.

- Created project structure with api/, web/, summarizer/, assistant/
- Set up Docker Compose with all services
- Implemented security middleware:
  - Rate limiting (10/day per user for videos, 10/min per IP for auth)
  - JWT refresh token flow (15 min access, 7 day refresh)
  - CORS configuration
  - Password validation
  - Security headers via helmet

### Phase 2: Summarizer Service

**Goal:** YouTube URL → Summary in cache.

- Python FastAPI service with SSE streaming
- Transcript fetching via yt-dlp
- Metadata and chapter extraction
- LLM pipeline with parallel processing
- Category detection from YouTube metadata
- Error handling for video edge cases:
  - NO_TRANSCRIPT, VIDEO_TOO_LONG, VIDEO_TOO_SHORT
  - VIDEO_UNAVAILABLE, VIDEO_RESTRICTED, LIVE_STREAM
- Retry logic with exponential backoff

### Phase 3: API Service

**Goal:** REST API with auth, videos, folders.

- Node.js Fastify service with TypeScript
- MongoDB connection and JWT authentication
- WebSocket for real-time updates
- Auth, folders, videos, explain routes
- HTTP client connection to assistant

### Phase 4: Frontend Core

**Goal:** Two-tab interface with folders and videos.

- React + Vite + TypeScript
- Tailwind CSS v4 with shadcn/ui
- React Query for server state
- Auth flow with token refresh
- Folder tree with drag-and-drop
- Video submission with SSE streaming
- Real-time status updates

### Phase 5: Assistant Service

**Goal:** RAG-powered video chat with semantic search.

- Python FastAPI service with Qdrant vector DB
- Video-scoped RAG chat with transcript embeddings
- Cached expansion generation for sections and concepts
- HTTP API integration with vie-api

### Success Criteria (All Achieved)

- [x] Register and login
- [x] Submit YouTube URL
- [x] View cached/new summary with progressive loading
- [x] Browse videos in folders
- [x] Explain sections and concepts
- [x] Chat about videos with RAG
- [x] Organize with folders

### Phase 7+: Beyond MVP (Ongoing)

The system has evolved significantly beyond the original MVP phases:

- **Plan-based pipeline**: Replaced persona detection with LLM classifier + plan stage for adaptive content extraction
- **Assembly stage**: Pure-code stage that converts extraction data into component-addressed `TabEntry[]`
- **Interactive components**: 15+ interactive renderers (Quiz, FlashDeck, Budget, Verdict, Comparison, etc.)
- **Redis response cache**: Full VIEResponse caching for instant serves
- **Qdrant vector DB**: RAG-based video chat with semantic search
- **Frame intelligence**: Vision LLM analysis of video frames, S3 storage, gallery display
- **Chunked extraction**: Chapter-aware batched extraction for long videos (>30min)
- **Admin dashboard**: LLM usage tracking, cost monitoring, video management
- **Sharing**: Public share links with SSR for OG metadata
- **Playlist support**: YouTube playlist import and batch processing
- **Multi-provider LLM**: Anthropic, OpenAI, Gemini via LiteLLM abstraction
