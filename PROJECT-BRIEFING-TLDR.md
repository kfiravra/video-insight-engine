# Video Insight Engine (VIE) — TL;DR Briefing

> Paste this into a fresh LLM chat for quick context. For the full version (data model, every endpoint, gotchas), use `PROJECT-BRIEFING.md`.

## What it is
**VIE turns any YouTube URL into a custom interactive web app** — not a summary, an *app built from the video's content*. An AI pipeline analyzes transcript + frames + structure, **classifies** the video, and generates a tailored set of **interactive tabs** (a cooking video → checkable ingredients + timed step player; a coding tutorial → code explorer + tool checklist). Classification drives generation, so the same pipeline yields different output per video. Same video re-serves to any user instantly at **$0.00** via caching.

## Services (Docker-Compose monorepo)
| Service | Tech | Port | Role |
|---|---|---|---|
| vie-web | React 19 + Vite + TS | 5173 | Frontend SPA (SSE/WS) |
| vie-api | Node + Fastify + TS | 3000 | Gateway: auth, REST, MongoDB, orchestration |
| vie-summarizer | Python + FastAPI + LiteLLM | 8000 | The AI pipeline |
| vie-assistant | Python + FastAPI + Qdrant | 8001 | RAG chat + tools |
| vie-admin | Python + React | 8002 | Ops dashboard |
| + worker | (shares summarizer img) | — | RabbitMQ queue consumer |

Stores: MongoDB 7 (records), Redis 7 (cache + locks), Qdrant (RAG vectors), RabbitMQ (optional queue), S3 (frames/transcripts). Shared config: `packages/shared/src/config/domains.json` (one config, read by Python + TS). **API gateway lives at top-level `api/`** (not `services/api`).

## The pipeline (7 LLM calls, ~$0.29 and ~4 min for a ~20-min captioned video, streamed over SSE)
```
metadata → [transcript ‖ frames] → visual injection
→ classifier(fast) ‖ plan(Sonnet)   ← "Plan" stage (replaced Manifest+Triage; SSE event still triage_complete)
→ extraction (chunked for >15min) → quality check (retry if <0.6 coverage)
→ [synthesis ‖ assembly] → enrichment (per-domain prompt map) → assembly (code, no LLM calls;
   holds back moment tabs for exact-timestamp frame fill, then streams them last)
→ [translation] (non-English only) → save + complete
```
- **14 domains** (fitness, food, gaming, language, learning, music, news, podcast, project, review, science, sport, tech, travel — see `domains.json`, the single source) + **2 modifiers** (narrative, finance). 8 `ContentTraits` booleans route components. `domains.json` also carries per-domain **forbidden** components (quiz_arena = educational-only), format **playbooks** (`gaming:unboxing`, `review:unboxing`), and **visualCriticality** tiers for the frame pipeline.
- **29 registered components** (snake_case, same set in the TS `COMPONENT_REGISTRY` and Python `ASSEMBLER_REGISTRY`): moment_track, step_player, spot_explorer, flash_deck, checklist, info_grid, overview, comparison(+_radar), budget, code_playground, quiz_arena, packing_mission, workout_room, lyrics_karaoke, video_filmstrip, claims_tracker, tier_list, formation_diagram, 3 canvases, display_section + 6 attachments (stat_banner, tip_callout, summary_header, diagram_card, frame_strip, quick_quiz).
- **Frames:** two-pass — yt-dlp worst-quality (player client `android`) → FFmpeg scene-detect (~200) → OpenCV 6-signal scoring → adaptive visual tier → ~25 winners re-extracted at **720p** (local-download fallback) → S3 `scenes-v3/` + manifest v2 (persists vision descriptions; `hiresCount==0` = cache miss) → Tesseract OCR + fast-tier vision → `[VISUAL at M:SS]` injected into transcript.
- **LLM:** LiteLLM multi-provider (default Sonnet 4.6 / fast Haiku 4.5). Anthropic prompt caching on static prompt halves.

## Output shape
`VIEResponse = { meta, tabs[] }`. Each tab = `{ id, label, emoji, component, props, crossTabLinks?, attachments?, degradedFrom? }`. Frontend does `COMPONENT_REGISTRY[tab.component]` → render. Timestamps seek an **inline** YouTube player in the video hero (moment cards open a frame Lightbox instead; the explicit Jump button seeks). Tabs stream in progressively (`tab_ready` events, each with a `position` to slot by — moment tabs arrive last).

## Caching & "never pay twice" (the defining concern)
- **3 dedup layers:** per-user idempotency hash → cross-user content-addressed `dedupKey` (no userId) → Redis dispatch guard. Backstop = summarizer pipeline lock.
- **`PIPELINE_VERSION`** is baked into the hash + dedupKey; bump it on any output-changing change to invalidate caches. Versioned, not time-based.
- MongoDB `videoSummaryCache` (one row per video) stores **both** v1 (`triage`/`output`) and v2 (`assembledMeta`/`assembledTabs`); non-English nests the original under `sourceLanguage` (omitted, not null, for English).

## Assistant / RAG
One Qdrant collection `transcript_chunks` (384-dim, English-only embeddings `all-MiniLM-L6-v2`; non-EN translated first). Chat = embed query → retrieve top-k → LLM → stream. Also `/library/search` (cross-video, no LLM) and `/action` (save_note, quiz_me, find_moment, explain).

## Auth & security
Two-token JWT (15-min access in memory + 7-day HttpOnly refresh cookie, different secrets). Per-request ownership checks at the data layer. Zod/Pydantic validation at boundaries. Rate limits, helmet CSP, SSRF/XSS guards. GDPR Art.17 = soft-delete (30-day grace) → cascade-delete saga + indefinite hashed audit.

## Conventions
React 19 (compiler auto-memoizes), Tailwind v4 CSS-first + OKLCH tokens (no hardcoded colors, No-Inter Rule), shadcn/ui. Layered code (routes→services→repositories), no `any`/no sync-in-async, files <500 lines. **`git stash` is banned; never mutate the working tree without explicit permission; never commit to `main`.**
