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
│  Pipeline: metadata → [transcript → probe → plan ∥ memory]         │
│           ∥ frames ∥ field-group extraction → assembly             │
│           → moment fill ∥ synthesis ∥ quiz → save → [translation]  │
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

Content type and tab layout come from the **plan** (primary model, Sonnet 5.5), which reads the full `[m:ss]`-marked transcript once the **tier probe** (`LLM_CLASSIFIER_MODEL`) has guessed domain and format; the **memory** call (`LLM_MEMORY_MODEL`) reads the same transcript in parallel. Extraction is cut into field groups that fire as soon as the plan (text groups) and the frames (visual group) are done. Phase order lives in `routes/pipeline_orchestration.py`; the call order with file anchors is in [summarizer-workflow.md](./summarizer-workflow.md).

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           PIPELINE FLOW                                     │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  1. METADATA (one yt-dlp extract_info + validate_duration)                  │
│     └── Starts the t=0 group in the background: caption fetch,              │
│         description analysis (gpt-6-luna), low-res + one ≤720p download     │
│         (none on a frame-manifest cache hit)                                │
│                                                                             │
│  2. TEXT BRANCH ∥ FRAMES ∥ FIELD GROUPS (one parallel group)                │
│     ├── Text: transcript (S3 → yt-dlp → transcript-api → Whisper →          │
│     │   Gemini → metadata, music only) → tier probe (domain, format,        │
│     │   has_visual_demo, transcript_usable; unusable captions → audio       │
│     │   pass) → plan (Sonnet 5.5, full [m:ss] transcript) ∥ memory          │
│     │   (Haiku 5.5: outline, evidence, tldr → the early hero)               │
│     ├── 14 primary tags + 2 modifiers (narrative, finance); domain          │
│     │   playbooks ("<domain>:<format>") injected into the plan prompt;      │
│     │   forbidden components stripped after it                              │
│     ├── Plan-done: reconcile (memory's second opinion, required             │
│     │   backfill, quiz policy, 3–6 tabs) → triage_complete + meta →         │
│     │   <video_memory> → provisional Moments tab                            │
│     ├── Frames: 360p detect (or its 360p twin if that lands first) + score  │
│     │   → tier (waits ≤ 3 s for the probe) → select → 720p refine if the    │
│     │   file is on disk, else a deferred pass after vision → S3             │
│     │   (manifest cache) → OCR ∥ vision → frames-done:                      │
│     │   <visual_annotations> + the code tabs reconcile deferred             │
│     └── Field groups (EXTRACTION_PARALLEL=true): the tabs' fields →         │
│         ≤ 5 text groups + ≤ 1 visual group (per chunk on long videos);      │
│         text groups fire at plan-done (text1 streamed first, warming        │
│         the cached prefix), the visual group at frames-done; each           │
│         landed group → extraction_progress + non-final tab previews         │
│                                                                             │
│  3. ASSEMBLY (code, 0 LLM calls) → LATE GROUP                               │
│     ├── Synthesis first only when memory left tldr/takeaways empty          │
│     ├── 29 assemblers in ASSEMBLER_REGISTRY → TabEntry[] (component props)  │
│     ├── Domain forbidden/max policy + degrade-never-drop demote ladder      │
│     ├── Frame thumbnail injection (+ ±15s backfill)                         │
│     ├── Late group, parallel: moment fill (exact-timestamp frames) ∥        │
│     │   synthesis (masterSummary + SEO, fast tier) ∥ the quiz (gpt-4o-mini; │
│     │   only for a plan with a quiz host)                                   │
│     └── Cross-tab links; final re-emit: every tab final once, the           │
│         overview last                                                       │
│                                                                             │
│  4. SAVE + COMPLETE                                                         │
│     ├── SSE complete (tabIds = the final order) → MongoDB (meta + tabs      │
│     │   + language + isRTL + pipeline.*)                                    │
│     ├── Redis response cache (English; awaited, best-effort); Qdrant        │
│     │   points + S3 raw transcript in the background (no Redis or           │
│     │   Qdrant for an eval-user run; S3 unless it came from S3)             │
│     └── English videos: SSE done + [DONE]                                   │
│                                                                             │
│  5. TRANSLATION (conditional — non-English only)                            │
│     ├── translate_to_source(): flat-list batches (≤ 2,500 chars,            │
│     │   ≤ 100 strings, ≤ 8 in flight) on LLM_TRANSLATION_MODEL              │
│     │   (gpt-6-luna), mirror detection, deep copy into sourceLanguage       │
│     ├── English stays the top-level tabs/meta; the original-language        │
│     │   artifact goes under `sourceLanguage = {code, name, isRTL, tabs,     │
│     │   meta}`                                                              │
│     ├── OWNS the Redis response-cache write and the terminal `done`         │
│     │   for non-English videos (assembly leaves the doc "processing")       │
│     └── no Qdrant work: the RAG transcript's English starts at              │
│         the save, in the background (docs/RAG.md)                           │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

`EXTRACTION_PARALLEL=false` (env only) drops the field groups: after the whole
phase-2 group the frames-done step runs, then ONE extraction call (single /
overflow, or a long video's chapter batches one at a time) — the pre-split
request, byte for byte, for the same plan. The lever is extraction-only: the
plan-done / frames-done reconcile, the `<video_memory>` evidence, the toolkit
markers and `PIPELINE_VERSION` stay; a full rollback is a redeploy of the
previous `main` (`config.py`).

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
│  PHASE 1: METADATA (no LLM call of its own)                                 │
│    Events: metadata                                                         │
│                                                                             │
│  PHASE 2: TEXT BRANCH ∥ FRAMES ∥ FIELD GROUPS (one parallel group)          │
│    Events: phase (transcript-source hops), transcript_ready,                │
│      synthesis_complete (memory-done hero: tldr + keyTakeaways),            │
│      triage_complete + meta (plan-done, after reconcile),                   │
│      tab_ready {provisional: true} (the Moments tab), frames,               │
│      meta again when frames-done changed the tab list,                      │
│      per landed group: extraction_progress {section: "groups"} +            │
│      tab_ready previews (final: false, no links / attachments),             │
│      extraction_complete (the merged groups)                                │
│    → Frontend shows the tab skeleton at meta, the Moments preview after it  │
│                                                                             │
│  PHASE 3: ASSEMBLY → LATE GROUP                                             │
│    Events: description_analysis, tab_ready previews of what the client      │
│      lacks (the overview, tabs assembly added or the last groups            │
│      changed), the moment tabs once framed, synthesis_complete              │
│      (superset), enrichment_complete (the quiz), then the final             │
│      re-emit: tab_ready {final: true} for every tab not yet final,          │
│      the overview last                                                      │
│                                                                             │
│  PHASE 4: SAVE + DONE                                                       │
│    Events: complete (tabCount, tabIds, processingTimeMs, degraded),         │
│      done, [DONE]                                                           │
│    → English videos: assembly emits `done` directly (status="completed")    │
│    → Non-English videos: `done` comes after Phase 5 (see below)             │
│    → MongoDB: meta + tabs + language + isRTL + pipeline.* saved             │
│    → Redis: full response cached for instant re-serve (English here)        │
│    → `heartbeat` events keep the SSE stream alive during long silent phases │
│      (e.g. multi-minute Whisper); cadence SSE_HEARTBEAT_SECONDS (12s)       │
│                                                                             │
│  PHASE 5: TRANSLATION (non-English only)                                    │
│    Events: phase: translation, then the runner's done + [DONE]              │
│    → Translates the English tabs + meta into the source language            │
│      (`sourceLanguage` block) for the FE language toggle                    │
│    → no Qdrant work: the RAG transcript's English is a background           │
│      task started at the save (docs/RAG.md §Non-English)                    │
│    → Owns the terminal transition: assembly left the doc "processing"       │
│      and DEFERRED `done`; translation writes status="completed" and         │
│      emits `done` after persisting the sourceLanguage block                 │
│                                                                             │
│  EXTRACTION_PARALLEL=false: no field groups — after Phase 2, frames-done    │
│    then ONE extraction call (chapter batches one at a time on long          │
│    videos; extraction_progress per batch); assembly then streams every      │
│    tab but the moment tabs at once                                          │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### LLM Calls Summary

Per run, default path (`EXTRACTION_PARALLEL=true`). The model column names the routing setting and its default (`config.py` = both compose anchors; picked by the 4.0d model A/B rounds 1 + 2, `dev/active/pipeline-1min/gates/g4-model-ab.md`). Under compose a blank or missing `.env` line keeps the default (`${LLM_X:-<default>}`); to change a stage, set a model (e.g. `openai/gpt-4o-mini`). Only `LLM_SYNTHESIS_MODEL` defaults blank, which means the fast tier. The fast tier is Haiku 4.5 by default (code and compose) and gpt-4o-mini with `LLM_FAST_PROVIDER=openai`, as both `.env` templates and prod set:

| Stage | Model | Calls | Timeout |
|-------|-------|-------|---------|
| Description analysis | `LLM_DESCRIPTION_MODEL` = gpt-6-luna | 1 | 25s per attempt, 30s total |
| Tier probe | `LLM_CLASSIFIER_MODEL` = Haiku 4.5 | 1 (a second one after an unusable-captions audio pass) | 4s, no retry |
| Plan | Primary model = Sonnet 5.5, effort medium | 1 | 60s, 1 retry |
| Memory | `LLM_MEMORY_MODEL` = Haiku 5.5 (blank = `LLM_EXTRACTION_MODEL` in config.py only — under compose a blank line keeps Haiku 5.5) | 0-1 (none without a timed transcript) | 25s, no retry |
| Frame vision | `LLM_VISION_MODEL` = Sonnet 5.5 | 0-5 batches of ≤ 8 frames (none on LOW tier / manifest hit) | 2× the expected wall, within 30s–`FRAME_VISION_TIMEOUT` |
| Chapter detect | Fast tier (`LLM_CHAPTER_DETECT_MODEL` is not passed by compose) | 0-1 (long videos only) | 25s |
| Extraction field groups | `LLM_EXTRACTION_MODEL` = Sonnet 5.5, effort low | 1-5 text + 0-1 visual, per chunk | 120-300s, from the group's estimated output |
| Synthesis | `LLM_SYNTHESIS_MODEL` (blank → fast tier) | 1 | 20s |
| Quiz (enrichment) | `LLM_ENRICHMENT_MODEL` = gpt-4o-mini | 0-1 (a plan with a quiz host) | 14s per attempt, 30s total |
| Faithfulness judge | `LLM_JUDGE_MODEL` = gpt-6-luna (blank = the fast tier) | 0-6 (sampled claims, background) | 20s |
| Translation | `LLM_TRANSLATION_MODEL` = gpt-6-luna | non-English only: output + title + RAG transcript batches | 90s per batch |
| Assembly | None (code only) | 0 | instant, unless moment frame fill runs (60s + 150s budgets) |

Every request is shaped for its model (thinking off + effort, accepted sampling params, no prefill), and a refused answer is asked once of the previous model (`config.PREVIOUS_MODEL`: Sonnet 5.5 → Sonnet 4.6, Haiku 5.5 → Haiku 4.5, gpt-6-luna → gpt-4o-mini) — [ERROR-HANDLING.md](./ERROR-HANDLING.md#llm-error-handling).

**Total** varies per video: description analysis, tier probe, plan, memory, the extraction field groups (1–5 text + ≤ 1 visual, times the chunks of a long video), synthesis and the quiz when planned — plus 0–5 vision batches and ≤ 6 sampled judge calls; a non-English video adds the translation batches. The last measurement on prod is v9 **before** the 4.0 model refresh (`dev/active/pipeline-1min/gates/g3.md` §Prod, 2026-10-13, runs with frames; the 3 benchmarks + the Hebrew regression video): $0.098–0.180 per video (T1dQhQAm8Tc $0.098 — a run that writes the plan's hourly cache entry adds ≈ $0.10 —, jMq8lEu-of0 $0.180, uC45_4nnEAI $0.148 (median of 2), Hebrew regression EqaMrzd9nZU $0.157; the others one run each), 9–25 LLM calls, `complete` at 59–93 s; prod v8 (g0) cost $0.114–0.216 and took 169–240 s on the first three. The Whisper fallback adds roughly $0.14 when captions are unavailable.

The plan starts once the tier probe answered (it waits at most the probe's 4 s budget + 1 s, `phases/probe.PLAN_PROBE_WAIT_SECONDS`); plan and memory then run concurrently. The plan's `PlanResult` (contentTags, tabs with briefs, evidence, terms) is reconciled against the memory at plan-done and rendered with the memory's outline / tldr / takeaways into `<video_memory>` — the `{video_context}` of every writer (extraction, synthesis, quiz).

A video one extraction batch can't cover (over `MAX_MINUTES_PER_BATCH`, 40 min, or `MAX_TOKENS_PER_BATCH`, 50k tokens) is cut into chunks — the chapter chain merged into batches, else a sentence force-split — and every field group fires once per chunk. With `EXTRACTION_PARALLEL=false` extraction is one call after frames-done, chapter batches one at a time for videos over `CHUNKED_EXTRACTION_THRESHOLD` (900 s).

---

## Observability Layer

Every pipeline run is wrapped in a [Langfuse](https://langfuse.com) trace named `pipeline:{videoSummaryId}` so each LLM call surfaces as a generation span with input/output/cost/latency. The layer is **optional**: every helper no-ops when `LANGFUSE_PUBLIC_KEY`/`SECRET_KEY` are unset.

```
┌─────────────────────────────────────────────────────────────────┐
│  Pipeline run                                                    │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ Langfuse trace (pipeline:{videoSummaryId})              │   │
│  │   ├── generation: description_analysis, tier_probe       │   │
│  │   ├── generation: plan ∥ memory                          │   │
│  │   ├── generation: frame_vision (× batches)               │   │
│  │   ├── generation: extraction_text1…N, extraction_visual  │   │
│  │   ├── generation: synthesis, enrichment (the quiz)       │   │
│  │   ├── generation: translation_* (conditional)            │   │
│  │   ├── span: phase:<name> (one per timed phase)           │   │
│  │   └── score: faithfulness (sampled, non-blocking)        │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

- **Trace boundary**: opened in `routes/pipeline_runner.py::stream_summarization` via `pipeline_trace(...)` before the Redis cache lookup (cache hits are traced too), closed automatically on exit. The trace handle lives in an asyncio `ContextVar` so concurrent runs don't collide.
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
