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

The api also records each admin mutation's signed actor header once in Redis (`vie:api:admin-actor-sig:<signature>`, `SET NX EX`, kept as long as the header could still pass its 300 s age check) so a captured header cannot be replayed. Unlike the dispatch guard this one fails closed: with Redis down an admin mutation is refused `503` ([SECURITY.md](./SECURITY.md#admin-api-key)).

The api also keeps `vie:api:video-meta:<youtubeId>` (24 h TTL): the title, channel, duration and thumbnail that `GET /video/metadata` on the summarizer returned, so a pre-flight quote does not re-ask YouTube. A summarizer 404 (unknown or private video) is remembered for 10 minutes under `vie:api:video-meta-miss:<youtubeId>` so a dead id is not looked up again on every submit; no other failure is cached, and a Redis error just skips both caches.

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
    # Single-node replica set (see "MongoDB Setup"); the healthcheck runs rs.initiate
    command: ["mongod", "--replSet", "rs0", "--bind_ip_all", "--oplogSize", "1024"]
    ports:
      - "127.0.0.1:27017:27017"
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
LLM_FALLBACK_PROVIDER=          # Optional: cross-provider fallback after one same-provider retry (primary-model calls)

# Provider API Keys (set for providers you use)
ANTHROPIC_API_KEY=sk-ant-api03-xxxxxxxxxxxxx
OPENAI_API_KEY=                 # Needed by the openai/* stage defaults (description, quiz, translation)
GEMINI_API_KEY=                 # Required if using Gemini

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

# Submit burst guard (Redis key vie:api:submit-burst:<userId>, fixed 1 h window):
# submissions per user per hour, one bucket for POST /api/videos, the assistant's
# generate route and playlist import. 0 = off. Fails open if Redis is down.
SUBMIT_BURST_LIMIT_PER_HOUR=20

# Chat fair use (Redis key vie:api:chat-fair-use:<userId>, rolling 24 h sorted set):
# messages a FREE account may send, one allowance for both chat routes. Pro is
# unlimited. 0 = off. Fails open if Redis is down.
CHAT_FREE_MESSAGES_PER_DAY=50

# Billing repair job (billing-roles P1.6), in vie-api, every interval behind the
# Redis lock vie:api:billing-repair (SET NX EX, TTL = the interval):
#   settle/release holds older than BILLING_HOLD_MAX_AGE_HOURS whose run is
#   terminal, missing, unstamped or stalled; expire lots past their expiry;
#   apply due recurring grants (a lot that expires at the next run); check
#   changed accounts against the ledger (drift → warn + llm_alerts row
#   `billing_drift`, once per account per 24 h); unset llm_usage.user_id older
#   than 90 days; run the GDPR hard-delete sweep. Unset = on (off under
#   NODE_ENV=test); both composes default to true. The minutes ledger decides
#   every submit (no switch, no dollar cap — P3.2), so Mongo must run as a
#   replica set before this code is deployed.
BILLING_REPAIR_ENABLED=true
BILLING_REPAIR_INTERVAL_SECONDS=300
# must exceed the job queue's message TTL (1 h, queue-topology.ts) — boot refuses otherwise
BILLING_HOLD_MAX_AGE_HOURS=3

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

### Summarizer settings passed through compose

`vie-summarizer` and `vie-summarizer-worker` get their environment from the `x-summarizer-env` anchor of each compose file (`docker-compose.yml` on dev, `docker-compose.prod.yml` on the server) plus a few service-level lines. **Only what is listed here reaches the containers**: a `config.py` setting without a compose line keeps its code default however `.env` sets it (list after the tables). Each passthrough is `${VAR:-default}`; the Default column is that compose default — "dev / prod" when the two files differ, *required* = `${VAR:?}` (compose refuses to start without it), "prod only" = dev compose does not pass it (the container keeps the `config.py` default). Booleans carry literal defaults: a blank boolean fails settings validation and crash-loops the container. `tests/test_config.py` checks the compose defaults of the tuning settings against `config.py`.

**LLM providers and models**

| Var | Default | Purpose |
|-----|---------|---------|
| `LLM_PROVIDER` | `anthropic` | Primary provider (`anthropic` / `openai` / `gemini`); its default model (`MODEL_MAP` in `config.py`; anthropic: `anthropic/claude-sonnet-5-5`) is the primary model. In the summarizer only the plan (at effort medium, `PLAN_EFFORT` in `plan.py`) and `/health` read it — every other stage has its own model below or runs on the fast tier |
| `LLM_FAST_PROVIDER` | blank | Provider of the fast tier (synthesis, chapter_detect; the faithfulness judge when `LLM_JUDGE_MODEL` is blank); blank = `LLM_PROVIDER` (anthropic: Haiku 4.5), `openai` = `gpt-4o-mini` |
| `LLM_FALLBACK_PROVIDER` | blank | Cross-provider fallback for primary-model calls after one same-provider retry (`call_llm_with_retry`): that provider's default model. Fast-tier calls never fall back; blank = none |
| `LLM_MODEL` | blank | Primary-model override (the plan; vie-assistant reads it too); blank = `LLM_PROVIDER`'s default model |
| `LLM_FAST_MODEL` | blank | Fast-tier override; blank = the fast provider's fast model |
| `LLM_TIMEOUT_SECONDS` | `60.0` | Per-call timeout for calls that set none themselves |
| `LLM_EXTRACTION_MODEL` | `anthropic/claude-sonnet-5-5` | Every extraction call (field groups, batches), at effort low; the group calls end their rules with the completeness rule (`extraction_group_rules.txt`) |
| `LLM_MEMORY_MODEL` | `anthropic/claude-haiku-5-5` | The memory call; blank = `LLM_EXTRACTION_MODEL` (config only — under compose a blank line keeps Haiku 5.5). Rollback: `anthropic/claude-haiku-4-5-20251001` |
| `LLM_JUDGE_MODEL` | `openai/gpt-6-luna` | The faithfulness judge (informational score, D22), reasoning off; blank = the fast tier (config only — under compose a blank line keeps gpt-6-luna). Rollback: `openai/gpt-4o-mini` |
| `LLM_ENRICHMENT_MODEL` | `openai/gpt-4o-mini` | The quiz call |
| `LLM_DESCRIPTION_MODEL` | `openai/gpt-6-luna` | Description analysis |
| `LLM_SYNTHESIS_MODEL` | blank | Synthesis (master summary, SEO description); blank = the fast tier |
| `LLM_TRANSLATION_MODEL` | `openai/gpt-6-luna` | Output and title translation, and the RAG transcript's English text |
| `LLM_VISION_MODEL` | `anthropic/claude-sonnet-5-5` | Frame descriptions |
| `LLM_CLASSIFIER_MODEL` | `anthropic/claude-haiku-4-5-20251001` | Tier-probe model (D21; the name predates the probe) |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY` | blank | Provider keys, read by LiteLLM. `OPENAI_API_KEY` is needed by the `openai/*` stage defaults (description, quiz, translation, the judge) and an `openai` fast tier, and enables the Whisper audio fallback; a `GEMINI_API_KEY` alone enables the Gemini audio fallback |

The per-stage defaults are the pipeline-1min 4.0d model A/B picks, rounds 1 + 2 (rule: quality within noise first, then latency, then cost; evidence in the pipeline-1min gate g4-model-ab report, kept in the owner's local task notes), carried as the same literal default by `config.py` and both anchors — a blank `.env` line keeps the pick; only `LLM_SYNTHESIS_MODEL` defaults blank (the fast tier). The plan (the primary, via `MODEL_MAP`) and vision moved to Sonnet 5.5 (p50 11.3 vs 23.6 s and 8.6 vs 16.8 s against Sonnet 4.6; for vision Haiku 5.5 labelled chef-at-work frames `talking_head`), description analysis and every translation to `gpt-6-luna` (no invented URLs; RAG batches parse 4/4 vs 3/4). Extraction moved to Sonnet 5.5 at effort low with the completeness rule (round 2: the fewest missed transcript-supported items, no invented details — Haiku 4.5 invented steps, quantities and code snippets), memory to Haiku 5.5 on its own `LLM_MEMORY_MODEL`; the tier probe stays on Haiku 4.5 (the probe matched 16/18 visual-demo labels vs 18/18), the quiz on `gpt-4o-mini` (`gpt-6-luna` as usable, p50 10.5 s vs 5.2 s), and synthesis and chapter_detect on the fast tier. The faithfulness judge moved to `gpt-6-luna` on its own `LLM_JUDGE_MODEL` at the pipeline-1min close-out (informational; the golden baseline re-scored under it, `gates/g4.md` §10).

**Extraction + vision concurrency (2026-10, pipeline-1min)**

| Var | Default | Purpose |
|-----|---------|---------|
| `EXTRACTION_PARALLEL` | `true` | `true`: field groups (phase 3) — text groups fire at plan-done, the visual group at frames-done, under `EXTRACTION_PARALLEL_BATCHES`; `false`: today's single extraction call after frames (chunked batches one at a time), byte-identical request for the same plan. Extraction-only: the plan-done/frames-done reconcile, the `<video_memory>` evidence ranges, the toolkit markers and `PIPELINE_VERSION` stay with either value — a full rollback is a redeploy of the previous `main` |
| `EXTRACTION_PARALLEL_BATCHES` | `6` | Max extraction LLM calls in flight per run (was 2). The semaphore is per run: a worker with `WORKER_CONCURRENCY=2` holds up to 12. `pipeline.timing` counts 429s per run (blind to a concurrent run's 429s) — drop to `4` if any appear |
| `CHUNKED_EXTRACTION_THRESHOLD` | `900` | Videos longer than this (s) may split the transcript into chapter batches |
| `MAX_TOKENS_PER_BATCH` / `MAX_MINUTES_PER_BATCH` | `50000` / `40` | Limits of one extraction batch: estimated transcript tokens, minutes of video |
| `EXTRACTION_FORCE_SPLIT_CHUNKS` | `4` | Sub-batches when a long video's chunked transcript collapses to one batch |
| `HERO_REWRITE_ENABLED` | `true` | Rollback lever: `true` = one `hero_rewrite` call on `LLM_EXTRACTION_MODEL` (reads the extraction's prompt cache) writes the hero and replaces the synthesis call; `false` = memory hero + synthesis |
| `QUIZ_V2_ENABLED` | `true` | Rollback lever: `true` = quiz v2 on `LLM_EXTRACTION_MODEL`, reading the extraction's prompt cache (every question carries its answer's timestamp and a verified evidence quote; instructional formats only — `quizPolicy.formats`); `false` = the `enrich_quiz` call on `LLM_ENRICHMENT_MODEL` |
| `MERGE_RANK_ENABLED` | `true` | Rollback lever: `true` = one `merge_rank` call on `LLM_MEMORY_MODEL` on chunked runs (cross-tab dedupe, sections, rank); `false` = the code merge alone |
| `FRAME_VISION_ENABLED` | `true` | Frame descriptions by the vision model; `false` = OCR only |
| `FRAME_VISION_PARALLEL` | `true` | Vision in parallel batches; `false` = one call with every frame |

**Frames and YouTube downloads**

| Var | Default | Purpose |
|-----|---------|---------|
| `SCENE_EXTRACTION_ENABLED` | `true` | Scene-based frames (detection, scoring, OCR, vision); `false` = the run has no frames |
| `SCENE_HIRES_ENABLED` | `true` | Pass-2 720p refinement of selected frames |
| `SCENE_HIRES_FALLBACK_TIMEOUT` | `180` | Seek budget (s) for the hi-res frames in the run's one local ≤720p download |
| `SCENE_S3_PREFIX` | `scenes-v4` | Versioned frame-cache prefix — bump to invalidate the S3 frame cache |
| `FRAME_TIER_ENABLED` | `true` | Adaptive visual tiers (high/standard/low from `domains.json` `visualCriticality`) |
| `FRAMES_SKIP_WHEN_NO_VISUAL_DEMO` | `true` | Rollback lever (output-quality 3.5, D2): a video over 30 min whose tier probe saw no visual demo, tier not HIGH, skips scene detection + vision when its tier is LOW or its probe format is in `visualCriticality.skipFormats` (`[]` = LOW only); the 360p download closes, the moment fill frames every moment from the 720p file. `false` = the skip only (no probe wait): the 3.5 `highTitleKeywords` fix ("pack opening" / "box opening": a bare "opening", e.g. an "Opening Keynote", no longer makes a title HIGH) applies either way |
| `YTDLP_PLAYER_CLIENTS` | `android` | yt-dlp player clients for all audio downloads and the video downloads' retry (both video downloads try `YTDLP_HIRES_PLAYER_CLIENTS` first). YouTube 403s the web client's URLs from some environments. **Never mix in `default`** — a merged format list lets bestvideo pick a 403ing web DASH format. Metadata/subtitle extraction deliberately doesn't use it. |
| `YTDLP_HIRES_PLAYER_CLIENTS` | `web_embedded,android` | Player clients both video downloads try first — the 360p pass-1 file (scene detection) and the ≤720p file (hi-res frames + moment fill). `android` alone serves only the muxed 360p rendition (format 18, audio included: 2-4× a 360p video-only file, often bigger than the 720p one — g4 §8); measured 2026-10-06 with real downloads, `web_embedded`/`tv_embedded` get 720p. A failed download retries once with `YTDLP_PLAYER_CLIENTS` |
| `YOUTUBE_PROXY_URL` | — | Proxy for every YouTube request (`http://user:pass@host:port`). Every run downloads one ≤720p file (through the proxy when set); hi-res frames and moment fills seek it — there are no stream-URL seeks |
| `YOUTUBE_PROXY_EXIT_COUNT` | `1` | Sticky exits behind the proxy gateway (Webshare `USERNAME-1…N`). >1 lets a 429 or YouTube's "Sign in to confirm you're not a bot" check move to the next exit on every proxied call — metadata, timedtext and caption fetches (every exit; no new one after 15 s, at least 3), the low-res/720p/audio downloads and playlist lookups (at most 3) — and lets a caption 429 retry there before the 15-min caption marker is written. Each job starts on its own round-robin exit; recently blocked exits go last (per process). Set it to the plan's exit count, or one blocked exit takes the service down |

**Transcript**

| Var | Default | Purpose |
|-----|---------|---------|
| `TRANSCRIPT_CLEANING_ENABLED` | `false` | spaCy + TF-IDF pass over the stored transcript text (Qdrant chunks, faithfulness, S3 blob). No prompt reads that text (prompts render `[m:ss]` segments), so it is off by default — it only added wall time before `transcript_ready` |
| `TRANSCRIPT_CLEANING_TIMEOUT` | `30` | Budget (s) for that pass; the first call per process pays the spaCy cold start |
| `HF_TOKEN` | empty | Optional Hugging Face Hub token for SentenceTransformer pulls |

**Storage (S3)**

| Var | Default | Purpose |
|-----|---------|---------|
| `S3_BUCKET` | `vie-transcripts` | Bucket for frames, transcripts and audio (the server template sets its own production bucket — never share it with dev) |
| `S3_PRESIGNED_URL_EXPIRY` | `21600` (was 3600) | Must stay ≥ api `FRAME_URL_TTL_SECONDS` |
| `AWS_REGION` | `us-east-1` / *required* | Region of `S3_BUCKET`; presigned frame URLs break when it is wrong |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | blank | Static keys, only where no instance role supplies credentials (the EC2 role does on the server) |
| `AWS_ENDPOINT_URL` | blank | Custom S3 endpoint (LocalStack); must be browser-reachable, because the api re-signs frame URLs against it |

**Observability and alerts**

| Var | Default | Purpose |
|-----|---------|---------|
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` | blank | Langfuse tracing and the prompt registry; tracing is off unless both are set |
| `LANGFUSE_BASE_URL` | `https://cloud.langfuse.com` | Langfuse host |
| `LANGFUSE_FAITHFULNESS_SAMPLE_RATE` | `0.2` | Share of extracted items the faithfulness judge checks; `0` disables the judge |
| `LANGFUSE_USER_ID_MODE` / `LANGFUSE_USER_ID_HASH_SALT` | `identity` / `hash`; blank salt | How user ids reach traces (`identity`, `hash` = salted SHA-256, `omit`) and the salt of the hash mode |
| `PROMPT_SOURCE` | `registry` | Where prompt templates load from: `registry` (the Langfuse `production` label wins over the local `.txt` whenever keys are set) or `disk` (always the local `.txt`, re-read on every call — dev-only, for prompt edits without re-registering or restarting; honoured only when `ENVIRONMENT` is `""`/`development`/`dev`/`test`/`local`, ignored otherwise; traces carry no `promptVersions`) |
| `SENTRY_DSN` / `SENTRY_ENVIRONMENT` / `SENTRY_RELEASE` | empty (`SENTRY_ENVIRONMENT`: empty / `production`) | Optional Sentry error reporting (see [OBSERVABILITY.md](./OBSERVABILITY.md)) |
| `SENTRY_TRACES_SAMPLE_RATE` | `0.1` | Share of transactions Sentry traces; `0` disables tracing (errors still report) |
| `ALERT_WEBHOOK_URL` | empty | Every `llm_alerts` row is also POSTed here as JSON (Slack/Discord/ntfy). Empty = Mongo-only, nothing pushes |
| `ALERT_COST_THRESHOLD_USD` | `0.50` | Per-call `high_cost_call` alert threshold (`llm_common/callback.py`) |
| `ALERT_DAILY_COST_THRESHOLD_USD` | `20` | vie-admin `daily_global_cost` alarm: global spend since UTC midnight (≤ 0 disables) |

**Runtime**

| Var | Default | Purpose |
|-----|---------|---------|
| `PYTHONUNBUFFERED` | `"1"` (literal) | Unbuffered stdout/stderr, so logs reach `docker logs` at once |
| `LOG_LEVEL` | `info` | Log verbosity |
| `INTERNAL_SECRET` | dev placeholder / *required* | Shared secret for internal calls between vie-api, the summarizer, the worker and the assistant |
| `ENVIRONMENT` | prod only: `production` | `production` arms the default-`INTERNAL_SECRET` refusal and the SSE soft-fail and keeps `PROMPT_SOURCE=disk` off; dev keeps `config.py`'s `""` (a dev name) |
| `LOG_FORMAT` | prod only: `json` | `json` = CloudWatch-parseable logs; dev keeps `console` |
| `API_URL` | prod only: `http://vie-api:3000` | Target of the `/internal/status` callbacks (dev keeps the same `config.py` default) |
| `MAX_VIDEO_DURATION_MINUTES` / `WHISPER_MAX_DURATION_MINUTES` | prod only: `600` / `600` | Longest accepted video, and longest video allowed onto the paid audio fallback (the server template sets the demo's 30 / 30) |
| `STALL_SWEEP_ENABLED` / `STALL_SWEEP_INTERVAL_SECONDS` / `STALL_THRESHOLD_MINUTES` | `true` / `300` / `30` | Summarizer stall sweeper — fails `processing` rows with no progress + no live producer lock, alerts `pipeline_stalled` |

**Service-level lines (not the anchor)**

| Var | Default | Purpose |
|-----|---------|---------|
| `MONGODB_URI` / `REDIS_URL` | literal compose URLs (prod: built from `MONGO_ROOT_*` / `REDIS_PASSWORD`) | MongoDB and Redis of the compose network. No `replicaSet=` and no `directConnection`: the drivers discover the one-member set from `vie-mongodb:27017` |
| `MONGO_KEYFILE_PATH` | prod only, required (template: `/opt/vie/secrets/mongo-keyfile`) | Host path of the replica-set keyfile, bind-mounted read-only at `/etc/mongo/keyfile`. Mode `0400`, owner `999:999` ([DEPLOY.md](./DEPLOY.md), section d) |
| `QDRANT_HOST` / `QDRANT_PORT` | `vie-qdrant` / `6333` (literal) | Qdrant for the RAG chunks |
| `RABBITMQ_URL` (worker) | built from `RABBITMQ_DEFAULT_USER/PASS` (dev) / `RABBITMQ_USER/PASS` (prod) | Job queue the worker consumes |
| `WORKER_CONCURRENCY` (worker) | `2` | Pipeline runs one worker process holds at once; every per-run cap multiplies by it (the env templates set `1`) |
| `WORKER_MAX_RETRIES` (worker) | `3` | Re-publishes of a failed job before it is dead-lettered |
| `WORKER_RETRY_BACKOFF_SECONDS` (worker) | `5` | Worker retry delay, doubled per attempt (5 → 10 → 20 s), capped at 120 s |

**Not passed through — `config.py` defaults only.** Setting any of these in `.env` changes nothing in the containers until both anchors pass it: `LLM_CHAPTER_DETECT_MODEL` (chapter_detect therefore runs on the fast tier), `MIN_VIDEO_DURATION_SECONDS`, `TRANSCRIPT_FETCH_TIMEOUT`, `SPONSORBLOCK_TIMEOUT`, `MUSIC_LANGUAGE_FORCE_EN_WPS`, `CAPTION_429_NEG_TTL_SECONDS`, `WHISPER_ENABLED`, `WHISPER_CLIENT_TIMEOUT_SECONDS`, `WHISPER_MAX_RETRIES`, `WHISPER_CHUNK_CONCURRENCY`, `GEMINI_UPLOAD_TIMEOUT`, `SCENE_THRESHOLD`, `SCENE_DETECT_SCALE_WIDTH`, `SCENE_JPEG_QUALITY`, `SCENE_HIRES_CONCURRENCY`, `FRAME_VISION_MAX_FRAMES`, `FRAME_VISION_TIMEOUT`, `FRAME_OVERSELECT_COUNT`, `FRAME_RESELECT_FLOOR`, `QDRANT_ENABLED`, `EMBEDDING_MODEL_NAME`, `REDIS_ENABLED`, `REDIS_CACHE_TTL`, `PIPELINE_LOCK_TTL_SECONDS`, `PIPELINE_STREAM_TTL_SECONDS`, `PIPELINE_STREAM_MAXLEN`, `SSE_HEARTBEAT_SECONDS`, `WORKER_PREFETCH`.

### Other tuning vars

| Var | Default | Purpose |
|-----|---------|---------|
| `EVAL_USER_EMAIL` / `EVAL_USER_PASSWORD` | `eval@vie.local` / none | Eval-runner local account — auto-registered on first run; no default password by design (also a CI secret) |
| `VITE_SENTRY_DSN` / `VITE_SENTRY_RELEASE` | empty | Browser Sentry — compose **build args**, baked into the web bundle (rebuild `vie-web` after changing) |
| `QDRANT_URL` (vie-admin) | `http://vie-qdrant:6333` | Admin health poller's Qdrant `/readyz` probe |
| `BACKUP_KEEP` / `BACKUP_MAX_AGE_HOURS` | `14` / `26` | Backups retained by `scripts/backup.sh` / the `vie-backup-cron` sidecar; age past which vie-admin's `backup_stale` dead-man's switch trips |

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

# MongoDB (dev has no auth; in prod add -u "$MONGO_INITDB_ROOT_USERNAME" -p ... --authenticationDatabase admin inside `sh -c`)
docker exec vie-mongodb mongosh --quiet --eval "db.runCommand('ping')"
docker exec vie-mongodb mongosh --quiet --eval "printjson({ ok: rs.status().ok, state: rs.status().members[0].stateStr })"   # { ok: 1, state: 'PRIMARY' }

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
  "plan": "free | paid",              // what the run's minutes hold paid with (billing-roles P3.4)
  "priority": 1 | 5,                  // free=1 (the free video), paid=5 (any plan or credit minutes)
  "providers": { "default": "...", "fast": "...", "fallback": "..." } | null,
  "bypassCache": false,
  "requestId": "uuid",
  "attempt": 1,                       // 1-indexed; incremented on republish
  "createdAt": "ISO-8601"
}
```

`plan` replaced `tier` in P3.4: the priority follows what pays for the run,
not the account's plan (a Pro account's free video queues at 1, a free
account's credit minutes at 5). The `x-plan` header carries it too, and the
worker tags the job's Sentry transaction with `plan`. A message published
before the deploy still carries `tier`; the worker ignores it (`plan` then
reads `free` for the tag only — the message keeps its priority).

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

**Admin endpoints** (require the `X-Admin-Key` header; `POST /replay` also needs a signed `X-Admin-Actor` naming an admin with `queue:replay`, see [SECURITY.md](./SECURITY.md#admin-api-key)):

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/admin/queue/stats` | Depth, in-flight, DLQ depth (RabbitMQ management API) |
| GET | `/api/admin/queue/dlq` | List messages in `vie.pipeline.dlq` |
| POST | `/api/admin/queue/replay` | Re-publish all DLQ messages back to `vie.pipeline.jobs` |

---

## MongoDB Setup

### Single-node replica set (`rs0`)

Dev and prod run `mongod --replSet rs0` with exactly one member, `vie-mongodb:27017`. The only reason is multi-document transactions (the billing ledger writes several documents atomically), which MongoDB allows on replica sets only. There is no second node and no failover.

| | Dev (`docker-compose.yml`) | Prod (`docker-compose.prod.yml`) |
|---|---|---|
| Command | `mongod --replSet rs0 --bind_ip_all --oplogSize 1024` | the same plus `--keyFile /etc/mongo/keyfile` (auth + replica set requires a keyfile) |
| Auth | none | root user from `MONGO_ROOT_USER` / `MONGO_ROOT_PASS`, keyfile from `MONGO_KEYFILE_PATH` |
| Healthcheck | initiates the set on the first probe, then exits 0 only while the node is PRIMARY | the same, authenticated with the container's own `MONGO_INITDB_ROOT_*` |
| Host port | `127.0.0.1:27017` | none |

**Initiation is the healthcheck's job.** Each probe calls `rs.status()`; on `NotYetInitialized` it runs `rs.initiate({ _id: 'rs0', members: [{ _id: 0, host: 'vie-mongodb:27017' }] })`, then exits 1 until `db.hello().isWritablePrimary`. Every `depends_on: service_healthy` therefore waits for a node that accepts writes, and `docker compose up --wait` (the e2e job) does too. The probe is idempotent: a restart or recreate of the container with the same volume keeps the config and goes healthy on the first probe. A fresh data directory is healthy after about 15 s.

**Connection strings.**

- **In the compose network** the URIs are unchanged (`mongodb://vie-mongodb:27017/video-insight-engine`, prod adds the root credentials and `?authSource=admin`). With no `replicaSet=` and no `directConnection`, a driver seeded with one host asks the node for its set and follows the member list, which names the same `vie-mongodb:27017`. Verified against Node driver 6.21 (`ReplicaSetWithPrimary`, transactions commit and abort) and pymongo 4.17. `replicaSet=rs0` is deliberately not added: it would add nothing here and would stop the same URI from working against a standalone Mongo (a rollback, a CI service container).
- **On the host** (`localhost:27017`) the member list names `vie-mongodb`, which the host cannot resolve, so a normal URI fails with `getaddrinfo ENOTFOUND vie-mongodb` (or a server-selection timeout). Every host-side URI needs `directConnection=true`: `mongodb://localhost:27017/video-insight-engine?directConnection=true`. `.env.example`, the `scripts/` defaults and the Playwright smoke seed carry it. A `.env` copied from an older `.env.example` needs the suffix added by hand. `mongosh` with no URI, and `docker exec vie-mongodb mongosh`, already connect directly.

**Oplog.** A replica set keeps an oplog. The default is 5% of free disk (up to 50 GB; 44.5 GiB on the dev box), which would let it grow into the dev disk; with one member nothing replicates from it, so both compose files cap it at `--oplogSize 1024` (MB). The size is applied when the set is initiated; on an existing set, change it with `db.adminCommand({ replSetResizeOplog: 1, size: 1024 })`.

**Tests.** `api/src/test/setup.ts` starts a `MongoMemoryReplSet` (one node) for every test file, so `session.withTransaction` works in any api test; `api/src/plugins/__tests__/transactions.test.ts` pins the commit and abort behaviour. A replica set costs about 1.3 s more per file than a standalone `MongoMemoryServer` (0.4 s start, 0.9 s stop). Files that create their own `MongoMemoryServer` stay standalone and cannot use transactions.

**Converting an existing volume** needs no data migration: restarting the same data directory with `--replSet rs0` and letting the healthcheck initiate it keeps every document. Dev: `docker compose up -d vie-mongodb`, then restart the services that hold Mongo connections. Prod: see [DEPLOY.md](./DEPLOY.md), "Convert Mongo to a replica set". Rolling back is a redeploy of the previous compose file; the volume starts as a standalone again with its data (tested).

### Indexes

Collections are created automatically. To set up indexes:

```javascript
// Connect to MongoDB (directConnection: the replica set's member name does not resolve on the host)
mongosh "mongodb://localhost:27017/video-insight-engine?directConnection=true"

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

Both scripts work unchanged against the replica set (verified 2026-10-28 with a scratch auth + keyfile replica set built from `docker-compose.prod.yml`: `backup.sh` of a standalone volume, conversion of the same volume, `backup.sh` of the replica set, `restore.sh` of that archive into a second replica set; counts matched). `mongodump` and `mongorestore` run inside the container and connect to `localhost:27017` directly, so no flag changed, and neither uses `--oplog`. They also work unchanged against the auth-enabled prod stack: mongodump/mongorestore auth args are resolved inside the Mongo container from its own `MONGO_INITDB_ROOT_*` env, so no secrets cross the host boundary and the same commands run on the authless dev stack. Pruning deletes only timestamp-named (`YYYYMMDDTHHMMSSZ`) directories under the backup root (newest `BACKUP_KEEP` kept), so unrelated dirs under a custom root are never touched. An unreachable Qdrant aborts with an explicit error; set `QDRANT_SKIP=1` for a deliberate Mongo-only backup (vectors are re-derivable).

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
| Billing repair job | `api/src/services/billing/billing-repair.service.ts` | safe — one run at a time across processes via the Redis lock `vie:api:billing-repair`; per-account writes are transactions | — |
| Billing account write queue + shadow runner | `api/src/services/billing/keyed-mutex.ts`, `shadow-runner.ts` | in-process ordering only; other replicas meet as transaction write conflicts (retried) | — |
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
  Mongo is a single-node replica set, which with auth needs a keyfile
  (`MONGO_KEYFILE_PATH`, created once on the host — [DEPLOY.md](./DEPLOY.md)).
- **No published datastore ports** — Mongo/Redis/Qdrant/RabbitMQ are reachable
  only on `vie-network`. Published surface: vie-web (`80`), vie-api (`3000`),
  and vie-admin on **loopback only** (`127.0.0.1:8002` — reach via
  `ssh -L 9002:127.0.0.1:8002 <host>`, then `http://localhost:9002`; any
  free local port works, but local 8002 is taken when the dev stack runs on
  the same machine).
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
                                      # MONGO_ROOT_USER/PASS, MONGO_KEYFILE_PATH
                                      # (a 0400, uid-999 file — DEPLOY.md), REDIS_PASSWORD,
                                      # RABBITMQ_USER/PASS, JWT_SECRET,
                                      # JWT_REFRESH_SECRET, INTERNAL_SECRET,
                                      # ADMIN_API_KEY,
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

- **GitHub Actions** runs the `CI` workflow (`.github/workflows/ci.yml`) on push to `main` / `dev-*` branches and on PRs to `main`
- Eleven parallel jobs, none depending on another: `ts-quality` (typecheck + lint), `design-guards`, `python-lint` (ruff), seven test suites (`api`, `web`, `summarizer`, `assistant`, `admin-backend`, `admin-ui`, `llm-common`; the summarizer and assistant jobs also run pyright) and `docker-build`
- `e2e.yml` runs the Playwright smoke project against the Compose API stack on PRs to `main`. It seeds a completed video into MongoDB, so no LLM call is made
- `eval.yml` runs on PRs that touch prompts, the pipeline, vector code or the golden dataset: a zero-spend dry-run of the golden-dataset harness and a retrieval gate (recall@k + MRR floors against a real Qdrant). A weekly scheduled job runs the golden dataset through the live pipeline and publishes to Langfuse
- `deploy.yml` deploys to the EC2 host after a successful `CI` run on `main` (see [DEPLOY.md](./DEPLOY.md))
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
