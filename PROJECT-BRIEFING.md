# Video Insight Engine (VIE) — Full Project Briefing

> **Purpose of this file:** A single, self-contained context document you can paste into a fresh LLM chat so it understands the product, the architecture, the workflows, the data model, and the conventions — well enough to discuss design and architecture immediately, without reading the codebase.
>
> **How to use:** Paste this whole file as your first message, then add a line like: *"This is my project. I want to discuss/change X. Ask me anything you need."* Everything below is drawn from the repo's own docs (`README.md`, `CLAUDE.md`, `docs/*.md`) as of **2026-05-29** (branch `dev-1-ux`).

---

## 0. One-paragraph elevator pitch

**Video Insight Engine turns any YouTube URL into a custom interactive web app — not a summary, not a transcript, an *app built from the video's content*.** You paste a URL; an AI pipeline analyzes the video's transcript, frames, and structure, classifies what kind of content it is, and generates a tailored set of **interactive tabs** chosen for that content. A cooking video becomes a checkable ingredient list + timed step player; a coding tutorial becomes a syntax-highlighted code explorer + tool checklist; a travel vlog becomes a spot explorer with a visual timeline. The same pipeline produces completely different output per video because **classification drives generation**. It's a Docker-Compose monorepo of 4 app services + backing stores, with aggressive Redis/MongoDB/S3 caching so the **same video re-serves instantly to any user at $0.00**, plus Qdrant for RAG-powered chat with the video.

**The problem it solves:** ~90% of video knowledge evaporates within a week. Videos can't be searched, highlighted, or quickly reviewed. VIE makes video knowledge durable and interactive.

---

## 1. Product concept & output

The unit of output is a **VIEResponse**: video-level `meta` + an array of **tabs**. Each tab names one **interactive component** plus the **props** that drive it. The frontend just does `COMPONENT_REGISTRY[tab.component]` → render. Tabs stream in progressively as the pipeline assembles them (you watch them appear).

**29 registered components** the AI can choose from (snake_case keys, identical set in the frontend `COMPONENT_REGISTRY` and the Python `ASSEMBLER_REGISTRY`):
`moment_track, step_player, spot_explorer, flash_deck, checklist, info_grid, overview, comparison, comparison_radar, budget, code_playground, quiz_arena, packing_mission, workout_room, lyrics_karaoke, video_filmstrip, claims_tracker, tier_list, formation_diagram, concept_canvas, step_flow_canvas, connect_canvas, display_section` + 6 secondary/attachment renderers (`stat_banner, tip_callout, summary_header, diagram_card, frame_strip, quick_quiz`).
(Legacy v1 keys — quiz, code_explorer, gallery, lyrics_player, scenario, exercise_tracker, verdict, timeline, clip_player — were retired in interactive-overhaul-v2; cached rows with those keys fall through to `display_section`.)

**Example — a cooking video might get:**

| Tab | Component | What it does |
|-----|-----------|--------------|
| 🛒 Ingredients | checklist | Checkable shopping list with quantities |
| 👨‍🍳 Steps | step_player | Timed steps with "watch this step" video seek |
| 🔪 Tips | flash_deck | Swipeable chef tips |
| ⏰ Moments | moment_track | Value gallery of key moments — card click opens a frame Lightbox; an explicit Jump button seeks |
| 🎞️ Filmstrip | video_filmstrip | Enriched frame scrubber (suppressed when Moments already carries ≥8 frame-backed items) |

**Example — a tech tutorial might get:** code_playground (copyable snippets), step_player (setup), checklist (tools/deps), flash_deck (concepts), quiz_arena (knowledge check — educational domains only).

**Everything is clickable into the video.** A `VideoPlayerContext.seekTo` wires timestamps (steps, filmstrip frames, moment Jump buttons) to an **inline** YouTube player hosted in the video hero (the old collapsible player was deleted); the player scrolls into view on seek. Moment cards deliberately open a Lightbox instead of seeking — jumping is always explicit.

**Who uses it:** students (lecture → concept map, flash cards, quiz), developers (talks → code explorers), home cooks (timed cooking mode), researchers/creators (folders + RAG chat + share links), teams (training videos as interactive knowledge bases).

---

## 2. System architecture

A Docker-Compose monorepo. 4 application services + an admin dashboard + 4 backing stores + a queue worker, on a private Docker bridge network.

| Service | Tech | Port | Role |
|---------|------|------|------|
| **vie-web** | React 19 + Vite + TS | 5173 | Frontend SPA, consumes SSE/WS |
| **vie-api** | Node.js 20 + Fastify + TS + Zod | 3000 | Gateway: auth, REST, MongoDB CRUD, orchestration, SSE proxy |
| **vie-summarizer** | Python 3.11+ + FastAPI + LiteLLM | 8000 | The AI pipeline (URL → assembled tabs) |
| **vie-assistant** | Python 3.12+ + FastAPI + LiteLLM + Qdrant | 8001 | RAG chat + "Explain" + tools/actions |
| **vie-admin** | Python FastAPI + React + Recharts | 8002 | Ops dashboard (cost, health, alerts) |
| **vie-summarizer-worker** | (shares summarizer image) | — | Consumes the RabbitMQ job queue |
| vie-mongodb | MongoDB 7 | 27017 | Records, structured results, caches |
| vie-redis | Redis 7 | 6379 | Response cache + dispatch guard + pipeline lock |
| vie-qdrant | Qdrant | 6333/6334 | RAG vector store |
| vie-rabbitmq | RabbitMQ 3.13 (mgmt) | 5672 / 15672 | Optional job queue |

**Only `5173` (web), `3000` (API), and `8002` (admin) are externally exposed.** The summarizer and assistant have **no exposed ports** and live on the internal network — only the API gateway talks to them.

**Service diagram:**

```
┌──────────────────────── vie-web (React 19) ───────────────────────┐
│  COMPONENT_REGISTRY[tab.component] → render · VideoPlayerContext   │
│  SSE stream consumer · WebSocket status                            │
└───────────────────────────────┬───────────────────────────────────┘
                                 │ SSE / WS / REST
                                 ↓
┌──────────────────────── vie-api (Fastify) ────────────────────────┐
│  Auth · Routes · MongoDB CRUD · idempotency/dedup · orchestration  │
└───────────┬───────────────────────────────────┬───────────────────┘
            │ HTTP / AMQP                         │ HTTP + SSE
            ↓                                     ↓
┌──── vie-summarizer (Python) ────┐     ┌──── vie-assistant (Python) ───┐
│  metadata → [transcript → probe │     │  RAG: embed query → Qdrant    │
│  → plan ∥ memory] ∥ frames      │     │  retrieve → LLM → SSE stream  │
│  ∥ field-group extraction       │     │  tools: note/quiz/navigate/.. │
│  → assembly → late group        │     └───────────────┬───────────────┘
│  → [translation]                │                     │
│  LiteLLM · yt-dlp · FFmpeg ·    │                     │
│  OpenCV · Tesseract · spaCy ·S3 │                     │
└─────┬───────────────────┬───────┘                     │
      ↓                   ↓                              ↓
  MongoDB              Redis + S3                     Qdrant
```

**Monorepo layout** (note: README shows API under `services/api`, but the **real gateway is at top-level `api/`** — trust `api/`):

```
video-insight-engine/
├── apps/web/                  # React 19 frontend
├── api/                       # vie-api gateway (Fastify) — at ROOT, not services/
├── services/
│   ├── summarizer/            # Python AI pipeline
│   └── assistant/             # Python RAG chat (formerly "explainer")
├── packages/
│   ├── shared/                # @vie/shared — owns domains.json (single source of truth)
│   ├── types/                 # @vie/types — shared TS types
│   └── llm-common/            # shared Python LLM cost-tracking package
├── docs/                      # the canonical docs this briefing summarizes
├── dev/                       # task planning (survives context resets)
├── .claude/                   # skills, agents, hooks, rules, commands
└── docker-compose.yml
```

**Shared config, one source of truth:** `packages/shared/src/config/domains.json` defines, per domain: `label`, `emoji`, `gradient`, `defaultTabs`, plus top-level `components` (planner-selectable set), `componentTiers`, `densityGates`, `domainRequirements`, `playbooks`, `visualCriticality`, the `dataSources` registry (caps, `outputWeight`, `requiresEvidence`, `waitsForVisual`), `quizEnrichment` and `grouping`. **Python** reads it via `domain_config.py`; **TypeScript** via `@vie/shared/config`. One config, two runtimes. The plan prompt is generated from it — `plan.txt` `{valid_components}` + `component_toolkit.txt` `{density_gates}` come from config (no hardcoded prompt lists), so editing `domains.json` changes what the planner LLM can pick.

**Layered service code (a strong convention across all services):** Routes (HTTP) → Services (business logic) → Repositories (data). Dependencies flow downward only; services throw domain errors and never know HTTP; repositories map docs ↔ domain entities. Node uses constructor injection + a DI `Container`; Python uses FastAPI `Depends()`.

---

## 3. The core pipeline (vie-summarizer)

The pipeline is **plan-driven** (`PIPELINE_VERSION` v11: pipeline-1min phases 1–3, the 4.0 model refresh and output-quality). Its LLM calls: description analysis, tier probe, plan ∥ memory, the extraction **field groups** (1–5 text + ≤ 1 visual, per chunk of a long video), **merge_rank** on a chunked run (> 40 min; one `LLM_MEMORY_MODEL` call at text-groups-done that dedupes and ranks the lists across chunks and tabs; `MERGE_RANK_ENABLED`), the **hero rewrite** (replaces the synthesis call, which stays as its fallback; `HERO_REWRITE_ENABLED`), the quiz when planned (quiz v2; `QUIZ_V2_ENABLED`), 0–5 vision batches and a sampled faithfulness judge (the extraction sample + one hero and one quiz call after assembly) (+ translation batches for non-English videos). Phase order lives in `routes/pipeline_orchestration.py`; the call-order walkthrough is `docs/summarizer-workflow.md`.

```
metadata (one extract_info) → t=0 group in the background: captions · description analysis · downloads
   → ONE parallel group:
       text branch:  transcript → tier probe → plan ∥ memory → plan-done reconcile → <video_memory>
                     → provisional Moments tab                                        (plan-done)
       frames:       scenes → tier (probe) → 720p refine → OCR ∥ vision
                     → frames-done: <visual_annotations> + deferred code tabs         (frames-done)
       field groups: text groups fire at plan-done (text1 warms the cache), the visual group at
                     frames-done; each landed group streams non-final tab previews
   → faithfulness judge (background)
   → assembly (pure code) → late group: moment fill ∥ hero rewrite (else synthesis) ∥ quiz
                     (720p still downloading: the fill runs beside the group, joined after complete)
   → final re-emit (every tab final, overview last) → complete → save → Redis / Qdrant / S3
                     (a waiting moment fill: the Moments tabs' final, framed, follows complete, before save)
   → [ translation ]  (non-English only) → done
```

`EXTRACTION_PARALLEL=false` is the rollback lever: no field groups — after the whole group, frames-done, then ONE extraction call (or a long video's chapter batches, one at a time). It reverts extraction only; reconcile, `<video_memory>` and `PIPELINE_VERSION` stay.

> ⚠️ **Naming note:** "Plan" is canonical; "triage" is the legacy name — the SSE event is still `triage_complete`, the plan phase lives in `phases/triage.py`, and `triage.py` keeps the `TriageResult` carrier. A failed plan or confidence < 0.6 → the category's default tabs (`_build_fallback_plan`). The old classifier (replaced by the tier probe) and "persona detection" are gone.

### Stage-by-stage

1. **Metadata** — one `yt-dlp` `extract_info` (title, channel, duration, chapters, description, thumbnails, category, the picked caption track) + `validate_duration`. Then, in the background, the **t=0 group**: the caption fetch, the description analysis (links/resources/social links/timestamps; an LLM call, `LLM_DESCRIPTION_MODEL` = gpt-6-luna) and the low-res + one ≤720p download (none on a frame-manifest cache hit).

2. **Transcript** (text branch) — multi-source fallback chain, first success wins:
   `S3 cache` → `yt-dlp subtitles` → `youtube-transcript-api` (tenacity retry, 3 attempts, exp backoff 4–30s) → `OpenAI Whisper` (~$0.16/26min, ~5–15min) → `Gemini Flash audio` (~$0.04/26min, ~30–90s) → `metadata fallback` (**music videos only**; non-music with no transcript raises `NO_TRANSCRIPT`).
   Caption track: the spoken language first (the one original ASR track's language, else yt-dlp's `language` field — the ASR overrides a wrong uploader tag unless the video is dubbed; legacy `iw` = Hebrew), then manual over auto; the spoken-language pick never takes a machine translation, the fallback still may, as its last resort; nothing in that language → the older language waterfall. An S3 blob holding another caption track than this run's pick is stale and replaced.
   Basic cleaning (English fillers; the spaCy + TF-IDF pass only with `TRANSCRIPT_CLEANING_ENABLED`) and **SponsorBlock** filtering. Frame annotations never enter the transcript.

3. **Tier probe** (`tier_probe.py`, `LLM_CLASSIFIER_MODEL` = Haiku 4.5, temperature 0, 4 s, no retry) — reads metadata + three short windows of the native transcript → domain (1 of 14), format (1 of 18, incl. `unboxing`), `has_visual_demo`, `transcript_usable`. Frames read it for the visual tier; the plan for its playbook (`"<domain>:<format>"`) and a hint line. Captions it judges not a coherent transcript of speech re-run the audio layers alone (Whisper → Gemini) once, then the probe again; never for music.

4. **Plan ∥ memory** — both read the full `[m:ss]`-marked transcript (rendered once).
   - **Plan** (`plan.py`, primary model = Sonnet 5.5 at effort medium, 60 s, 1 retry, 3,500 tokens, JSON mode; the static half of the prompt cached 1 h): `contentTags`, modifiers, the **tab layout** (component, dataSource, goal, brief `{what, where, expect}`), **evidence** (`has_steps`, `has_ingredients`, `has_code`, `is_learnable`, …) and ≤ 12 terms. Domain **playbooks** inject required/preferred/forbidden components; `_enforce_domain_policy()` strips forbidden ones (e.g. `quiz_arena` outside learning/language/tech/science).
   - **Memory** (`memory.py`, `LLM_MEMORY_MODEL` = Haiku 5.5, 25 s, one attempt): outline (4–12 sections), evidence, tldr, takeaways — the tldr + takeaways stream at once as the first `synthesis_complete` (**the hero**).

5. **Plan-done reconcile** (`reconcile.py`, `reconcile_pass.py`) — the memory's second opinion: an evidence key is false only when both readers said false; a tab whose required evidence is doubly false is re-pointed, kept (moment_track), demoted or dropped; code tabs wait for frames-done (code on screen decides); required components backfilled; quiz policy; dedupe; 3–6 tabs. Every decision → `pipeline.reconcile`. Then `triage_complete` + `meta`, the `<video_memory>` block (every writer's `{video_context}`) and the **provisional Moments tab** (memory outline → chapters → description timestamps).

6. **Frames** (parallel with the text branch) — **two-pass with a manifest cache.** Cache check first: `videos/{id}/{SCENE_S3_PREFIX}/manifest.json` (default `scenes-v4`; timestamps, `hiresCount`, persisted vision descriptions; `hiresCount == 0` counts as a MISS). On miss, pass 1 reads the run's **360p video-only** download (or the 720p download's file when it lands first as the same ≤360p rendition; both video downloads try `YTDLP_HIRES_PLAYER_CLIENTS` first — `android` alone serves only the muxed 360p file, 2-4× the bytes — and retry with `YTDLP_PLAYER_CLIENTS`, default `android`; never mix in `default`) → **FFmpeg** scene detect (~200 candidates; zero-candidate ladder 0.3 → 0.15 → uniform) → score locally with **OpenCV** on **6 signals** → select ~25 → adaptive **visual tier** from the probe (HIGH over-selects + vision-reselects to drop presenter frames; LOW skips vision; STANDARD = top-8). Pass 2 (`SCENE_HIRES_ENABLED`): the winners re-extracted at **720p** by seeking the run's one local ≤720p file (no stream-URL seeks) — only when that file is already on disk; otherwise a deferred pass after vision overwrites the same S3 objects, so frames-done never waits for the big file. Then S3 upload + manifest → **Tesseract OCR** ∥ **vision** (`LLM_VISION_MODEL` = Sonnet 5.5; ≤ 8 frames per call, ≤ 5 in flight). **Frames-done**: the `<visual_annotations>` block (`[m:ss] caption | on-screen text`) + the deferred code tabs.

7. **Field-group extraction** (`phases/split_extraction.py`, `EXTRACTION_PARALLEL=true`) — requests only the fields the reconciled tabs read (+ siblings, side reads, scalars), cut into ≤ 5 text groups (split by `outputWeight × expect` over 1,500) + one visual group (`waitsForVisual` fields: tech code, gaming highlights). Each call = three cached-prompt blocks (1h rules + schemas + example, 5m video + transcript + memory, uncached group job). Per chunk text1 streams and its first token releases the other text groups (they read the cache); the visual group fires at frames-done. Each landed group → `extraction_progress` + **preview** `tab_ready`s (non-final); a lost answer gets one more call, then the group fails (its tabs drop/demote, the run is `degraded`). One deterministic merge → `extraction_complete`. Coverage and quality are logged metrics — no retry.

8. **Assembly** — **code, no LLM calls.** 29 assemblers in `ASSEMBLER_REGISTRY` transform extraction → component-addressed `TabEntry[]`. Domain forbidden/max policy, a **degrade-never-drop demote ladder** (`promotion.py`, stamped `degradedFrom`), per-tab **drop-reason accounting**, frame thumbnails (nearest-timestamp + ±15s backfill), moments beyond the duration dropped, cross-tab links from `LINK_RULES`, a minimum of 3 tabs, an always-present Moments tab when a source has entries. Synthesis runs first only when memory left the hero empty.

9. **Late group** (parallel, heartbeats) — **moment fill** (a frame at each frameless moment's timestamp from the run's ≤720p file; the moment tabs stream framed, replacing the provisional tab), the **hero rewrite** (`hero_rewrite.txt`, `LLM_EXTRACTION_MODEL`: one call reading the field groups' cached blocks, started when the text groups landed; thesis, a 3–5 sentence brief, 3/5/7 takeaways stamped by code, worth-it-for / skip-if lines — it replaces **synthesis**, the fast-tier masterSummary + SEO call that runs only when the flag is off or the hero call gives none), the **quiz** (quiz v2 — `quiz_v2.txt` on `LLM_EXTRACTION_MODEL`, reading the field groups' cached blocks, every question's evidence quote checked against the transcript; `enrich_quiz.txt` on gpt-4o-mini with `QUIZ_V2_ENABLED=false`; only on a lecture / tutorial (`quizPolicy.formats`) with a quiz host in a quiz domain; on the field-group path started as soon as its groups landed). Then the **final re-emit** — every tab `final: true` at its final position, the overview last — `complete`, the save, Redis (English, awaited), Qdrant + the S3 raw transcript (background), `done`.

10. **Translation (non-English only)** — `translate_to_source()` collects every translatable prose string into **one flat list**, translates it on `LLM_TRANSLATION_MODEL` (gpt-6-luna) in ≤ 2,500-char batches run in parallel (`translate_flat.txt`), each with mirror-detection (an echoed batch is retried as halves, then kept English), and applies results back by path. Generation is English-canonical: English stays the top-level `tabs`/`meta`, the original-language artifact goes under `sourceLanguage = {code, name, isRTL, tabs, meta}`. **Translation — not assembly — owns the Redis cache write and `done` for non-English videos** (assembly skips Redis when `source_language_code` is set). It does no Qdrant work: the English text the transcript points embed comes from a background task started at the save — the transcript translated as text on `LLM_TRANSLATION_MODEL` (`summarize:rag_translation`), Whisper translate only when the run transcribed the audio ([docs/RAG.md](./docs/RAG.md#non-english-transcripts)). Non-blocking: any failure returns input unchanged.

---

## 4. Domain model

**14 primary content tags:** `learning, tech, fitness, food, music, travel, review, project, language, science, gaming, news, podcast, sport`.
**2 modifier tags:** `narrative, finance`. → **16 domain schemas** total (`prompts/schemas/*.txt`).

**Evidence keys** (answered by the plan and the memory; drive conditional domain requirements, reconcile, the quiz gate and `<video_memory>`):
`has_steps, has_ingredients, has_materials, has_code, has_drills, has_comparison, has_ranking, has_lineup, has_claims, has_lyrics, has_itinerary, has_packing, is_learnable` (`plan.txt` `<evidence_rules>`). The tier probe adds `has_visual_demo` (visual tier) and `transcript_usable`.

**Tags → example tabs:**
- learning: key_points, concepts, takeaways, timestamps (+ the quiz when planned)
- tech: overview, setup, code, patterns, cheat_sheet
- food: overview, ingredients, steps, tips
- fitness: overview, exercises, timer, tips
- travel: overview, itinerary, packing, budget
- review: pros_cons (Pros & cons), comparison (vs competitors, one table per competitor), specs / features — the verdict sits in the hero, never a tab (4.3)
- music: overview, analysis, structure, lyrics, credits
- project: overview, materials, tools, steps, safety
- language: phrases, rules, drills, vocabulary
- science: concepts, key_facts, experiments
- gaming: highlights, builds/loadouts, tier_list (unboxing format: pulled items ranked by rarity/value)
- news: events, claims_tracker, key_facts
- podcast: guests, key_quotes, moments
- sport: match events, formation_diagram, stats
- narrative (modifier): key_moments, quotes, takeaways
- finance (modifier): costs[] + savingTips[] only (primary domain owns budget)

**To add a new domain:** update `domains.json` (incl. its `dataSources` entries; a quiz domain also goes in `quizEnrichment` with a flavor line) + add `prompts/schemas/{tag}.txt` (+ `prompts/examples/{tag}.txt` if it has an example). The quiz is the only enrichment (`enrich_quiz.txt`).

---

## 5. Long-video support (chunked extraction)

```
Field groups (default):
≤ 40 min and ≤ 50k tokens:  ONE chunk → its groups (1–5 text + ≤ 1 visual)
longer / larger:            chapters → batches (≤ MAX_TOKENS_PER_BATCH 50k, ≤ MAX_MINUTES_PER_BATCH 40)
                            → one chunk per batch → chunks × groups in parallel → chunk-major merge
EXTRACTION_PARALLEL=false:  > CHUNKED_EXTRACTION_THRESHOLD (900 s) with chapters → batches one at a time
```

- **Chapter chain:** YouTube creator chapters → description timestamps → (only when one batch can't cover the video) the memory outline, else AI chapter detection (`chapter_detect.txt`, fast tier) → ~5-minute time splits → single chunk. When the chain gives ≤ 1 batch the transcript is force-split by sentences.
- **Concurrency:** every group call of every chunk shares the run's `EXTRACTION_PARALLEL_BATCHES` semaphore (6 per run); each chunk's text1 warms that chunk's cached prefix for its other groups.
- **Merge:** chunk-major then group order; lists concatenate with later duplicates dropped, first non-empty scalar wins, ordered items renumbered (the false path keeps `extraction_merger.py`: identity dedup, richest scalar).
- **Per-chunk context:** each chunk's prompt carries a `<batch_partial_context>` block telling the model it sees only a slice and to **return empty arrays for absent fields / not pad to meet density quotas**, plus only the visual annotations and key frames of its own time window.
- Synthesis has no hierarchical mode any more: it reads `<video_memory>` (built from the full transcript) + the compact final extraction, whatever the length.

---

## 6. Caching, dedup & idempotency (the system's defining concern: never pay for a video twice)

### Four caching layers (all share ONE Redis instance + Mongo + S3)
- **L0 — Redis dispatch guard:** `vie:api:dispatched:<videoSummaryId>` (SET NX EX, fail-open). Prevents duplicate publishes across API replicas.
- **L1 — Redis response cache:** `vie:response:<youtube_id>` holds the full VIEResponse JSON (allowlisted top-level keys only). Same video = instant $0.00. (Written by the **translation** phase for non-English videos.)
- **L2 — MongoDB `videoSummaryCache`:** persistent, keyed by `youtubeId`.
- **L3 — S3 frames/transcripts:** keyed by `youtube_id`; if frames exist, frame extraction is skipped (~$0.01 even on cache expiry).

### Three dedup/idempotency layers
1. **Per-user idempotency hash** = `SHA-256(userId : youtubeId : PIPELINE_VERSION : providersHash [: client:headerValue])`. Reserved atomically as a `pending` placeholder on a unique index in `idempotencyKeys`. Race winner runs the pipeline; losers get `{duplicate:true}` (200) or `409 IDEMPOTENCY_IN_FLIGHT`. A client `Idempotency-Key` header is **mixed into** the hash (not a replacement — stricter than Stripe). Gate runs **before** cost reservation, so a duplicate is a pure Mongo read with zero spend.
2. **Cross-user content-addressed dedupKey** = `SHA-256(youtubeId : PIPELINE_VERSION : providersHash : v<version>)` — **deliberately NO userId**, so two users on the same video collapse onto one cache row via `upsertCacheByDedupKey` (atomic `findOneAndUpdate` + `$setOnInsert`). Exactly one caller sees `wasInsert:true` and owns dispatch.
3. **Redis dispatch guard** (L0 above) catches the narrow API-replica race. Fail-open; the summarizer's per-video **pipeline lock** is the final backstop.

### PIPELINE_VERSION
Single-sourced from `packages/shared/src/config/pipeline-version.json` (**currently `v11`**, the output-quality close-out bump), baked into **both** the idempotency hash and the dedupKey. **Bump it on any change that alters output for the same input** (prompt rewrites, schema changes, model swaps, assembly/cross-tab-link changes). Do **not** bump for logging/perf/frontend-only/non-output fixes. Bumping atomically invalidates all stale keys in both layers; schema changes additionally require clearing the DB cache. **`DISPATCH_GUARD_TTL_SECONDS` (900) must exceed the summarizer's `PIPELINE_LOCK_TTL_SECONDS` (600).** Caching is **versioned, not time-based**.

---

## 7. The API gateway (vie-api)

Node.js 20 + Fastify + TypeScript + Zod. Base URL `/api`. Patterns: DI `Container`, App Builder `buildApp({logger, container})` (test overrides), Repository layer (`VideoRepository`, `ShareRepository`, `UserRepository`, `UserDeletionRepository`).

### Auth (two-token JWT)
- **Access token:** 15-min JWT (`expiresIn:900`), in-memory, claims `{userId, email}`, signed with `JWT_SECRET`.
- **Refresh token:** 7-day, HttpOnly + Secure + SameSite=Strict cookie scoped to `Path=/api/auth/refresh`, claims `{userId}`, signed with `JWT_REFRESH_SECRET` (**must differ from `JWT_SECRET`**).
- Endpoints: `POST /auth/register` (201), `POST /auth/login` (200), `POST /auth/refresh` (200; 401 `REFRESH_EXPIRED`), `POST /auth/logout`, `GET /auth/me`.
- On 401 → client silently POSTs `/auth/refresh` (cookie auto-sent) → new access token → retry.
- Every protected route verifies **resource ownership at the data layer** (e.g. `userHasAccessToSummary`), not just route auth.

### Key endpoints
- `POST /videos` — the core submit. Validates optional `Idempotency-Key`, extracts the 11-char `youtubeId`, counts the hourly burst limit, runs the idempotency gate, quotes the video against the account's minutes and holds them (the minutes ledger), publishes the job. Returns `{cached:true}` on cache hit (hold given back) or `status:pending {cached:false}` on miss. Refusals: `402 INSUFFICIENT_MINUTES`, `422 VIDEO_TOO_LONG_FOR_PLAN`, `403 ACCOUNT_BLOCKED`, `429 TOO_MANY_RUNS` — a cached or in-flight video is still served free. `?bypassCache=true` skips the idempotency gate.
- `GET /videos/:videoSummaryId/stream` — **SSE** stream of the pipeline (see events below). Ends with literal `data: [DONE]`.
- `POST /videos/:id/retry` — manual retry (only when `status==failed`, error is retryable, within 24h).
- `PATCH /videos/:id/override-category` — re-classify through the Plan stage.
- `DELETE /videos/:id` — removes only the **userVideo reference**; the shared cache row is untouched.
- Share: `POST /share/:videoSummaryId` (auth, 20/hr) → `{shareSlug, shareUrl:/s/<slug>}`; `GET /share/:slug` (public, 100/min); `POST /share/:slug/like` (10/min/IP); SSR `GET /s/:slug` (HTML + OG, 60/min) + `GET /s/:slug/og-image.png`.
- Assistant proxy: `POST /api/assistant/chat`, `POST /api/videos/:id/action`, `POST /library/search`.
- `GET /api/users/me/usage` — the user's minutes (plan, free video, plan minutes, credit lots, holds; never dollars).
- Internal: `POST /internal/status` (summarizer → API → rebroadcast over WS; a terminal status settles or releases the run's hold), `POST /internal/run-deletions` (manual GDPR hard-delete sweep; vie-api's billing repair job runs the same sweep every 5 minutes).

### SSE event sequence (since v9, `EXTRACTION_PARALLEL=true`)
```
metadata → phase* (transcript hops: transcript_cached | transcript | audio_transcription
                   | whisper_transcription | metadata_fallback; later: translation)
  → transcript_ready → synthesis_complete (memory's hero: tldr + keyTakeaways)
  → triage_complete + meta (plan-done, after reconcile) → tab_ready {provisional} (Moments)
  → frames (any time after metadata) → [meta again when frames-done changed the tabs]
  → per landed extraction group: extraction_progress {section: "groups"}
      + tab_ready previews (final: false, no links / attachments)
  → extraction_complete → description_analysis → tab_ready previews (overview, added tabs)
  → late group: moment tabs (framed; replace the provisional) ∥ synthesis_complete
      (superset) ∥ enrichment_complete (quiz)
  → final re-emit: every tab tab_ready {final: true} at its final position, overview last
  → complete (tabCount, tabIds, processingTimeMs, degraded) → done (confetti) → [DONE]
```
Clients replace tabs by id and sort by `position`; `heartbeat` events keep the stream alive.
`chapters`, `sponsor_segments` and the v1 events (`detection_result`, `chapter_ready`,
`concepts_complete`) are no longer emitted. Full rules: `docs/API-REFERENCE.md#tab_ready`.

### WebSocket
`ws://localhost:3000/ws?token=<jwt>` (auth via query param; invalid → close 4001). Events: `video.status`, `expansion.status`, `chat.message`. Fed by the summarizer's `POST /internal/status` callback.

### Plans & minutes
Users pay in **video-minutes, never dollars** (billing-roles). Every account (`billingAccounts`) is on plan `free` or `pro` and spends, in order, one free video per rolling 24 h (≤ 30 min), the Pro plan's 600 minutes per monthly cycle (no rollover), then credit lots (admin-granted or bought later, FIFO by expiry). Every submit path — `POST /videos`, each playlist item, the assistant's generate route — is quoted and held before the run (`LedgerEnforcement`; videos up to 180 min with paid minutes), a completed run is debited, a failed one released, a cached or attached video costs nothing; the append-only `ledgerEntries` are the source of truth and a repair job settles lost holds, expires lots and applies recurring grants. There is **no per-user dollar cap and no daily video limit** (removed in P3.2 with `userCosts`' reservation gate, `TIER_LIMITS` and `users.tier` as a gate); abuse is bounded by the shared hourly burst limit (`SUBMIT_BURST_LIMIT_PER_HOUR`, default 20) and per-account admin caps (`blocked`, `maxConcurrent`). A run paid with plan or credit minutes is queued ahead of a free one. LLM dollars are internal margin only (`costUsd` on the run's ledger entries); global spend only trips the vie-admin `daily_global_cost` operator alarm (`ALERT_DAILY_COST_THRESHOLD_USD`). Summaries never expire on any plan (the 30-day free-tier TTL was removed 2026-09-17).

---

## 8. Data model (MongoDB)

**System cache (shared):** `videoSummaryCache`, `systemExpansionCache`.
**User data:** `users`, `folders`, `userVideos`.
**Billing (minutes ledger):** `billingAccounts`, `ledgerEntries` (append-only, the source of truth), `creditLots`, `recurringGrants`; `billingShadow` (P1 diagnostics, read-only).
**Cost & control:** `userCosts`, `userCostAdjustments` (read-only history of the retired dollar gate), `idempotencyKeys`, `llm_usage` (+ `llm_alerts`, `health_history` for admin), `userDeletions`, `agentNotes`.

### `videoSummaryCache` (one row per YouTube video — central, dual-shaped)
- **Core:** `_id`, `youtubeId` (unique), `url`, `title`, `channel`, `duration`, `thumbnailUrl`, `language` (always `'en'` post-translation), `isRTL`, `context{category, contentTags[], primaryTag, youtubeCategory, tags[], displayTags[]}`, `status` (`pending|processing|completed|failed`), `errorMessage`, `errorCode`, `retryCount`, `transcriptMeta{outcome, source, type, origin, captionTrack, captionLang, captionFetchError, captionApiSkipped, attempted[], segments, chars, fetchWallMs, errorCode}` (per-run transcript provenance, written for ok **and** failed runs, absent on Redis-served rows; `transcript`/`transcriptSegments[]` are legacy and unwritten).
- **v1 (legacy):** `summary{tldr, keyTakeaways[], chapters[...], concepts[...]}`, `triage{contentTags[], modifiers[], primaryTag, tabs[{id,label,emoji,dataSource}], confidence}`, `output` (domain-keyed extraction), `enrichment`, `synthesis{tldr, keyTakeaways[], masterSummary, seoDescription}`.
- **v2 (current, used by frontend):**
  - `assembledMeta{videoId, videoTitle, creator, contentTags[], modifiers[], primaryTag, userGoal, tldr, keyTakeaways[], masterSummary, seoDescription, language, isRTL}`
  - `assembledTabs[{id, label, emoji, component, props, crossTabLinks?[{targetTab,label}], attachments?, degradedFrom?}]` — `component` maps to the frontend `COMPONENT_REGISTRY`.
- **Multi-language:** `sourceLanguage{code, name, isRTL, tabs, meta}` — **OMITTED entirely (not null)** for English-source and sound-only videos. (Legacy `tabs_en/meta_en/synthesis_en/forceEnglishReason` were **removed** in `dev-1-ux`; use the nested block.)
- **Share/dedup:** `shareSlug` (nanoid-10, unique-sparse), `viewsCount`, `likesCount`, `likedIps[]` (hashed), `dedupKey` (unique partial index `{$exists:true}`), `version`, `processingTimeMs`, `tokenUsage{input,output,cost}`, `rawTranscriptRef` (S3 key), `generation{model,promptVersion,generatedAt}`.
- **Indexes:** `{youtubeId:1,isLatest:1}`, `{youtubeId:1,version:-1}`, `{status:1}`, `{shareSlug:1}` unique sparse, `{dedupKey:1}` unique partial.

> **Frontend resolution order:** `doc.meta` → `doc.assembledMeta + synthesis` → `doc.triage + synthesis`. Both v1 and v2 are written side-by-side for backward compat.

### Other key collections
- `users`: `email` (unique), `passwordHash` (bcrypt), `tier`, `usage{...}`, soft-delete fields `deletedAt` / `hardDeleteAt`.
- `folders`: materialized-path hierarchy (`path`, `parentId`, `level`).
- `userVideos`: per-user library referencing the cache (denormalized `youtubeId/title/...`, `folderId`, `isFavorite`, optional `playlistInfo`). Index `{userId,videoSummaryId}` unique.
- `userCosts`: per-user daily aggregate (`date` UTC, `totalCostUsd`, `videoCount`, `creditAdjustmentUsd`). Read-only history since P3.2 — nothing writes it and it gates nothing.
- `userCostAdjustments`: signed admin audit (`amountUsd<0` = credit).
- `idempotencyKeys`: `hash` (unique), `status`, `videoSummaryId` (sparse), 24h TTL.
- `systemExpansionCache`: cached "Explain" expansions, keyed `{videoSummaryId, targetType, targetId}` unique.

---

## 9. Frontend (vie-web)

React 19 + Vite 7 + TypeScript 5 SPA. **React Compiler** auto-memoizes (so manual `useMemo`/`useCallback`/`memo` are intentionally rare — only add when react-scan proves the compiler missed it).

### State split
- **React Query 5** — remote/server state (videos, folders, playlists, user). `queryKeys` factory in `lib/query-keys.ts`; mutations invalidate lists `onSuccess`.
- **Zustand 5** — persisted client state (auth under localStorage `vie-auth`, theme). Use **atomic selectors** (`useAuthStore(s => s.user)`), never destructure the whole store.
- **Forms:** controlled inputs + explicit validation (React Hook Form is **not installed** — removed in the 2026-07 dead-deps cleanup; Zod guards data boundaries like `tab-prop-schemas.ts` and `sse-validators.ts`).
- **useState** — ephemeral UI. **URL params** — shareable state.

### Structure
Two tiers: shared `components/` (ui, vie, layout, collections, rag, videos) and `features/` (`sidebar` 30+ files, `video-output` 40+ files). The **VIE component library** (`components/vie/`, 7 domain dirs) is Layer 2: primitive props only (never `@vie/types`), all `React.memo`, imported via the `@/components/vie` barrel.

### The output rendering system (the heart of the frontend)
`OutputRouter` inspects the response and picks a path:
- **v2 path:** `assembledTabs` present → `ComposableOutput` → `COMPONENT_REGISTRY[tab.component]` → renderer + `tab.props`. One lookup, one render.
- **v1 fallback:** `ComposableOutputV1` rebuilds a VIEResponse from `triage + extraction` via `resolveTabData()` + `renderInteractive()`.

Four-layer component model: (1) shadcn/ui primitives, (2) the domain-free VIE library, (3) self-contained interactive mini-apps with their own state, (4) the shell coordinating cross-tab state via `TabCoordinationContext` (active tab persisted per `videoId` in sessionStorage, `CrossTabLink` navigation).

### Routing
React Router 7: `/` (Landing, public), `/login`, `/register`, `/board` (folder explorer + video grid, protected), `/generate` (URL intake, protected), `/video/:id` (output, protected), `/s/:slug` (public share).

### Streaming consumption & resilience
- SSE at `/api/videos/:id/stream`: `triage_complete` → pending-tab strip + domain accent; `tab_ready[]` → progressive render **sorted by `position`**, replaced by id (the provisional Moments tab at plan-done, non-final previews per extraction group, moment tabs after their frame fill, then every tab once `final: true` — a Moments tab whose fill waits for the 720p download gets its final after `complete`); `complete` → the final tab order; `done` → celebration. UI phases derive from milestone events via `SSE_PHASE_MAP` (`StreamPhase`: idle | connecting | metadata | transcript | extraction | building | translation | done | cancelled | error, plus `phaseDetail`). On load, `resolve-display-tabs` prefers a completed DB doc over a partial stream.
- `useProcessingManager` (init in `App.tsx`) watches the video list; for any `pending`/`processing` video it reconnects an SSE stream into a Zustand `processing-store` keyed by `videoSummaryId` → **auto-resume after refresh**, shared between sidebar spinner and detail page.
- `use-websocket` delivers `video.status`/`video.metadata` → invalidates React Query lists, keeps sidebar titles synced.

### Design system
- **Tailwind v4, CSS-first** — no `tailwind.config.ts`; configured via `@theme inline {}` in `src/index.css`. ⚠️ `rtl:` utilities silently no-op unless `@custom-variant rtl` is registered in `index.css`. v4 scale shifted (`shadow-sm`→`shadow-xs`, `ring`→`ring-3`, etc.).
- **OKLCH colors** — `oklch(L% C H)`, perceptually uniform. Always use **semantic tokens** (`bg-primary`, `text-muted-foreground`) / `var(--token)`, never hardcoded hex. 8 accent tokens: `--vie-coral, --vie-plum, --vie-sky, --vie-mint, --vie-honey, --vie-rose, --vie-forest, --vie-peach`. Light mode ~hue 250 (cool), dark ~hue 55 (warm).
- **Theme** — `data-theme` on `<html>` (dark/light/system), FOUC-prevention inline script reading localStorage `vie-theme`, View Transitions API crossfade.
- **Typography (No-Inter Rule)** — Inter/DM Sans/Plus Jakarta are **banned** in `apps/web/src/`. Display = Bricolage Grotesque, Body = Hanken Grotesk, Mono = JetBrains Mono. Use semantic `.type-*` utilities.
- **shadcn/ui** (new-york) — `data-slot` attributes, `asChild` pattern, CVA variants, `cn()` (clsx + tailwind-merge). Icons from lucide-react (`h-4 w-4`, `shrink-0` in flex, `aria-label` on icon-only). Raw `<button>` is ESLint-flagged — use `<Button>`.
- Design spec (`DESIGN.md`): "Two-Violet Rule", "Glass-Is-Rare", depth-aware output, Frame-as-Hero rule.

### i18n / RTL
- `DirectionProvider` wraps output; derives `isRTL` from `meta.isRTL` or detected language (RTL codes: `he, ar, fa, ur, ps, sd, yi, ku, dv`). `useDirection()` → `{language, isRTL, dir}`; `useLabels()` → translated UI strings (currently EN/HE/AR).
- `LanguageToggle` (WAI-ARIA radiogroup) renders **only** when `video.sourceLanguage` exists AND `sourceLanguage.tabs.length > 0`; preference in localStorage `vie:prefersOriginalLang`.
- ⚠️ **RTL streaming gate (critical):** the toggle's `useOriginal` flag **must** be gated on `!isProcessing`. Streamed content is always English-shape during the pipeline; `sourceLanguage` is only populated after the final translation phase. Without the gate, a Hebrew user with `showOriginal` persisted gets full-page misalignment while LTR English streams in.

---

## 10. Assistant & RAG (vie-assistant)

Python 3.12+ FastAPI on port 8001. Lets a user **chat with a specific video**, grounded in its content. Reached via the gateway (`POST /api/assistant/chat`), authenticated service-to-service with `X-Internal-Secret`. (This service replaced the former "explainer".)

### RAG mechanics
- **One Qdrant collection:** `transcript_chunks`, **384-dim, cosine**. Two content kinds distinguished by a `source` payload field: `transcript` and `default_output`.
- **Embedding model:** `all-MiniLM-L6-v2` (sentence-transformers, 384 dims, **English-only**). `EMBEDDING_MODEL_NAME` **must match** between summarizer (writer) and assistant (reader). Swap candidate: `BAAI/bge-small-en-v1.5` (same dims, +5–8% English retrieval) — any dim change forces collection recreation + full re-ingest.
- **Chat pipeline (`POST /chat`):** detect language → translate non-EN query to EN → encode → search top-k (MatchValue for 1 video, MatchAny for many) → dedup near-identical (cosine 0.95) → build system prompt (video meta + up to `MAX_CONTEXT_CHUNKS=8` chunks + history, `MAX_CONVERSATION_TURNS=20`) → **stream tokens over SSE**.
- **SSE:** `source` event (RAGSources) → `text` token events → `done` (metadata). Plus `error`, `tool_result`.

### Output chunking (summarizer side)
The `output_chunker.py` walks assembled tabs via a **per-component handler registry** (`_COMPONENT_HANDLERS`). **Unknown component → 0 chunks** (deliberate: don't index button labels/IDs). Drops chunks < 6 words; excludes raw code/lyrics/URLs/numbers; flattens only a prose whitelist. Each output point carries `tab_id`, `tab_component`, `prop_path` (e.g. `questions[2]`) for **deep-linking** a hit back to the exact tab/prop. Indexing runs as **fire-and-forget** background tasks after `complete` (pre-delete by `(video_id, source)` then upsert) — so `done` fires before vectors are stored (retrieval can briefly lag).
> Adding a component requires updating BOTH the assembler (`ASSEMBLER_REGISTRY`) and the chunker (`_COMPONENT_HANDLERS`), or its content is never retrievable.

### Other endpoints
- **Intent routing (keyword, first match wins):** `save note/bookmark` → `note_taker`; `quiz me/test me` → `quiz_generator` (the fast tier: `settings.llm_fast_model` — gpt-4o-mini with `LLM_FAST_PROVIDER=openai`, else Haiku 4.5); `what is/define/explain` → `concept_explain` (the assistant's primary, Sonnet 4.6, + RAG); `find/navigate/show me where` → `navigator` (local fuzzy); `compare/cross-reference` → `cross_reference` (Sonnet 4.6). No match → default RAG chat.
- **`POST /action`** — structured dispatcher: `{video_id, action, params}` where action ∈ `save_note, quiz_me, find_moment, explain`. Returns `{success, action, data, error, trace_id}` (12-char hex). Rate-limited 30/min/caller.
- **`POST /library/search`** — pure retrieval, no LLM. 1–200 `video_ids`, `query` (1–500 chars), `top_k` (1–50, default 10), optional `sources`. **Trusts the gateway** to verify ownership of every `video_id` before forwarding. Rate-limited 60/min keyed on `X-User-Id`. Returns ranked results with deep-link metadata.
- Errors: `AppError` hierarchy → 400/404/500/502/503 with machine-readable codes; stack traces logged, never returned.
> `QdrantClient.search` is **synchronous** — mock with `MagicMock`, not `AsyncMock`. (Newer client code prefers `query_points()` over the removed `search()`.)

---

## 11. Cross-cutting: queue, observability, errors

### RabbitMQ job queue (behind `USE_QUEUE_PIPELINE=true`; flag-gated)
- `POST /api/videos` publishes a **Zod-validated** job (Pydantic-validated by the consumer) to exchange `vie.pipeline` (direct, durable), routing key `video.process`, into queue `vie.pipeline.jobs` (x-max-priority 10, 1h message TTL, DLX `vie.pipeline.dlx` → DLQ `vie.pipeline.dlq`).
- **Priority by what pays (P3.4):** the payload's `plan` is `paid` (priority 5) when the run's minutes hold takes plan or credit minutes, `free` (1) for the free video — not the account's plan.
- **Worker retry:** republish with `attempt+1` up to `WORKER_MAX_RETRIES=3`, then `nack(requeue=False)` → dead-letters. One Sentry event per dead-lettered job (retries fire none).
- **SSE preserved without frontend changes:** the worker acquires the **same Redis pipeline lock** the SSE producer uses; a client opening `/stream` sees the lock held and attaches as a consumer of the Redis Streams event log.
- Admin queue endpoints (require `X-Admin-Key`; the replay also needs a signed `X-Admin-Actor` naming an admin): `GET /api/admin/queue/stats`, `GET /api/admin/queue/dlq`, `POST /api/admin/queue/replay`.
- Default path (flag off) is a **direct HTTP `POST /summarize`** → `202 Accepted` → FastAPI BackgroundTasks.

### Observability (Langfuse) — strictly best-effort, no-ops when keys unset
- One pipeline run = one trace `pipeline:{videoSummaryId}` (tagged `youtubeId`, `videoSummaryId`, `requestId`). Each LLM call is a child **generation** with usage + metadata (`attempt`, `useFastModel`, `modelOverride`, `latencyMs`, `finishReason`). **Assembly emits no generations** (pure code). Generations: `description_analysis`, `tier_probe`, `plan`, `memory`, `frame_vision`, `extraction_text1..N` / `extraction_visual` (`_c<k>` per later chunk), `synthesis`, `enrichment` (the quiz), `faithfulness`, `translation_*`, `rag_translation`; plus one `phase:<name>` span per timed phase.
- Assistant traces chat as `chat:{videoId}` with tool spans + `library_generation` / `library_agent` generations.
- **Prompt registry:** local `.txt` files under `services/summarizer/src/prompts/**` are the source of truth; `vie-langfuse-init` (run-once service) syncs them to Langfuse under `label='production'`. `load_prompt_text` is registry-first with file fallback, recording the version in the trace.
- **Faithfulness judge:** after extraction, fire-and-forget, samples `LANGFUSE_FAITHFULNESS_SAMPLE_RATE=0.2` of items (capped 6/video), asks the judge model (`LLM_JUDGE_MODEL` = gpt-6-luna, reasoning off; blank = the fast tier) if each claim is transcript-supported, logs a `faithfulness` score (≈ $0.002–0.003/video). **Informational only — never blocks**; runs < 0.7 logged at warning.
- **Golden dataset eval:** 20-video dataset + `run_eval.py --fail-under 0.7` for CI-gradeable scoring (must use `--api-url http://vie-api:3000` from inside the container).
- Aggregate cost lives **locally in MongoDB** (`llm_usage` via the shared `packages/llm-common` callback), not in Langfuse.

### Request-id propagation
Every API request gets a UUID v4 on `x-request-id` (regex-validated `^[A-Za-z0-9_-]{8,128}$`; forged → fresh UUID). It flows: API→frontend (echoed), API→RabbitMQ (`requestId` field), API→summarizer (`X-Request-ID`), API→assistant (`X-Request-ID` on `/chat` + `/action`), bound to structlog contextvars in worker/assistant. Search logs/Langfuse/Sentry by it, or run `./scripts/find-request.sh <id>`.

### Sentry
`@sentry/node` (API) + `sentry-sdk[fastapi]` (Python). Env: `SENTRY_DSN` (empty=no-op), `SENTRY_ENVIRONMENT`, `SENTRY_RELEASE`, `SENTRY_TRACES_SAMPLE_RATE=0.1`. API captures only effective status **≥500** (resolved across Fastify `.statusCode`, `AppError.status`, ZodError→400).

### Error handling model
Canonical `{error, message, details, statusCode}` envelope, one shared code set.
- **Validation/edge:** `INVALID_URL`, `NO_TRANSCRIPT` (422), `VIDEO_TOO_LONG` (> `MAX_VIDEO_DURATION_MINUTES`: config default 600, the `.env` templates set 30; checked by the summarizer's metadata phase), `VIDEO_TOO_SHORT` (<60s), `VIDEO_UNAVAILABLE`, `VIDEO_RESTRICTED`, `LIVE_STREAM`.
- **Server:** `LLM_ERROR`, `DATABASE_ERROR`, `SUMMARIZER_ERROR`.
- **Automatic retry:** max 3, backoff `[5,15,60]s`, retryable = `LLM_ERROR, DATABASE_ERROR, NETWORK_ERROR, RATE_LIMITED`.
- **Manual retry** (`POST /api/videos/:id/retry`): only if `failed` + retryable + within 24h. Non-retryable: `INVALID_URL, VIDEO_TOO_LONG, VIDEO_TOO_SHORT, NO_TRANSCRIPT, VIDEO_RESTRICTED, LIVE_STREAM`.
- Each service **translates errors at its boundary** (e.g. assistant `NOT_FOUND` → HTTP 404 `VIDEO_NOT_FOUND`).

---

## 12. LLM providers, model tiering & cost

**LiteLLM** multi-provider with automatic fallback. Per-stage routing via `LLM_<STAGE>_MODEL` overrides.

The summarizer's `MODEL_MAP` (`services/summarizer/src/config.py`) — the provider's default (primary) and fast-tier models. vie-assistant keeps its own (`services/assistant/src/config.py`: anthropic default Sonnet 4.6, fast Haiku 4.5; chat = `LLM_CHAT_MODEL`, else the primary provider's fast model):

| Provider | Default (primary) | Fast tier | Context (default / fast) |
|----------|-------------------|------------------------|---------|
| Anthropic | Claude Sonnet 5.5 (`anthropic/claude-sonnet-5-5`) | Claude Haiku 4.5 (`anthropic/claude-haiku-4-5-20251001`) | 1M / 200K |
| OpenAI | GPT-4o | GPT-4o-mini | 128K |
| Google | Gemini 2.5 Flash | Gemini 2.5 Flash Lite | 1M |

**Routing by stage (defaults from the 4.0d model A/B rounds 1 + 2, pipeline-1min gate g4-model-ab — quality within noise first, then latency, then cost):** Plan = the primary, Sonnet 5.5 at effort medium (the only stage that reads it) · Frame vision = `LLM_VISION_MODEL` = Sonnet 5.5 (Haiku 5.5 labelled cooking frames as talking heads; gpt-4o-mini was reverted in 2026-05 — it hallucinated OCR on dense frames) · Extraction groups = `LLM_EXTRACTION_MODEL` = Sonnet 5.5 at effort low, the group calls' rules ending with the completeness rule (A/B round 2: the fewest missed transcript-supported items and no invented details — Haiku 4.5 invented steps, quantities and code snippets) · Memory = `LLM_MEMORY_MODEL` = Haiku 5.5 (blank follows `LLM_EXTRACTION_MODEL` in config.py only — under compose a blank line keeps Haiku 5.5) · Tier probe = `LLM_CLASSIFIER_MODEL` = Haiku 4.5 · Description analysis + translation (output, title, RAG transcript) = `LLM_DESCRIPTION_MODEL` / `LLM_TRANSLATION_MODEL` = gpt-6-luna, reasoning off · Hero rewrite = `LLM_EXTRACTION_MODEL` (Sonnet 5.5, on the extraction's prompt cache) · Merge rank = `LLM_MEMORY_MODEL` (Haiku 5.5; chunked runs) · Quiz = `LLM_EXTRACTION_MODEL` (Sonnet 5.5, on the extraction's prompt cache; quiz v2) — `LLM_ENRICHMENT_MODEL` = gpt-4o-mini only with `QUIZ_V2_ENABLED=false` · Faithfulness judge = `LLM_JUDGE_MODEL` = gpt-6-luna (pipeline-1min close-out; informational, the golden baseline re-scored under it) · Synthesis (fallback), chapter detect = the fast tier (gpt-4o-mini with `LLM_FAST_PROVIDER=openai`, as both `.env` templates and prod set; Haiku 4.5 by default). config.py and both compose anchors carry the same literal defaults. `LLM_FALLBACK_PROVIDER` = a cross-provider fallback for primary-model calls after one same-provider retry (`openai` → gpt-4o).

**Per-model request rules + refusals (4.0b):** every request is shaped for the model it goes to (`llm_model_rules`: thinking off + effort for the 5.5 models, `reasoning_effort="none"` for gpt-6-luna, sampling params dropped where the model rejects them, no assistant prefill). A refused answer (HTTP 200) is asked once more, in the same call, of the model it replaced (`config.PREVIOUS_MODEL`: Sonnet 5.5 → Sonnet 4.6, Haiku 5.5 → Haiku 4.5, gpt-6-luna → gpt-4o-mini) — never again on the refused model, never retried; tagged in `pipeline.timing` (v4) and `llm_usage` (`refusal`, `fallback_from`).

**Per-stage timeouts:** Tier probe 4s · Plan 60s · Memory 25s · Extraction groups 120–300s (from the group's estimate; the single call 240s / dynamic overflow) · Hero rewrite 25s · Merge rank 12s per attempt, 25s total · Synthesis 20s · Quiz v2 30s per attempt, 40s total (v1: 14s per attempt, 30s total) · Assembly: no LLM calls (moment frame fill budgets 60s + 150s when it runs).

**Prompt caching (Anthropic):** `cache_control` on user blocks — the plan's static half (ttl 1h); the extraction field groups' shared blocks (1h rules + schemas + example, 5m video + transcript + memory; text1 warms them, the other groups read them); the single call's `[video + transcript + memory]` block (5m). Cached reads bill ~10% of input ($0.20 vs $2.00/MTok for Sonnet 5.5, the plan's model). ⚠️ Cache breaks (zero hits) if the static half isn't byte-identical between calls (a timestamp/user_goal slipping in invalidates it). `cost_usd` is **net of** the cache discount; list-price ≈ `cost_usd + cache_savings_usd`. Audit with `scripts/audit_cache_credits.py` (exit 2 = Anthropic calls but zero cache hits → investigate) — but on LiteLLM 1.105 the ledger's `provider` reads `unknown`, so it ends "No Anthropic calls in window" (exit 1); use `scripts/cache_hit_rate.py` (reads `pipeline.timing`) meanwhile.

**Cost per video:**

| Scenario | LLM calls | Cost |
|----------|-----------|------|
| First processing (v9 on prod before the 4.0 model refresh; the 3 benchmarks + the Hebrew regression video, with frames) | 9–25 | $0.098–0.180 |
| Same video, any user (cache hit) | 0 | **$0.00** |
| Cache expired, S3 frames reused | 0 | ~$0.01 |

Measured on prod v9 on 2026-10-13, before the 4.0 model refresh (pipeline-1min gate g3 §Prod, runs with frames; `pipeline.timing` = the ledger): T1dQhQAm8Tc $0.098 (a run that writes the plan's hourly cache entry adds ≈ $0.10), jMq8lEu-of0 $0.180, uC45_4nnEAI $0.148 (median of 2 runs; the others one each), the Hebrew regression video EqaMrzd9nZU $0.157; `complete` at 59–93 s. Prod v8 on the first three: $0.114–0.216 and 169–240 s. The call count includes the sampled faithfulness judge (up to 6 small calls) and, for a non-English video, the translation batches. The Whisper fallback adds roughly $0.14 when captions are unavailable.

**Per-user spend:** no dollar cap — users pay video-minutes through the ledger (§7 Plans & minutes). Each run's LLM $ (Σ `llm_usage.cost_usd` by its request id) is recorded on its ledger debit/release as `costUsd`, for margin only.

---

## 13. Security

- **Two-token JWT** (see §7). `JWT_SECRET ≠ JWT_REFRESH_SECRET`. A soft-deleted user's JWT stays valid for its 15-min TTL, so the `authenticate` hook does a **per-request user lookup** → `403 ACCOUNT_DELETION_PENDING` to kill sessions instantly.
- **Passwords:** bcrypt cost ≥ 10. **Validation:** Zod everywhere (11-char YouTube IDs across watch/youtu.be/embed; 8+ char passwords with upper+lower+digit; lowercased emails ≤255).
- **Rate limits** keyed by `user.id` (auth) or IP: default 100/min; register 5/h, login 10/15min, refresh 30/15min, `POST /videos` 10/24h, share endpoints as noted. The submit burst limit (`SUBMIT_BURST_LIMIT_PER_HOUR`, default 20/h) is one shared bucket for `POST /videos`, the assistant's generate route and playlist import. Returns `{error:'RATE_LIMITED', retryAfter}` + `X-RateLimit-*` headers.
- **CORS:** localhost:5173 + `FRONTEND_URL`, `credentials:true`, never `*` in prod. **Helmet CSP:** `defaultSrc 'self'`, image hosts `img.youtube.com`/`i.ytimg.com`, `crossOriginEmbedderPolicy` disabled so YouTube thumbnails load.
- **SSRF:** OG-image service validates thumbnail hostnames against a YouTube allowlist. **XSS:** share-page escapes all dynamic values + JSON-LD. **NoSQL injection:** `_ALLOWED_RESULT_KEYS` allowlist in the summarizer's Mongo repo.
- **Payments:** none live. The Paddle Classic stub is removed; `PaymentProviderAdapter` (noop) is the seam for the P4 provider, and its webhook must be signature-verified, fresh and idempotent.
- **Required secrets:** `JWT_SECRET`, `JWT_REFRESH_SECRET`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` (the openai/* stage defaults since 4.0d), `INTERNAL_SECRET` (`openssl rand -base64 32`).

---

## 14. GDPR / privacy (Article 17 cascade deletion)

- **Soft delete:** `DELETE /api/users/me` sets `deletedAt=now`, `hardDeleteAt=now+30d`, returns `202 + scheduledHardDeleteAt`, clears the refresh cookie. User immediately frozen (`403 ACCOUNT_DELETION_PENDING`).
- **30-day grace:** `POST /api/users/me/restore` — the **only** authenticated endpoint exempt from the soft-delete check.
- **Scheduled hard delete:** vie-api's billing repair job runs `runScheduledDeletions()` every 5 minutes (`hardDeleteAt <= now`); `POST /internal/run-deletions` is the manual entrypoint → `executeHardDelete()` saga deletes in fixed order: `agentNotes → userVideos → folders → userCosts → userCostAdjustments → idempotencyKeys → videoSummaryCache dispatch stamp (videoCacheDispatchContext: unsets userId/requestId/billingRunId) → billing (pseudonymised, not deleted — the account, holds and ledger entries move to a tombstone owner id, amounts stay, Q12) → Qdrant user content (placeholder) → S3 user objects (placeholder) → users (FK anchor, last)` → writes a `userDeletions` audit row. Every step is idempotent (`deleteMany({userId})`) and wrapped in `runStep` that records failures as **warnings without aborting** (smallest PII footprint > rollback).
- **Audit:** `userDeletions` retained **indefinitely**, stores `emailHash` (SHA-256, never plaintext), `initiatedBy` (`self|admin|scheduler`), counts, timing, warnings. Proof of erasure = re-hash an email and match a completed row.
- **NOT deleted** (video-scoped / shared / anonymized): `videoSummaryCache` (the shared row stays; only the deleted user's dispatch stamp, `userId` / `requestId`, is unset), Qdrant `transcript_chunks` (`user_id` reserved but always null today), S3 `videos/{youtubeId}`, `llm_usage` (kept indefinitely, no TTL; rows keep the orphaned `user_id`), IP-hashed share signals, external Paddle records.
- **Compliance gate:** `gdpr-cascade.integration.test.ts` — seeds every inventoried collection, runs the cascade, asserts zero rows + audit row. If it fails, deletion no longer satisfies Article 17 → **do not merge**.
- Article 20 (data export) is **not yet implemented** (manual via support, JSON archive within 30 days). Admin escape hatch: `DELETE /api/admin/users/:id?immediate=true`.

---

## 15. Admin service & cost telemetry (vie-admin)

Port 8002, FastAPI + static React/Recharts SPA in one multi-stage container. Auth via shared `ADMIN_API_KEY` Bearer (timing-safe `hmac.compare_digest`). Reads `llm_usage` (no TTL — ledger), `llm_alerts`, `health_history` (30d TTL); polls `/health` on api/summarizer/assistant, Mongo, Qdrant `/readyz`, Redis+RabbitMQ via api `/ready`, and worker liveness via queue consumers.

**`packages/llm-common`** (shared Python) auto-tracks every LiteLLM call via a `MongoDBUsageCallback` (LiteLLM `CustomLogger`) → one `UsageRecord` row (23 fields incl. `cache_creation_tokens`, `cache_read_tokens`, `cache_savings_usd`) per call. Registered in summarizer `main.py` (sync buffer) + assistant `server.py` (async buffer). **Single source of truth for cost** — no manual tracking in the hot loop.

Surfaces: usage analytics (`/usage/stats`, by-feature/model/video, anomalies, duplicates), health, cost alerts (`ALERT_COST_THRESHOLD_USD=0.50`), per-user `grant-credit` (`|amountUsd|≤1000`, reason 1–500 chars; `>0` grants credit stored negative, `<0` is a manual charge). Note: all admins share one key → `adminId` is self-attested (not forgery-resistant; fine for single-admin).

---

## 16. Conventions & gotchas to know before discussing changes

- **`git stash` is BANNED** for clean-tree comparisons in this repo; never run working-tree-mutating git commands without explicit current-turn permission. Conventional commits; never commit to `main` directly.
- **Plan vs Triage:** "Plan" is canonical; "Triage" is legacy naming (the SSE event is still `triage_complete`).
- **API gateway lives at `api/`** (root), not `services/api/`.
- **Adding a TabEntry component** requires changes in **BOTH** `assembly.py` (`ASSEMBLER_REGISTRY`, Python) **and** `ComposableOutput.tsx` (`COMPONENT_REGISTRY`, TS) — plus the chunker handler, `@vie/types`, and API docs for full coverage.
- **`PIPELINE_VERSION`** must be bumped on any output-changing pipeline change; schema changes also need a DB cache clear.
- **Non-English Redis write** is owned by the **translation** phase, not assembly.
- **`sourceLanguage` is omitted, not null** for English/sound-only videos — use presence checks.
- **Tailwind v4 `rtl:` no-ops** without `@custom-variant rtl` in `index.css`. Never hardcode colors — semantic OKLCH tokens only.
- **Embedding model must match** writer (summarizer) and reader (assistant); changing it requires re-ingest (+ collection recreation on a dim change). It's **English-only** — non-EN translated to EN before embedding.
- **`docker-compose.override.yml` is auto-loaded** — keep it in sync during service renames.
- **Observability/Sentry no-op when keys unset** — don't assume traces exist in a given environment.
- **Code style:** no `any` (TS), type hints on every Python signature, no sync-in-async, no empty catches, files < 500 lines, functions < 50 lines. Validate input at boundaries (Zod / Pydantic), trust it internally.
- `field_validator(mode="before")` for backward-compatible Pydantic schema changes (string→list coercion); `!= null` (not `!== undefined`) to catch null.
- **Test counts (local, 2026-07 baselines — grown since):** api ~964, web ~1153 vitest + 55+ Playwright, summarizer ~2281, assistant ~346, admin ~142. Runners (repo uses **pnpm**): `cd api && pnpm test`, `cd apps/web && pnpm test`, summarizer `.venv/bin/python -m pytest`. Never run the three big suites in parallel (memory-server contention → phantom failures).

---

## 17. Quick start

```bash
cp .env.example .env          # add ANTHROPIC_API_KEY and OPENAI_API_KEY (GEMINI optional)
docker-compose up -d
curl http://localhost:3000/health && curl http://localhost:8000/health
open http://localhost:5173    # paste a YouTube URL, watch tabs stream in
```

---

## 18. Where the canonical detail lives (doc map)

| Topic | Doc |
|-------|-----|
| System architecture, pipeline | `docs/ARCHITECTURE.md` |
| API contracts, endpoints | `docs/API-REFERENCE.md`, `docs/SERVICE-API.md` |
| MongoDB schemas, VIEResponse | `docs/DATA-MODELS.md` |
| Summarizer pipeline internals | `docs/SERVICE-SUMMARIZER.md` |
| Assistant + RAG | `docs/SERVICE-ASSISTANT.md`, `docs/RAG.md` |
| Frontend patterns | `docs/FRONTEND.md` |
| Auth, rate limiting, CORS | `docs/SECURITY.md` |
| Idempotency, dedup, dispatch guard | `docs/IDEMPOTENCY.md` |
| Error codes, retry, DLQ | `docs/ERROR-HANDLING.md` |
| Multi-service contracts | `docs/CROSS-CUTTING.md` |
| Infra, RabbitMQ, Redis, workers | `docs/INFRASTRUCTURE.md` |
| Langfuse, prompt registry, request-id, Sentry | `docs/OBSERVABILITY.md` |
| LLM cost & cache crediting | `docs/llm-cost-model.md` |
| GDPR Art.17, soft-delete, audit | `docs/GDPR.md`, `docs/PRIVACY.md` |
| Admin service | `docs/SERVICE-ADMIN.md` |
| Project rules & workflow | `CLAUDE.md` |

---

*End of briefing. This document is a synthesized snapshot; for any contract you intend to change, confirm against the linked canonical doc and the code, since prose and code occasionally drift (notably Plan/Triage naming and the `api/` location).*
