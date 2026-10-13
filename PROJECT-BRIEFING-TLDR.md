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

## The pipeline (v9; last measured on v8: ~$0.29 and ~4 min for a ~20-min captioned video; streamed over SSE)
```
metadata (+ t=0 background: captions · description analysis · downloads)
→ ONE parallel group:
    text: transcript → tier probe → plan ∥ memory (memory's tldr = the early hero)
          → plan-done reconcile → <video_memory> → provisional Moments tab
    frames: scenes → tier → 720p refine → OCR ∥ vision → <visual_annotations> + code-tab check
    field groups: text groups at plan-done, the visual group at frames-done;
          each landed group streams tab previews  (EXTRACTION_PARALLEL=false: one call after the group)
→ assembly (code, no LLM calls) → late group: moment fill ∥ synthesis ∥ quiz
→ final re-emit (every tab final, overview last) → complete → save
→ [translation] (non-English only) → done
```
- **14 domains** (fitness, food, gaming, language, learning, music, news, podcast, project, review, science, sport, tech, travel — see `domains.json`, the single source) + **2 modifiers** (narrative, finance). Evidence keys (`has_steps`, `has_code`, `is_learnable`, …) from the plan and the memory gate requirements, reconcile and the quiz. `domains.json` also carries per-domain **forbidden** components (quiz_arena = educational-only), format **playbooks** (`gaming:unboxing`, `review:unboxing`), and **visualCriticality** tiers for the frame pipeline.
- **29 registered components** (snake_case, same set in the TS `COMPONENT_REGISTRY` and Python `ASSEMBLER_REGISTRY`): moment_track, step_player, spot_explorer, flash_deck, checklist, info_grid, overview, comparison(+_radar), budget, code_playground, quiz_arena, packing_mission, workout_room, lyrics_karaoke, video_filmstrip, claims_tracker, tier_list, formation_diagram, 3 canvases, display_section + 6 attachments (stat_banner, tip_callout, summary_header, diagram_card, frame_strip, quick_quiz).
- **Frames:** two-pass — yt-dlp worst-quality (player client `android`) → FFmpeg scene-detect (~200) → OpenCV 6-signal scoring → adaptive visual tier → ~25 winners re-extracted at **720p** from the run's one local ≤720p file → S3 `{SCENE_S3_PREFIX}/` (default `scenes-v4`) + manifest v2 (persists vision descriptions; `hiresCount==0` = cache miss) → Tesseract OCR + vision (`LLM_VISION_MODEL`, blank → primary) → a `<visual_annotations>` block for extraction (never injected into the transcript).
- **LLM:** LiteLLM multi-provider (default Sonnet 4.6 / fast Haiku 4.5). Anthropic prompt caching: the plan's static half (1h) and the extraction groups' shared blocks (1h + 5m).

## Output shape
`VIEResponse = { meta, tabs[] }`. Each tab = `{ id, label, emoji, component, props, crossTabLinks?, attachments?, degradedFrom? }`. Frontend does `COMPONENT_REGISTRY[tab.component]` → render. Timestamps seek an **inline** YouTube player in the video hero (moment cards open a frame Lightbox instead; the explicit Jump button seeks). Tabs stream in progressively (`tab_ready` events with a `position` to sort by and `final`: a provisional Moments tab at plan time, previews per extraction group, then every tab once final).

## Caching & "never pay twice" (the defining concern)
- **3 dedup layers:** per-user idempotency hash → cross-user content-addressed `dedupKey` (no userId) → Redis dispatch guard. Backstop = summarizer pipeline lock.
- **`PIPELINE_VERSION`** is baked into the hash + dedupKey; bump it on any output-changing change to invalidate caches. Versioned, not time-based.
- MongoDB `videoSummaryCache` (one row per video) stores `meta` + `tabs` + the `pipeline` record (older rows may hold v1 `triage`/`output` or v2 `assembledMeta`/`assembledTabs`); non-English nests the original under `sourceLanguage` (omitted, not null, for English).

## Assistant / RAG
One Qdrant collection `transcript_chunks` (384-dim, English-only embeddings `all-MiniLM-L6-v2`; non-EN translated first). Chat = embed query → retrieve top-k → LLM → stream. Also `/library/search` (cross-video, no LLM) and `/action` (save_note, quiz_me, find_moment, explain).

## Auth & security
Two-token JWT (15-min access in memory + 7-day HttpOnly refresh cookie, different secrets). Per-request ownership checks at the data layer. Zod/Pydantic validation at boundaries. Rate limits, helmet CSP, SSRF/XSS guards. GDPR Art.17 = soft-delete (30-day grace) → cascade-delete saga + indefinite hashed audit.

## Conventions
React 19 (compiler auto-memoizes), Tailwind v4 CSS-first + OKLCH tokens (no hardcoded colors, No-Inter Rule), shadcn/ui. Layered code (routes→services→repositories), no `any`/no sync-in-async, files <500 lines. **`git stash` is banned; never mutate the working tree without explicit permission; never commit to `main`.**
