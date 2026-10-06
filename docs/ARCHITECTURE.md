# Architecture

System overview and data flows.

---

## System Diagram

```
┌───────────────────────────────────────────────────────────────────┐
│                         vie-web (React 19)                         │
│  Tailwind v4 · shadcn/ui · 29 registered interactive renderers    │
│  ComposableOutput → COMPONENT_REGISTRY[tab.component] → render    │
│  VideoPlayerContext (seekTo) · SSE stream consumer                 │
└──────────────────────────┬────────────────────────────────────────┘
                           │ SSE (Server-Sent Events)
                           ↓
┌───────────────────────────────────────────────────────────────────┐
│                        vie-api (Node.js · Fastify)                 │
│  Routes · Auth · MongoDB CRUD · Orchestration                      │
└──────────────────────────┬────────────────────────────────────────┘
                           │ HTTP
                           ↓
┌───────────────────────────────────────────────────────────────────┐
│                    vie-summarizer (Python · FastAPI)                │
│  Pipeline: metadata → [transcript + frames] parallel               │
│           → plan (classifier + triage) → extraction                │
│           → synthesis → enrichment → assembly → [translation]      │
│  Storage: MongoDB + Redis (cache) + S3 (frames/transcripts)       │
│  Vector:  Qdrant (background embedding storage)                    │
└───────────────────────────────────────────────────────────────────┘
```

---

## Service Communication

| From           | To             | Protocol       | Purpose                          |
| -------------- | -------------- | -------------- | -------------------------------- |
| vie-web        | vie-api        | HTTP/SSE       | API calls, streaming updates     |
| vie-api        | vie-mongodb    | MongoDB driver | Data operations                  |
| vie-api        | vie-summarizer | HTTP POST      | Trigger summarization (legacy path; queue path uses RabbitMQ) |
| vie-api        | vie-assistant  | HTTP           | Explain + RAG video chat         |
| vie-api        | vie-rabbitmq   | AMQP           | Publish video.process jobs (when `USE_QUEUE_PIPELINE=true`) |
| vie-api        | vie-redis      | Redis          | Dispatch guard (`vie:api:dispatched:*`) — same instance as the summarizer; see [IDEMPOTENCY.md](./IDEMPOTENCY.md#dispatch-guard-publish-layer) |
| vie-summarizer | vie-mongodb    | MongoDB driver | Save structured results          |
| vie-summarizer | vie-redis      | Redis          | Response cache + per-video pipeline lock (same instance the api uses for the dispatch guard) |
| vie-summarizer | vie-qdrant     | HTTP           | Vector storage (background)      |
| vie-summarizer | S3             | HTTP           | Frame + transcript storage       |
| vie-summarizer | LLM APIs       | HTTP           | LiteLLM (Anthropic/OpenAI/Google)|
| vie-assistant  | vie-mongodb    | MongoDB driver | Cache lookups                    |
| vie-assistant  | vie-qdrant     | HTTP           | RAG vector search                |
| vie-assistant  | LLM APIs       | HTTP           | LLM generation                   |

---

## Data Flows

### 1. Video Summarization

```
User submits YouTube URL
         │
         ▼
┌─────────────────────┐
│ vie-api checks      │
│ videoSummaryCache    │
└──────────┬──────────┘
           │
    ┌──────┴──────┐
    │             │
  HIT           MISS
    │             │
    ▼             ▼
 Create      POST /summarize
 userVideo   to vie-summarizer
 reference        │
    │             ▼
    │    ┌────────────────────┐
    │    │ vie-summarizer     │
    │    │                    │
    │    │ 1. Redis cache chk │
    │    │    HIT → instant   │
    │    │    MISS → continue │
    │    │ 2. Transcript      │
    │    │ 3. Plan + extract  │
    │    │ 4. Assemble        │
    │    │ 5. Save cache      │
    │    └───────┬────────────┘
    │            │
    │     Status: done
    │     (DB + Redis update)
    │            │
    └─────┬──────┘
          │
          ▼
    User sees summary
    (via SSE stream)
```

### 2. Explain Auto (Cached)

```
User clicks "Explain" on section
         │
         ▼
┌─────────────────────┐
│ vie-api calls       │
│ vie-assistant HTTP  │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ vie-assistant checks│
│ systemExpansionCache│
└──────────┬──────────┘
           │
    ┌──────┴──────┐
    │             │
  HIT           MISS
    │             │
    ▼             ▼
 Return       Generate
 cached       with LLM
    │             │
    │        Save to cache
    │             │
    └──────┬──────┘
           │
           ▼
     Return expansion
```

### 3. Video Chat (Ephemeral)

```
User sends message about video
         │
         ▼
┌─────────────────────┐
│ vie-api calls       │
│ vie-assistant HTTP  │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ vie-assistant:       │
│ 1. RAG vector search│
│ 2. Build context    │
│ 3. Call LLM         │
└──────────┬──────────┘
           │
           ▼
     Return response
     (ephemeral, no persistence)
     Chat history in React state
```

---

## Pipeline Flow

Content type is determined by a **plan phase** that runs a classifier (fast model) concurrently with a plan LLM call to pick content tags and design tab layout.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           PIPELINE FLOW                                     │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  1. METADATA (yt-dlp, ~1-3s)                                              │
│     └── Title, channel, thumbnail, duration, chapters                      │
│                                                                             │
│  2. TRANSCRIPT + FRAMES (parallel, ~10-30s)                                │
│     ├── Transcript: S3 cache → yt-dlp → API → Gemini → Whisper           │
│     └── Frames: two-pass — worst-quality detect/score → select ~25 →      │
│         720p hi-res refine (seek; local 720p when proxied) → S3 (scenes-v3│
│         manifest-v2 cache); adaptive visual tier (high/standard/low)      │
│                                                                             │
│  2.5 VISUAL CONTEXT INJECTION (~0-1s)                                      │
│     └── [VISUAL at M:SS] annotations injected into transcript              │
│                                                                             │
│  3. CLASSIFIER + PLAN (concurrent, ~2-5s)                                  │
│     ├── Classifier: fast model, domain + format detection (non-blocking)   │
│     └── Plan: Sonnet call → contentTags, tab layout, extractionGuidance   │
│     ├── 14 primary tags: learning, tech, fitness, food, music, travel,    │
│     │   review, project, language, science, gaming, news, podcast, sport  │
│     ├── 2 modifier tags: narrative, finance                                │
│     ├── Domain playbooks ("<domain>:<format>") injected into the prompt;  │
│     │   post-validation strips `forbidden` components before extraction    │
│     └── Classifier overrides category_hint when confidence > 0.6          │
│                                                                             │
│  4. EXTRACTION (1-5+ LLM calls, ~10-60s)                                  │
│     ├── Schema-injected: base_extraction.txt + schemas/{tag}.txt           │
│     ├── Short: single call; Long (>30min): chunked by chapters             │
│     ├── Pydantic validation + count check                                  │
│     └── Quality check: synthesis-fed retry if coverage < 0.6               │
│                                                                             │
│  5. SYNTHESIS (1 LLM call, ~5-10s)                                        │
│     └── TLDR, takeaways, master summary (fast model)                       │
│                                                                             │
│  6. ENRICHMENT (0-1 LLM call, ~5-10s)                                     │
│     └── Quiz + flashcards + scenarios (domains with enrichment mapping)    │
│                                                                             │
│  7. ASSEMBLY (code, 0 LLM calls — may run moment frame fill I/O)          │
│     ├── 29 assemblers in ASSEMBLER_REGISTRY                                │
│     ├── Extraction → TabEntry[] with component-addressed props             │
│     ├── Domain forbidden/max policy + degrade-never-drop demote ladder     │
│     ├── Frame thumbnail injection (+ ±15s backfill, exact-ts moment fill)  │
│     └── Cross-tab link resolution                                          │
│                                                                             │
│  8. TRANSLATION (conditional — non-English only, 1-2 LLM calls)           │
│     ├── translate_to_source(): one flat-list Haiku call,                   │
│     │   mirror-detection, deep-copy into nested sourceLanguage block       │
│     ├── Promotes English to top-level tabs/meta; stashes original under    │
│     │   `sourceLanguage = {code, name, isRTL, tabs, meta}`                 │
│     ├── OWNS the Redis response-cache write for non-English videos         │
│     │   (assembly skips Redis when ctx.language != "en")                    │
│     └── English Qdrant embeddings for cross-language RAG search            │
│                                                                             │
│  9. SAVE + STREAM COMPLETE                                                 │
│     ├── SSE: tab_ready events (progressive; each carries `position` —     │
│     │   moment_track tabs held back for frame fill and streamed last)     │
│     ├── Store to MongoDB (meta + tabs + language + isRTL + sourceLanguage) │
│     ├── Store to Redis — English videos: written in this phase;            │
│     │   non-English: written in the translation phase above                │
│     └── SSE: complete + done + [DONE]                                      │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Content Tag → Domain Schema

| ContentTag | Schema File | Example Tabs |
|------------|-------------|-------------|
| `learning` | `schemas/learning.txt` | key_points, concepts, takeaways, timestamps |
| `tech` | `schemas/tech.txt` | overview, setup, code, patterns, cheat_sheet |
| `food` | `schemas/food.txt` | overview, ingredients, steps, tips |
| `fitness` | `schemas/fitness.txt` | overview, exercises, timer, tips |
| `travel` | `schemas/travel.txt` | overview, itinerary, packing, budget |
| `review` | `schemas/review.txt` | overview, verdict, pros_cons, specs |
| `music` | `schemas/music.txt` | overview, analysis, structure, lyrics |
| `project` | `schemas/project.txt` | overview, materials, tools, steps |
| `language` | `schemas/language.txt` | phrases, rules, drills, vocabulary |
| `science` | `schemas/science.txt` | concepts, key_facts, experiments |
| `gaming` | `schemas/gaming.txt` | highlights, builds/loadouts, tier_list |
| `news` | `schemas/news.txt` | events, claims_tracker, key_facts |
| `podcast` | `schemas/podcast.txt` | guests, key_quotes, moments |
| `sport` | `schemas/sport.txt` | match events, formation_diagram, stats |

---

## SSE Streaming Pipeline (v2)

The pipeline uses Server-Sent Events (SSE) to stream results progressively with component-addressed tabs.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    STREAMING PHASES (SSE Events)                            │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  PHASE 1: METADATA (~1-3s, no LLM)                                        │
│    Events: metadata, chapters                                               │
│                                                                             │
│  PHASE 2: TRANSCRIPT + FRAMES (parallel, ~10-30s)                          │
│    Events: transcript_ready, sponsor_segments, frames                       │
│                                                                             │
│  PHASE 2.5: VISUAL CONTEXT INJECTION (~0-1s, no LLM)                      │
│    Injects [VISUAL at M:SS] annotations into transcript                     │
│                                                                             │
│  PHASE 3: CLASSIFIER + PLAN (concurrent, ~2-5s)                            │
│    Events: triage_complete (contentTags, tabs, confidence)                   │
│    → Classifier (fast model) runs concurrently with plan (Sonnet)          │
│    → Frontend shows tab skeleton immediately                                │
│                                                                             │
│  PHASE 4: EXTRACTION (1-5+ LLM calls, ~10-60s)                            │
│    Events: extraction_progress, extraction_complete                          │
│    → Short: single call; Long >30min: chunked by chapters                  │
│                                                                             │
│  PHASE 5: SYNTHESIS (1 LLM call, ~5-10s)                                  │
│    Events: synthesis_complete                                                │
│                                                                             │
│  PHASE 6: ENRICHMENT (0-1 LLM call, ~5-10s)                               │
│    Events: enrichment_complete (if applicable)                               │
│                                                                             │
│  PHASE 7: ASSEMBLY (code, 0 LLM calls; may run moment frame fill I/O)      │
│    Events: meta, tab_ready[] (progressive, each with `position`)             │
│    → Each tab_ready event renders one tab immediately, slotted by position  │
│    → moment_track tabs held back for exact-timestamp frame fill             │
│      (heartbeats keep the stream alive), then stream last                   │
│                                                                             │
│  PHASE 7.5: TRANSLATION (non-English only, ~5-15s)                         │
│    → Translates tabs + synthesis to English for bilingual storage           │
│    → Whisper translate for English Qdrant embeddings                        │
│    → Stores language/isRTL metadata on document                             │
│    → Owns the terminal transition: assembly left the doc "processing"        │
│      and DEFERRED `done`; translation writes status="completed" and emits    │
│      `done` after persisting the sourceLanguage block                        │
│                                                                             │
│  PHASE 8: SAVE + DONE                                                      │
│    Events: complete (tabCount, processingTimeMs), done, [DONE]              │
│    → English videos: assembly emits `done` directly (status="completed")     │
│    → Non-English videos: `done` is emitted by Phase 7.5 (see above)          │
│    → MongoDB: meta + tabs + language + isRTL saved                          │
│    → Redis: full response cached for instant re-serve                      │
│    → `heartbeat` events keep the SSE stream alive during long silent phases  │
│      (e.g. multi-minute Whisper); cadence SSE_HEARTBEAT_SECONDS (12s)        │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### LLM Calls Summary

For a typical video (< 30 min):

| Stage | Model | Calls | Timeout |
|-------|-------|-------|---------|
| Classifier | Fast (Haiku/mini/flash-lite) | 1 | 10s |
| Plan | Sonnet | 1 | 30s |
| Extraction | Sonnet | 1-2 | 240s |
| Synthesis | Fast (Haiku/mini/flash-lite) | 1 | 30s |
| Enrichment | Fast (Haiku/mini/flash-lite) | 0-1 | 90s |
| Assembly | None (code only) | 0 | instant, unless moment frame fill runs (60s + 150s budgets) |

**Total: 4-6 LLM calls, ~20-50 seconds, ~$0.09/video**

Classifier and Plan run concurrently. Plan produces `PlanResult` (identity, tabs, contentTags, extractionGuidance) which flows to all downstream phases for creator-aware, context-rich output.

For long videos (>30 min), extraction uses chunked batching (2-5 additional calls).

---

## Observability Layer

Every pipeline run is wrapped in a [Langfuse](https://langfuse.com) trace named `pipeline:{videoSummaryId}` so each LLM call surfaces as a generation span with input/output/cost/latency. The layer is **optional**: every helper no-ops when `LANGFUSE_PUBLIC_KEY`/`SECRET_KEY` are unset.

```
┌─────────────────────────────────────────────────────────────────┐
│  Pipeline run                                                    │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ Langfuse trace (pipeline:{videoSummaryId})              │   │
│  │   ├── generation: classifier                             │   │
│  │   ├── generation: plan                                   │   │
│  │   ├── generation: extraction (× chunks)                  │   │
│  │   ├── generation: synthesis                              │   │
│  │   ├── generation: enrichment                             │   │
│  │   ├── generation: translation_* (conditional)            │   │
│  │   └── score: faithfulness (sampled, non-blocking)        │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

- **Trace boundary**: opened in `routes/stream.py::stream_summarization` via `pipeline_trace(...)`, closed automatically on exit. The trace handle lives in an asyncio `ContextVar` so concurrent runs don't collide.
- **Generation spans**: emitted by `LLMProvider.complete_with_messages` and `complete_fast` whenever a caller passes `span_name`. `call_llm_with_retry` threads its `stage_name` through automatically, so every stage gets per-attempt visibility for free.
- **Prompts**: synced via `scripts/register_prompts.py`. Call sites can opt into registry-first loading with `load_prompt_with_fallback`.
- **Faithfulness judge**: `services/pipeline/faithfulness.py` runs as a fire-and-forget task after extraction. Sampling rate is `LANGFUSE_FAITHFULNESS_SAMPLE_RATE` (default 0.2).
- **Assistant**: separate trace per chat session (`chat:{videoId}`); tool calls attach as named spans.

See [OBSERVABILITY.md](./OBSERVABILITY.md) for the full guide.

---

## Network Topology

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        vie-network (Docker bridge)                           │
│                                                                              │
│  ┌───────────┐  ┌───────────┐  ┌──────────────┐  ┌─────────────┐           │
│  │ vie-web   │  │ vie-api   │  │vie-summarizer│  │vie-assistant│           │
│  │  :5173    │  │  :3000    │  │   :8000      │  │   :8001     │           │
│  └─────┬─────┘  └─────┬─────┘  └──────┬───────┘  └──────┬──────┘           │
│        │              │               │                  │                  │
│        └──────────────┼───────────────┼──────────────────┘                  │
│                       │               │                                     │
│                       ▼               │                                     │
│                ┌─────────────┐        │                                     │
│                │ vie-mongodb │◄───────┤                                     │
│                │   :27017    │        │                                     │
│                └─────────────┘        │                                     │
│                                       │                                     │
│                ┌─────────────┐        │                                     │
│                │  vie-redis  │◄───────┤                                     │
│                │   :6379     │        │                                     │
│                └─────────────┘        │                                     │
│                                       │                                     │
│                ┌─────────────┐        │                                     │
│                │ vie-qdrant  │◄───────┘                                     │
│                │   :6333     │                                               │
│                └─────────────┘                                               │
│                                                                              │
│  ┌───────────┐                                                              │
│  │ vie-admin │                                                              │
│  │  :8002    │                                                              │
│  └───────────┘                                                              │
└─────────────────────────────────────────────────────────────────────────────┘

Exposed ports:
  - 5173  → Frontend
  - 3000  → API
  - 8002  → Admin
```
