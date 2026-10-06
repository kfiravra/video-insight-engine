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

**Who uses it:** students (lecture → quiz/flashcards/scenarios), developers (talks → code explorers), home cooks (timed cooking mode), researchers/creators (folders + RAG chat + share links), teams (training videos as interactive knowledge bases).

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
│  metadata → [transcript+frames] │     │  RAG: embed query → Qdrant    │
│  → visual injection → plan      │     │  retrieve → LLM → SSE stream  │
│  → extraction → synthesis       │     │  tools: note/quiz/navigate/.. │
│  → enrichment → assembly        │     └───────────────┬───────────────┘
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

**Shared config, one source of truth:** `packages/shared/src/config/domains.json` defines, per domain: `label`, `schemas[]`, `components[]`, `enrichment(bool)`, plus top-level `components` (planner-selectable set), `componentTiers`, and `densityGates`. **Python** reads it via `domain_config.py`; **TypeScript** via `@vie/shared/config`. One config, two runtimes. The plan prompt is generated from it — `plan.txt` `{valid_components}` + `component_toolkit.txt` `{density_gates}` come from config (no hardcoded prompt lists), so editing `domains.json` changes what the planner LLM can pick.

**Layered service code (a strong convention across all services):** Routes (HTTP) → Services (business logic) → Repositories (data). Dependencies flow downward only; services throw domain errors and never know HTTP; repositories map docs ↔ domain entities. Node uses constructor injection + a DI `Container`; Python uses FastAPI `Depends()`.

---

## 3. The core pipeline (vie-summarizer)

The pipeline is **"plan-driven"** and uses only **3–6 LLM calls**. Canonical stage order (from `docs/ARCHITECTURE.md`):

```
metadata + description analysis
   → [ transcript ‖ frames ]  (parallel, asyncio.Queue)
   → visual context injection (2.5)
   → classifier (fast) ‖ plan (Sonnet)   (the "Plan" stage)
   → extraction (adaptive; chunked for long videos)
   → extraction quality check (retry if coverage < 0.6)
   → [ synthesis ‖ assembly ]
   → enrichment (only for domains with enrichment:true)
   → assembly (pure code, no LLM)
   → [ translation ]  (non-English only)
   → save + stream complete
```

> ⚠️ **Naming note:** the current canonical design replaced **Manifest + Triage** with a single **"Plan"** stage (one Sonnet call instead of two, saving ~30–50s). Treat **"Plan" as canonical**; "triage" is the legacy name and the SSE event is still `triage_complete`. `triage.py` survives only as a low-confidence fallback (when plan confidence < 0.6 → category-based mapping). The old "persona detection" system is removed.

### Stage-by-stage

1. **Metadata** — `yt-dlp` fetches title, channel, duration, chapters, description, thumbnails, category. A description analysis extracts links/resources/social links.

2. **Transcript** (parallel with frames) — multi-source fallback chain, first success wins:
   `S3 cache` → `yt-dlp subtitles` → `youtube-transcript-api` (tenacity retry, 3 attempts, exp backoff 4–30s) → `OpenAI Whisper` (~$0.16/26min, ~5–15min, max 60min) → `Gemini Flash audio` (~$0.04/26min, ~30–90s) → `metadata fallback` (**music videos only**; non-music with no transcript raises `NO_TRANSCRIPT`).
   Cleaned with **spaCy** (filler removal, TF-IDF repetition collapse). Sponsor segments filtered via **SponsorBlock** API.

3. **Frames** (parallel with transcript) — **two-pass with a manifest cache.** Cache check first: `videos/{id}/scenes-v3/manifest.json` (v2 — timestamps, `hiresCount`, persisted vision descriptions; `hiresCount == 0` counts as a MISS so 403-era low-res runs self-heal). On miss, pass 1: yt-dlp downloads **worst-quality** video (~15–20s; all video/audio downloads route through `YTDLP_PLAYER_CLIENTS`, default `android` — never mix in `default`) → **FFmpeg** scene detect @ threshold 0.3 (~200 candidates) → score locally with **OpenCV** on **6 signals** (color saturation HSV, face via Haar, skin fraction inverted, center-crop detail, text density via Canny, perceptual-hash uniqueness) → adaptive **visual tier** (`domains.json` `visualCriticality`: HIGH domains over-select 40 + vision-reselect to drop presenter frames; LOW skips vision; STANDARD = top-8) → select ~25 evenly across duration. Pass 2 (`SCENE_HIRES_ENABLED`): re-extract only the winners at **720p** via stream-URL seek, with a local ≤720p download fallback when the CDN 403s seeks. Then upload winners to S3 + write the manifest → **Tesseract OCR** + **fast-tier vision** in parallel (descriptions persisted into the manifest). A per-video `asyncio.Lock` prevents duplicate concurrent extraction. `FRAME_VISION_ENABLED=false` → OCR-only.

4. **Visual context injection (2.5)** — splice `[VISUAL at M:SS]` (from vision) and `[ON-SCREEN TEXT at M:SS]` (from OCR) annotations into the transcript at the right positions, filtering low-value talking-head frames and de-duplicating OCR against vision.

5. **Classifier + Plan (the Plan stage)** —
   - **Classifier** (`classifier.py`, **fast model**, ~$0.001, 10s timeout): assigns 1 of 14 **domains**, 1 of 18 **formats** (incl. `unboxing` — TCG box openings → gaming+unboxing, consumer products → review+unboxing), and 8 **ContentTraits** booleans. Overrides the rule-based category hint when confidence > 0.6.
   - **Plan** (`plan.py`, **single Sonnet call**, 30s timeout, 2 retries, JSON mode + prompt caching): produces creator identity, core promise, unique angle, extraction guidance, `contentTags`, modifiers, the **tab layout** (component names + goals + item counts), and a compact ~300-char `video_context` that flows to all downstream phases. Domain **playbooks** (`domains.json`, keyed `"<domain>:<format>"`) inject required/preferred/forbidden components + guidance into the prompt, and post-validation `_enforce_domain_policy()` strips `forbidden` components (e.g. `quiz_arena` outside learning/language/tech/science) **before** extraction burns tokens.

6. **Extraction** (adaptive) — injects the domain schema (`prompts/schemas/{tag}.txt`) into a cached `base_extraction.txt` template.
   - **Short (<~15min / 900s threshold):** single call (or "overflow" single call for 5.3K+ words).
   - **Long:** chapter-aware **chunked extraction** (see §5). Output is per-domain structured data (ingredients, steps, code snippets, locations, …).

7. **Extraction quality check** — `score = populated tabs / planned tabs`. If `< 0.6` (`RETRY_SCORE_THRESHOLD`), run a **synthesis-fed retry** (max 1): run synthesis early, build a retry prompt with evidence, re-extract, keep the better result. Costs +$0.05–0.15 for ~10–30% of videos.

8. **Synthesis** (fast model, parallel with assembly) — TLDR, key takeaways, master summary, SEO description. Hierarchical (chapter summaries) for >5 chapters.

9. **Enrichment** (fast model) — quiz / flashcards / scenarios. **Config-driven**, not hardcoded: the `domains.json` `enrichment` map assigns every domain a prompt; **podcast and gaming** route to `enrich_recall.txt` (flashcards only), and quiz/scenarios are code-stripped for any domain where `quiz_arena` is forbidden.

10. **Assembly** — **code, no LLM calls** (no longer <10ms — see moment frame fill below). 29 assemblers in `ASSEMBLER_REGISTRY` transform extraction → component-addressed `TabEntry[]` with pre-resolved props. Enforces domain forbidden/max policy and a **degrade-never-drop demote ladder** (`promotion.py` — e.g. step_player → checklist → display_section, stamped `degradedFrom`); returns per-tab **drop-reason accounting**. Injects frame thumbnails (nearest-timestamp match + ±15s backfill), drops moments beyond video duration (+10s), validates props against component schemas, resolves cross-tab links from static `LINK_RULES`. Guarantees a minimum of 3 tabs via fallback candidates. **Moment frame fill:** `moment_frame_fill.py` extracts a frame AT each still-frameless moment's exact timestamp (stream-URL seek, local-download fallback; 60s + 150s budgets) — `moment_track` tabs are held back during this, stream last, and the client slots them by `tab_ready.position` while SSE heartbeats keep the stream alive.

11. **Translation (non-English only)** — `translate_to_source()` collects every translatable prose string into **one flat list**, makes a single **Haiku** call (`translate_flat.txt`) with mirror-detection (rejects echoed output), and applies results back by path. **Promotes English to the top-level** `tabs`/`meta`, stashes the original under a nested `sourceLanguage = {code, name, isRTL, tabs, meta}` block. **Translation — not assembly — owns the Redis cache write for non-English videos** (assembly skips Redis when `ctx.language != 'en'`). Also generates English Qdrant embeddings for cross-language RAG. Non-blocking: any failure returns input unchanged.

---

## 4. Domain model

**14 primary content tags:** `learning, tech, fitness, food, music, travel, review, project, language, science, gaming, news, podcast, sport`.
**2 modifier tags:** `narrative, finance`. → **16 domain schemas** total (`prompts/schemas/*.txt`).

**8 ContentTraits booleans** (drive component routing in Plan):
`has_steps, has_drills, has_comparison, has_narrative, has_code, has_visual_demo, is_opinionated, is_list`.

**Tags → example tabs:**
- learning: key_points, concepts, takeaways, timestamps (+ quizzes, flashcards, scenarios via enrichment)
- tech: overview, setup, code, patterns, cheat_sheet
- food: overview, ingredients, steps, tips
- fitness: overview, exercises, timer, tips
- travel: overview, itinerary, packing, budget
- review: overview, verdict, pros_cons, specs
- music: overview, analysis, structure, lyrics, credits
- project: overview, materials, tools, steps, safety
- language: phrases, rules, drills, vocabulary
- science: concepts, key_facts, experiments
- gaming: highlights, builds/loadouts, tier_list (unboxing format: pulled items ranked by rarity/value)
- news: events, claims_tracker, key_facts
- podcast: guests, key_quotes, moments (recall-only enrichment)
- sport: match events, formation_diagram, stats
- narrative (modifier): key_moments, quotes, takeaways
- finance (modifier): costs[] + savingTips[] only (primary domain owns budget)

**To add a new domain:** update `domains.json` + add `prompts/schemas/{tag}.txt` + (if enriched) add an enrichment prompt in `prompts/enrich/`.

---

## 5. Long-video support (chunked extraction)

```
SHORT   (<~15–30 min):  full transcript → single extraction call
MEDIUM  (30–120 min):   chapters → 1–2 batch calls → merge
LONG    (2 h+):         chapters → 3–5 batch calls → hierarchical synthesis → merge
```

- **Chapter detection fallback:** YouTube creator chapters → AI-detected (fast model) → ~5-minute time splits → single chunk.
- **Batching:** chapters grouped up to `MAX_TOKENS_PER_BATCH=50000`, `CHAPTER_BATCH_SIZE=3`, run in parallel via `asyncio.Semaphore(EXTRACTION_PARALLEL_BATCHES=2)`.
- **Threshold:** `CHUNKED_EXTRACTION_THRESHOLD=900` (15 min) is the actual code default (prose sometimes says 30 min — trust 900s).
- **Merge:** per-domain — dedup lists by identity, re-number ordered items, keep the richest scalar.
- **Per-batch context:** each batch's prompt carries a `<batch_partial_context>` block telling the model it sees only a slice and to **return empty arrays for absent fields / not pad to meet density quotas**. Without it, partial batches scored 0.40 and triggered cost-doubling retries. The `{batch_context}` placeholder sits *after* the cache-split marker so per-batch text never invalidates the Anthropic prompt cache.
- A **9-hour video** ≈ **~10 LLM calls** total, processed in ~8 minutes.

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
Single-sourced from `packages/shared/src/config/pipeline-version.json` (**currently `v8`**), baked into **both** the idempotency hash and the dedupKey. **Bump it on any change that alters output for the same input** (prompt rewrites, schema changes, model swaps, assembly/cross-tab-link changes). Do **not** bump for logging/perf/frontend-only/non-output fixes. Bumping atomically invalidates all stale keys in both layers; schema changes additionally require clearing the DB cache. **`DISPATCH_GUARD_TTL_SECONDS` (900) must exceed the summarizer's `PIPELINE_LOCK_TTL_SECONDS` (600).** Caching is **versioned, not time-based**.

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
- `POST /videos` — the core submit. Validates optional `Idempotency-Key`, extracts the 11-char `youtubeId`, runs the idempotency gate, reserves estimated cost against the daily tier cap, publishes the job. Returns `{cached:true}` on cache hit or `status:pending {cached:false}` on miss. Over-cap → refund + `429 DAILY_LIMIT_REACHED`. `?bypassCache=true` skips the idempotency gate.
- `GET /videos/:videoSummaryId/stream` — **SSE** stream of the pipeline (see events below). Ends with literal `data: [DONE]`.
- `POST /videos/:id/retry` — manual retry (only when `status==failed`, error is retryable, within 24h).
- `PATCH /videos/:id/override-category` — re-classify through the Plan stage.
- `DELETE /videos/:id` — removes only the **userVideo reference**; the shared cache row is untouched.
- Share: `POST /share/:videoSummaryId` (auth, 20/hr) → `{shareSlug, shareUrl:/s/<slug>}`; `GET /share/:slug` (public, 100/min); `POST /share/:slug/like` (10/min/IP); SSR `GET /s/:slug` (HTML + OG, 60/min) + `GET /s/:slug/og-image.png`.
- Assistant proxy: `POST /api/assistant/chat`, `POST /api/videos/:id/action`, `POST /library/search`.
- `GET /api/users/me/usage` — today's spend/headroom/next-reset.
- Internal: `POST /internal/status` (summarizer → API → rebroadcast over WS), `POST /internal/reconcile-costs` (nightly cron), `POST /internal/run-deletions` (GDPR cron).

### SSE event sequence (v2)
```
metadata → chapters → transcript_ready → sponsor_segments → description_analysis
  → phase* (transcript sub-phases: transcript | transcript_cached | audio_transcription
            | whisper_transcription | metadata_fallback; later: translation)
  → triage_complete (contentTags, modifiers, primaryTag, tabs[], confidence)
  → extraction_progress* → extraction_complete
  → frames (selected frames — timestamps, presigned URLs, OCR)
  → meta (VIEResponseMeta) → tab_ready × N (one component-addressed tab each,
      with `position` = index in the persisted order; moment_track tabs are held
      back for exact-timestamp frame fill and stream LAST — clients slot by
      position, never append; heartbeat events keep the stream alive meanwhile)
  → synthesis_complete → enrichment_complete
  → complete (tabCount, processingTimeMs) → done (confetti) → [DONE]
```
Legacy v1 events (still emitted): `detection_result`, `chapter_ready`, `concepts_complete`.

### WebSocket
`ws://localhost:3000/ws?token=<jwt>` (auth via query param; invalid → close 4001). Events: `video.status`, `expansion.status`, `chat.message`. Fed by the summarizer's `POST /internal/status` callback.

### Tiers
`free` (3 videos/day, 5 chats/output), `pro`/`team` (unlimited). Summaries never expire on any tier (the 30-day free-tier TTL was removed 2026-09-17). Cost gate: `429 DAILY_LIMIT_REACHED` (per-user daily cap) vs `503 COST_LIMIT_EXCEEDED` (global aggregate cap).

---

## 8. Data model (MongoDB)

**System cache (shared):** `videoSummaryCache`, `systemExpansionCache`.
**User data:** `users`, `folders`, `userVideos`.
**Cost & control:** `userCosts`, `userCostAdjustments`, `idempotencyKeys`, `llm_usage` (+ `llm_alerts`, `health_history` for admin), `userDeletions`, `agentNotes`.

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
- `userCosts`: per-user daily aggregate (`date` UTC, `totalCostUsd`, `videoCount`, `creditAdjustmentUsd`). Gates `POST /videos`.
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
- SSE at `/api/videos/:id/stream`: `triage_complete` → pending-tab strip + domain accent; `tab_ready[]` → progressive render **spliced by `position`** (moment tabs stream last after their frame fill); `complete` → celebration. UI phases derive from milestone events via `SSE_PHASE_MAP` (`StreamPhase`: idle | connecting | metadata | transcript | extraction | building | translation | done | cancelled | error, plus `phaseDetail`). On load, `resolve-display-tabs` prefers a completed DB doc over a partial stream.
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
- **Intent routing (keyword, first match wins):** `save note/bookmark` → `note_taker`; `quiz me/test me` → `quiz_generator` (Haiku); `what is/define/explain` → `concept_explain` (Sonnet + RAG); `find/navigate/show me where` → `navigator` (local fuzzy); `compare/cross-reference` → `cross_reference` (Sonnet). No match → default RAG chat.
- **`POST /action`** — structured dispatcher: `{video_id, action, params}` where action ∈ `save_note, quiz_me, find_moment, explain`. Returns `{success, action, data, error, trace_id}` (12-char hex). Rate-limited 30/min/caller.
- **`POST /library/search`** — pure retrieval, no LLM. 1–200 `video_ids`, `query` (1–500 chars), `top_k` (1–50, default 10), optional `sources`. **Trusts the gateway** to verify ownership of every `video_id` before forwarding. Rate-limited 60/min keyed on `X-User-Id`. Returns ranked results with deep-link metadata.
- Errors: `AppError` hierarchy → 400/404/500/502/503 with machine-readable codes; stack traces logged, never returned.
> `QdrantClient.search` is **synchronous** — mock with `MagicMock`, not `AsyncMock`. (Newer client code prefers `query_points()` over the removed `search()`.)

---

## 11. Cross-cutting: queue, observability, errors

### RabbitMQ job queue (behind `USE_QUEUE_PIPELINE=true`; flag-gated)
- `POST /api/videos` publishes a **Zod-validated** job (Pydantic-validated by the consumer) to exchange `vie.pipeline` (direct, durable), routing key `video.process`, into queue `vie.pipeline.jobs` (x-max-priority 10, 1h message TTL, DLX `vie.pipeline.dlx` → DLQ `vie.pipeline.dlq`).
- **Priority by tier:** free=1, pro/team=5.
- **Worker retry:** republish with `attempt+1` up to `WORKER_MAX_RETRIES=3`, then `nack(requeue=False)` → dead-letters. One Sentry event per dead-lettered job (retries fire none).
- **SSE preserved without frontend changes:** the worker acquires the **same Redis pipeline lock** the SSE producer uses; a client opening `/stream` sees the lock held and attaches as a consumer of the Redis Streams event log.
- Admin queue endpoints (require `X-Admin-Key`): `GET /api/admin/queue/stats`, `GET /api/admin/queue/dlq`, `POST /api/admin/queue/replay`.
- Default path (flag off) is a **direct HTTP `POST /summarize`** → `202 Accepted` → FastAPI BackgroundTasks.

### Observability (Langfuse) — strictly best-effort, no-ops when keys unset
- One pipeline run = one trace `pipeline:{videoSummaryId}` (tagged `youtubeId`, `videoSummaryId`, `requestId`). Each LLM call is a child **generation** with usage + metadata (`attempt`, `useFastModel`, `modelOverride`, `latencyMs`, `finishReason`). **Assembly emits no generations** (pure code). Model mix in traces: classifier=fast, plan=sonnet, extraction=sonnet (one generation per chunk), synthesis=fast, enrichment=haiku, `translation_*`.
- Assistant traces chat as `chat:{videoId}` with tool spans + `library_generation` / `library_agent` generations.
- **Prompt registry:** local `.txt` files under `services/summarizer/src/prompts/**` are the source of truth; `vie-langfuse-init` (run-once service) syncs them to Langfuse under `label='production'`. `load_prompt_text` is registry-first with file fallback, recording the version in the trace.
- **Faithfulness judge:** after extraction, fire-and-forget, samples `LANGFUSE_FAITHFULNESS_SAMPLE_RATE=0.2` of items (capped 6/video), asks a Haiku-tier LLM if each claim is transcript-supported, logs a `faithfulness` score (~$0.005/video). **Informational only — never blocks**; runs < 0.7 logged at warning.
- **Golden dataset eval:** 20-video dataset + `run_eval.py --fail-under 0.7` for CI-gradeable scoring (must use `--api-url http://vie-api:3000` from inside the container).
- Aggregate cost lives **locally in MongoDB** (`llm_usage` via the shared `packages/llm-common` callback), not in Langfuse.

### Request-id propagation
Every API request gets a UUID v4 on `x-request-id` (regex-validated `^[A-Za-z0-9_-]{8,128}$`; forged → fresh UUID). It flows: API→frontend (echoed), API→RabbitMQ (`requestId` field), API→summarizer (`X-Request-ID`), API→assistant (`X-Request-ID` on `/chat` + `/action`), bound to structlog contextvars in worker/assistant. Search logs/Langfuse/Sentry by it, or run `./scripts/find-request.sh <id>`.

### Sentry
`@sentry/node` (API) + `sentry-sdk[fastapi]` (Python). Env: `SENTRY_DSN` (empty=no-op), `SENTRY_ENVIRONMENT`, `SENTRY_RELEASE`, `SENTRY_TRACES_SAMPLE_RATE=0.1`. API captures only effective status **≥500** (resolved across Fastify `.statusCode`, `AppError.status`, ZodError→400).

### Error handling model
Canonical `{error, message, details, statusCode}` envelope, one shared code set.
- **Validation/edge:** `INVALID_URL`, `NO_TRANSCRIPT` (422), `VIDEO_TOO_LONG` (>180min/3h), `VIDEO_TOO_SHORT` (<60s), `VIDEO_UNAVAILABLE`, `VIDEO_RESTRICTED`, `LIVE_STREAM`.
- **Server:** `LLM_ERROR`, `DATABASE_ERROR`, `SUMMARIZER_ERROR`.
- **Automatic retry:** max 3, backoff `[5,15,60]s`, retryable = `LLM_ERROR, DATABASE_ERROR, NETWORK_ERROR, RATE_LIMITED`.
- **Manual retry** (`POST /api/videos/:id/retry`): only if `failed` + retryable + within 24h. Non-retryable: `INVALID_URL, VIDEO_TOO_LONG, VIDEO_TOO_SHORT, NO_TRANSCRIPT, VIDEO_RESTRICTED, LIVE_STREAM`.
- Each service **translates errors at its boundary** (e.g. assistant `NOT_FOUND` → HTTP 404 `VIDEO_NOT_FOUND`).

---

## 12. LLM providers, model tiering & cost

**LiteLLM** multi-provider with automatic fallback. Per-stage routing via `LLM_<STAGE>_MODEL` overrides.

| Provider | Default (quality) | Fast (classify/synth) | Context |
|----------|-------------------|------------------------|---------|
| Anthropic | Claude Sonnet 4.6 (`anthropic/claude-sonnet-4-6`) | Claude Haiku 4.5 (`anthropic/claude-haiku-4-5-20251001`) | 200K |
| OpenAI | GPT-4o | GPT-4o-mini | 128K |
| Google | Gemini 2.5 Flash | Gemini 2.5 Flash Lite | 1M |

**Tiering by stage:** Plan = Sonnet · Classifier = fast · Synthesis = fast · Enrichment = **Haiku 4.5** · **Frame vision = Sonnet** (must stay on primary — gpt-4o-mini was reverted; it was only ~20% cheaper and hallucinated OCR on dense frames) · Extraction first pass = primary unless `EXTRACTION_USE_FAST_FIRST`.

**Per-stage timeouts:** Classifier 10s · Plan 30s · Extraction 240s (dynamic) · Synthesis 30s · Enrichment 90s · Assembly: no LLM calls (moment frame fill budgets 60s + 150s when it runs).

**Prompt caching (Anthropic):** `cache_control: {type: ephemeral}` on the static halves of `base_extraction.txt` and `plan.txt`. Cached reads bill ~10% of input ($0.30 vs $3.00/MTok for Sonnet 4.6). ⚠️ Cache breaks (zero hits) if the static half isn't byte-identical between calls (a timestamp/user_goal slipping in invalidates it). `cost_usd` is **net of** the cache discount; list-price ≈ `cost_usd + cache_savings_usd`. Audit with `scripts/audit_cache_credits.py` (exit 2 = Anthropic calls but zero cache hits → investigate).

**Cost per video:**

| Scenario | LLM calls | Cost |
|----------|-----------|------|
| First processing (~20 min, captions) | 7 | ~$0.29 |
| Same video, any user (cache hit) | 0 | **$0.00** |
| Cache expired, S3 frames reused | 0 | ~$0.01 |

Measured on v8 from the `llm_usage` ledger: 7 LLM calls and about $0.29 for a ~20-minute video with captions, about 4 minutes end to end. The sampled faithfulness judge adds up to 6 small calls. The Whisper fallback adds roughly $0.14 when captions are unavailable.

**Per-user daily cost cap:** `llm_usage` aggregates into `userCosts`; `POST /videos` calls `reserveUserCost` (increment-then-check) → `429 DailyLimitReachedError` past the tier cap. Refunds happen **only** on the terminal-status callback (don't double-credit).

---

## 13. Security

- **Two-token JWT** (see §7). `JWT_SECRET ≠ JWT_REFRESH_SECRET`. A soft-deleted user's JWT stays valid for its 15-min TTL, so the `authenticate` hook does a **per-request user lookup** → `403 ACCOUNT_DELETION_PENDING` to kill sessions instantly.
- **Passwords:** bcrypt cost ≥ 10. **Validation:** Zod everywhere (11-char YouTube IDs across watch/youtu.be/embed; 8+ char passwords with upper+lower+digit; lowercased emails ≤255).
- **Rate limits** keyed by `user.id` (auth) or IP: default 100/min; register 5/h, login 10/15min, refresh 30/15min, `POST /videos` 10/24h, share endpoints as noted. Paddle webhook unlimited but signature-verified. Returns `{error:'RATE_LIMITED', retryAfter}` + `X-RateLimit-*` headers.
- **CORS:** localhost:5173 + `FRONTEND_URL`, `credentials:true`, never `*` in prod. **Helmet CSP:** `defaultSrc 'self'`, image hosts `img.youtube.com`/`i.ytimg.com`, `crossOriginEmbedderPolicy` disabled so YouTube thumbnails load.
- **SSRF:** OG-image service validates thumbnail hostnames against a YouTube allowlist. **XSS:** share-page escapes all dynamic values + JSON-LD. **NoSQL injection:** `_ALLOWED_RESULT_KEYS` allowlist in the summarizer's Mongo repo.
- **Payments:** Paddle webhooks verified via HMAC-SHA256 (`PADDLE_WEBHOOK_SECRET`); mandatory in prod, skipped in dev when empty; idempotent.
- **Required secrets:** `JWT_SECRET`, `JWT_REFRESH_SECRET`, `ANTHROPIC_API_KEY`, `PADDLE_WEBHOOK_SECRET`, `INTERNAL_SECRET` (`openssl rand -base64 32`).

---

## 14. GDPR / privacy (Article 17 cascade deletion)

- **Soft delete:** `DELETE /api/users/me` sets `deletedAt=now`, `hardDeleteAt=now+30d`, returns `202 + scheduledHardDeleteAt`, clears the refresh cookie. User immediately frozen (`403 ACCOUNT_DELETION_PENDING`).
- **30-day grace:** `POST /api/users/me/restore` — the **only** authenticated endpoint exempt from the soft-delete check.
- **Scheduled hard delete:** daily cron (~03:00 UTC) → `POST /internal/run-deletions` → `executeHardDelete()` saga deletes in fixed order: `agentNotes → userVideos → folders → userCosts → userCostAdjustments → idempotencyKeys → Qdrant user content (placeholder) → S3 user objects (placeholder) → users (FK anchor, last)` → writes a `userDeletions` audit row. Every step is idempotent (`deleteMany({userId})`) and wrapped in `runStep` that records failures as **warnings without aborting** (smallest PII footprint > rollback).
- **Audit:** `userDeletions` retained **indefinitely**, stores `emailHash` (SHA-256, never plaintext), `initiatedBy` (`self|admin|scheduler`), counts, timing, warnings. Proof of erasure = re-hash an email and match a completed row.
- **NOT deleted** (video-scoped / shared / anonymized): `videoSummaryCache`, Qdrant `transcript_chunks` (`user_id` reserved but always null today), S3 `videos/{youtubeId}`, `llm_usage` (anonymized, 90-day TTL), IP-hashed share signals, external Paddle records.
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
cp .env.example .env          # add ANTHROPIC_API_KEY (or OPENAI/GEMINI)
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
