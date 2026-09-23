# 🎬 VIE — Video Insight Engine

> **Stop losing knowledge from videos you watch.**

You watch a 2-hour tutorial, learn amazing things, and a week later... it's gone. You can't remember the exact steps, you can't find that one explanation that clicked, and you definitely can't explain it to someone else.
**Video Insight Engine fixes this.**

---

## The Problem

📺 You watch educational YouTube videos all the time.

😤 But videos are **terrible for knowledge retention**:

- Can't search inside them
- Can't highlight or save key parts
- Can't quickly review what you learned
- Rewatching wastes hours

**Result:** 90% of video knowledge evaporates within a week.

## The Solution

> **Turn any YouTube video into an interactive app.**

You paste a URL. VIE analyzes the video — transcript, frames, structure — and generates a custom interactive experience with the right components for that content. A cooking video becomes a recipe with timed steps and ingredient checklist. A tech tutorial becomes a code explorer with runnable snippets. A travel vlog becomes a spot explorer with a visual timeline.

Not a summary. Not a transcript. An **app built from the video's content.**

---

## What VIE Produces

Every video gets a unique set of **interactive tabs** — chosen automatically based on what the video is about.

```
📺 YouTube URL
      ↓
🤖 AI Pipeline (classify + plan → extract → assemble)
      ↓
🎯 Knowledge Base Interactive Tabs (tailored to content type)
```

### A cooking video might get:

| Tab               | Component  | What it does                                          |
| ----------------- | ---------- | ----------------------------------------------------- |
| 🛒 Ingredients    | checklist   | Checkable shopping list with quantities               |
| 👨‍🍳 Steps          | step_player | Timed cooking steps with "Watch this step" video seek |
| 🔪 Tips           | flash_deck  | Swipeable chef tips and storage advice                |
| ⏰ Moments        | moment_track | Value gallery of key moments — click opens a frame lightbox, an explicit Jump button seeks the video |
| 🎞️ Filmstrip      | video_filmstrip | Enriched frame scrubber with captions (suppressed when Moments already covers the frames) |

### A tech tutorial might get:

| Tab         | Component    | What it does                             |
| ----------- | ------------ | ---------------------------------------- |
| 💻 Code     | code_playground | Syntax-highlighted code blocks with copy |
| 📋 Steps    | step_player  | Setup instructions with timestamps       |
| 🔧 Tools    | checklist    | Required tools and dependencies          |
| 📚 Concepts | flash_deck   | Key concept flashcards                   |
| 🧠 Quiz     | quiz_arena   | Knowledge check (educational domains only) |

### 29 registered components available:

moment_track, step_player, spot_explorer, flash_deck, checklist, info_grid, overview, comparison, comparison_radar, budget, code_playground, quiz_arena, packing_mission, workout_room, lyrics_karaoke, video_filmstrip, claims_tracker, tier_list, formation_diagram, concept_canvas, step_flow_canvas, connect_canvas, display_section + secondary attachments (stat_banner, tip_callout, summary_header, diagram_card, frame_strip, quick_quiz)

The AI picks which components to use based on the video's content — not a template.

---

## How It Works

### The Pipeline

```
URL → Metadata → [Transcript ∥ Frames] → Classify + Plan → Extraction → Synthesis → Enrichment → Assembly → SSE Stream → React UI
```

Each stage is a separate async phase. The pipeline streams results to the frontend via Server-Sent Events — the user sees tabs appearing progressively as they're assembled.

### Key Architecture Decisions

**Plan-driven:** A fast classifier plus a single Sonnet plan call classify the video (content tags like "food", "tech", "travel") and design the tab layout. This means a 10-min recipe and a 10-min code tutorial produce completely different output — same pipeline, different components.

**Domain-specific extraction:** 14 domain schemas (learning, tech, fitness, food, music, travel, review, project, language, science, gaming, news, podcast, sport + narrative, finance modifiers) define exactly what data to extract per content type. A food video extracts ingredients, steps, tips. A tech video extracts code snippets, tools, concepts.

**Component-addressed assembly:** Extraction output is transformed into props for specific React components. The frontend just does `INTERACTIVE_REGISTRY[tab.component]` — one lookup, one render.

**Frame extraction with smart scoring:** Two-pass. Pass 1: FFmpeg scene detection on a fast worst-quality download produces ~200 candidates, scored locally on six signals (color saturation, face, skin fraction, center detail, text density, uniqueness); ~25 winners are selected. Pass 2: only the winners are re-extracted at 720p (stream-URL seek, with a local-download fallback for CDN-403 environments) before S3 upload, so vision, OCR, and the UI all get hi-res frames. A versioned S3 manifest (`scenes-v3`) caches the result — including the vision descriptions — for instant re-serves.

**Chunked extraction for long videos:** Videos over 30 minutes are split by YouTube chapters (or AI-detected chapters, or 5-minute time splits). Chapters are batched into LLM calls up to the context window limit. A 9-hour video needs ~10 LLM calls total, not 100+.

**Inline video player with seekTo:** Click any timestamp in any tab → the inline YouTube player (hosted in the video hero) opens, scrolls into view, and seeks to that moment. Step instructions, filmstrip frames, moment Jump buttons — everything is clickable and connected to the video. Moment cards themselves open a frame lightbox instead of seeking; jumping is always an explicit action.

### Response Shape

```json
{
  "meta": {
    "contentTags": ["food", "learning"],
    "primaryTag": "food",
    "userGoal": "Cook a chicken stir fry",
    "tldr": "...",
    "masterSummary": "...",
    "keyTakeaways": ["..."]
  },
  "tabs": [
    {
      "id": "ingredients",
      "label": "🛒 11 Ingredients",
      "emoji": "🛒",
      "component": "checklist",
      "props": { "items": [...] },
      "goal": "Checkable shopping list",
      "crossTabLinks": []
    }
  ]
}
```

---

## Architecture

```
┌───────────────────────────────────────────────────────────────────┐
│                         vie-web (React 19)                         │
│  Tailwind v4 · shadcn/ui · 29 registered interactive renderers    │
│  ComposableOutput → COMPONENT_REGISTRY[tab.component] → render     │
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
│                                                                    │
│  Pipeline Phases:                                                  │
│  metadata → [transcript + frames] parallel                         │
│           → classify + plan → extraction                           │
│           → [synthesis + assembly] parallel → enrichment           │
│           → translation (non-English)                              │
│                                                                    │
│  Services:                                                         │
│  ├── LiteLLM (multi-provider: Anthropic, OpenAI, Google)          │
│  ├── FFmpeg + OpenCV + Tesseract (frame extraction + OCR)         │
│  ├── yt-dlp (video download + subtitles)                          │
│  ├── SponsorBlock (ad filtering)                                  │
│  ├── spaCy (transcript cleaning)                                  │
│  └── S3 (frame + transcript storage)                              │
│                                                                    │
│  Storage:                                                          │
│  ├── MongoDB (video records, structured results)                  │
│  ├── Redis (response cache — same video = instant serve)          │
│  └── S3 (frames, transcripts, audio)                              │
└───────────────────────────────────────────────────────────────────┘
```

### SSE Event Flow

```
metadata → triage_complete → frames → meta → tab_ready × N → synthesis_complete → complete → done
```

Each `tab_ready` event carries one assembled tab plus its `position` in the final tab order. The frontend slots tabs by position as they arrive — moment tabs are held back while exact-timestamp frames are extracted and stream last, so the user sees results appearing in real-time without reordering jumps.

---

## Pipeline Phases (Detail)

### 1. Metadata

Fetch video info via yt-dlp: title, duration, creator, chapters, description, thumbnails, category.

### 2. Transcript (parallel with frames)

Fetch YouTube captions (yt-dlp subtitles → S3 cache → youtube-transcript-api fallback). Clean with spaCy (filler removal, TF-IDF repetition collapse). Filter sponsor segments via SponsorBlock API.

### 3. Frame Extraction (parallel with transcript)

Two-pass. Pass 1: download worst-quality video via yt-dlp (~15-20s; player client set by `YTDLP_PLAYER_CLIENTS`, default `android`). FFmpeg scene detection at threshold 0.3 (~200 candidates). Score each frame locally with OpenCV on six signals: color saturation, face detection (Haar cascades), skin fraction, center detail, text density (Canny edges), visual uniqueness (perceptual hashing). Select ~25 evenly distributed across video duration. Pass 2: re-extract only the winners at 720p (stream-URL seek; local-download fallback when the CDN 403s seeks), then upload to S3 under a versioned prefix with a manifest that also persists the vision descriptions. OCR on text-heavy frames (Tesseract).

### 4. Classify + Plan

A fast classifier (domain + format + traits) runs concurrently with a single Sonnet plan call. Input: video metadata + chapter titles + transcript sample (+ domain playbook if one matches, e.g. unboxing). Output: contentTags, primaryTag, userGoal, tab definitions with component names and goals — with forbidden components stripped before extraction. This decides what the entire extraction phase will look for.

### 5. Extraction

Domain-specific LLM call using schemas from `domains.json`. For short videos: single call with full transcript. For long videos: split by chapters, batch into calls, merge results. Output: structured data per domain (ingredients, steps, code snippets, locations, etc.).

### 6. Synthesis (parallel with assembly)

Fast LLM call. Produces: TLDR, masterSummary, keyTakeaways, seoDescription. For long videos: uses chapter summaries instead of full transcript (hierarchical).

### 7. Enrichment

LLM call for domains with an enrichment mapping. Produces: quiz questions, flashcards, scenario explorations (recall-only domains like podcast/gaming get flashcards only).

### 8. Assembly

No LLM calls. Transforms extraction output into component props for each tab via 29 assemblers, enforcing domain forbidden/max policy and a degrade-never-drop demote ladder. Injects frame thumbnails (nearest-timestamp matching, ±15s backfill), and extracts exact-timestamp frames for still-frameless moments (with SSE heartbeats). Validates all props against component schemas.

---

## Domain Configuration

Single source of truth: `packages/shared/src/config/domains.json`

```json
{
  "food": {
    "label": "Food & Cooking",
    "schemas": ["food"],
    "components": ["checklist", "step_player", "flash_deck", "moment_track"],
    "enrichment": true
  },
  "tech": {
    "label": "Tech & Coding",
    "schemas": ["tech"],
    "components": ["code_playground", "step_player", "checklist", "flash_deck"],
    "enrichment": true
  }
}
```

14 domains: learning, tech, fitness, food, music, travel, review, project, language, science, gaming, news, podcast, sport (+ narrative, finance modifiers). Each domain defines which extraction schemas to use, which components the planner may pick, per-domain required/max/**forbidden** component policy, format-specific playbooks (e.g. `gaming:unboxing`), and visual-criticality tiers for the frame pipeline.

Python reads this via `domain_config.py`. TypeScript reads via `@vie/shared/config`. One config, two runtimes.

---

## Long Video Support

Videos over ~15 minutes (`CHUNKED_EXTRACTION_THRESHOLD` = 900s) use chunked extraction:

```
SHORT (<15 min):   Full transcript → single extraction call
MEDIUM (15-120 min): Chapters → 1-2 batch extraction calls → merge
LONG (2+ hours):   Chapters → 3-5 batch calls → hierarchical synthesis → merge
```

Chapter detection fallback chain: YouTube creator chapters → AI-detected chapters (fast model) → time-based splits (~5 min each) → single chunk.

Multiple chapters are batched into a single LLM call up to the context limit (~50K tokens per batch). A 9-hour video needs ~10 LLM calls total, processing in ~8 minutes.

---

## Frame Pipeline

```
Pass 1 — detect + score (worst-quality download, YTDLP_PLAYER_CLIENTS=android):
  yt-dlp → FFmpeg scene detect (0.3) → ~200 candidates on disk
    ↓
  Score locally (OpenCV, no network, 6 signals):
    - Color saturation (HSV) → catches final dishes, landscapes
    - Face detection (Haar cascade) + skin fraction → penalizes presenter shots
    - Center detail (Laplacian) → catches the subject in frame
    - Text density (Canny edges) → catches slides, code
    - Visual uniqueness (perceptual hash) → catches real scene changes
    ↓
  Adaptive visual tier (domains.json visualCriticality):
    HIGH domains over-select 40 + vision reselect · LOW skips vision · else top-8
    ↓
  Select ~25 evenly distributed across video duration
    ↓
Pass 2 — hi-res refine (SCENE_HIRES_ENABLED):
  re-extract only the winners at 720p via stream-URL seek
  (local ≤720p download fallback when the CDN 403s seeks)
    ↓
Upload winners to S3 under scenes-v3/ + manifest.json (v2 — timestamps,
hiresCount, persisted vision descriptions; hiresCount == 0 counts as a
cache miss so low-res runs self-heal)
    ↓
Thumbnail injection: nearest-timestamp matching (+ ±15s backfill,
exact-timestamp moment frame fill as last resort)
```

---

## Smart Caching

Redis caches the full VIEResponse keyed by `youtube_id` + `PIPELINE_VERSION`. Same video = instant serve from cache ($0.00). No re-processing, no LLM calls. `?bypassCache=true` forces a fresh run.

S3 stores frames and transcripts per video. Frame reuse is manifest-gated: if a valid `scenes-v3/manifest.json` exists (with hi-res frames), frame extraction is skipped entirely — including the vision descriptions, which are restored from the manifest.

| Scenario                    | LLM Calls            | Cost       |
| --------------------------- | -------------------- | ---------- |
| First processing of a video | 5-10                 | $0.02-0.12 |
| Same video, any user        | 0 (cache hit)        | $0.00      |
| Same video, cache expired   | 0 (S3 frames reused) | $0.01      |

---

## Cost Per Video

| Component                                                | Cost           |
| -------------------------------------------------------- | -------------- |
| Transcript (YouTube captions)                            | $0.00 (free)   |
| Frame extraction (FFmpeg + OpenCV)                       | ~$0.001        |
| OCR on ~22% of frames (Tesseract)                        | ~$0.001        |
| S3 storage (~25 frames × 28KB)                           | ~$0.002        |
| LLM calls (classify + plan + extraction + synthesis + enrichment) | $0.02-0.10 |
| **Total**                                                | **$0.02-0.10** |

Scales with video length: 15 min = ~$0.02, 2 hours = ~$0.05, 9 hours = ~$0.12.

---

## Tech Stack

| Layer                | Technology                                               |
| -------------------- | -------------------------------------------------------- |
| **Frontend**         | React 19 · TypeScript · Tailwind v4 · shadcn/ui          |
| **Backend API**      | Node.js · Fastify                                        |
| **AI Pipeline**      | Python · FastAPI · LiteLLM (Anthropic / OpenAI / Google) |
| **Frame Processing** | FFmpeg · OpenCV · Tesseract · yt-dlp                     |
| **NLP**              | spaCy (transcript cleaning) · SponsorBlock API           |
| **Database**         | MongoDB (records) · Redis (cache)                        |
| **Storage**          | AWS S3 (frames, transcripts, audio)                      |
| **Shared Config**    | `domains.json` → Python + TypeScript                     |

---

## LLM Provider Support

VIE uses LiteLLM for multi-provider support with automatic fallback:

```
Primary: configurable (Anthropic / OpenAI / Google)
Fast model: configurable (used for classifier, synthesis, enrichment, vision)
Fallback: automatic on rate limit or error
```

| Provider  | Default Model     | Fast Model            | Context Window |
| --------- | ----------------- | --------------------- | -------------- |
| Anthropic | Claude Sonnet 4.6 | Claude Haiku 4.5      | 200K tokens    |
| OpenAI    | GPT-4o            | GPT-4o-mini           | 128K tokens    |
| Google    | Gemini 2.5 Flash  | Gemini 2.5 Flash Lite | 1M tokens      |

Per-phase model routing (`LLM_<STAGE>_MODEL` overrides): fast models for classifier/synthesis/enrichment/vision, default models for quality-sensitive stages (plan, extraction).

---

## Quick Start

> Production on a single EC2 host: [docs/DEPLOY.md](./docs/DEPLOY.md).

```bash
# Clone
git clone https://github.com/kfiravra/video-insight-engine.git
cd video-insight-engine

# Configure
cp .env.example .env
# Add at least one LLM API key:
#   ANTHROPIC_API_KEY=sk-ant-...
#   OPENAI_API_KEY=sk-...
#   GEMINI_API_KEY=AIza...

# Launch
docker-compose up -d

# Open
open http://localhost:5173
```

Paste a YouTube URL and watch tabs appear in real-time as the pipeline processes.

---

## Project Structure

```
video-insight-engine/
├── apps/
│   └── web/                          # React 19 frontend
│       └── src/
│           ├── components/           # Shared components (ui/, vie/, layout/, …)
│           ├── features/
│           │   └── video-output/     # Output rendering
│           │       ├── components/output/
│           │       │   ├── component-registry.tsx  # 29 interactive renderers
│           │       │   └── interactive/            # The renderers themselves
│           │       ├── contexts/     # VideoPlayerContext (seekTo)
│           │       └── lib/streaming/ # SSE stream processor
│           └── pages/
├── packages/
│   └── shared/
│       └── src/config/
│           ├── domains.json          # Single source of truth
│           └── pipeline-version.json # PIPELINE_VERSION (currently v8)
├── api/                              # Node.js API gateway (Fastify) — repo root, not services/
├── services/
│   ├── summarizer/                   # Python AI pipeline
│   │   └── src/
│   │       ├── prompts/              # LLM prompts (plan, classify, extraction, etc.)
│   │       │   ├── schemas/          # 14 domain + 2 modifier extraction schemas
│   │       │   └── enrich/           # Per-domain enrichment prompts
│   │       ├── services/
│   │       │   ├── pipeline/         # Phase orchestration (phases/, assembly/)
│   │       │   ├── media/            # FFmpeg, frame scoring, hires refine, OCR, S3
│   │       │   └── transcription/    # Transcript fetch, clean, chunk
│   │       └── config.py             # Model map, thresholds, settings
│   ├── assistant/                    # RAG chat service (Python)
│   └── admin/                        # Usage/observability panel (Python)
└── docker-compose.yml
```

---

## Use Cases

**Cooking** — Ingredient checklists, timed step-by-step instructions, chef tips as flashcards, frame thumbnails on each cooking step.

**Tech tutorials** — Code blocks with syntax highlighting, tool/dependency checklists, concept flashcards, moment track with code-on-screen frame detection.

**Travel vlogs** — Spot explorer with locations and frame images, budget calculators, itinerary timelines, tips as flashcards.

**Lectures & courses** — Quiz challenges, concept flashcards, scenario explorations, chapter-based navigation, hierarchical summaries for long content.

**Music** — Lyrics player with synced timestamps, song structure as a moment track with replayable section highlights.

**Product reviews** — Pro/con lists, rating breakdowns, comparison tables, gear lists.

**Fitness** — Exercise step players with timers, equipment checklists, workout timelines.

### Who Uses VIE

**Students** — Process a 3-hour lecture into quiz challenges, concept flashcards, and chapter-based navigation. Review before exams with scenario explorations instead of rewatching. Every timestamp is clickable — jump to exactly the explanation you need.

**Developers** — Turn conference talks and tutorials into code explorers with syntax-highlighted snippets, tool/dependency checklists, and concept flashcards. Frame intelligence detects code-on-screen moments and links them to the moment track.

**Home Cooks** — Cooking mode with timed step-by-step instructions, checkable ingredient lists, and chef tip flashcards. Click any step to seek the video to that exact moment. Never pause-and-scroll again.

**Researchers & Creators** — Organize processed videos into folders. Memorize key sections into a personal collection. Ask questions about saved content via RAG chat. Share interactive summaries with a public link.

**Teams** — Share processed training videos as interactive knowledge bases. New hires get quiz challenges and scenario explorations instead of "watch these 40 hours of recordings."

---

## What Makes This Different

| Feature                           | YouTube       | Notion | ChatGPT           | VIE                           |
| --------------------------------- | ------------- | ------ | ----------------- | ----------------------------- |
| Interactive components from video | ❌            | ❌     | ❌                | 16 domain-aware components    |
| Click timestamp → video seeks     | Chapters only | ❌     | ❌                | Every element is clickable    |
| Domain-specific extraction        | ❌            | Manual | Generic summary   | 14 domain schemas             |
| Quiz / flashcards / scenarios     | ❌            | Manual | On request        | Auto-generated per domain     |
| Frame intelligence (OCR + vision) | ❌            | ❌     | ❌                | Scored frames with thumbnails |
| Progressive streaming UI          | ❌            | ❌     | Token stream      | Tabs appear as assembled      |
| Multi-language + RTL support      | Captions only | Manual | English-only      | Auto-detect, translate, RTL   |
| Same video = instant ($0.00)      | N/A           | N/A    | Costs per request | Redis + S3 cache              |
| Organize & share                  | Playlists     | Pages  | Chat history      | Folders + public links        |

---

## Documentation

📖 **[Full Documentation →](./CLAUDE.md)**

🧠 **LLM onboarding briefing:** paste **[PROJECT-BRIEFING.md](./PROJECT-BRIEFING.md)** (full) or **[PROJECT-BRIEFING-TLDR.md](./PROJECT-BRIEFING-TLDR.md)** (quick) into any LLM chat to give it full project context in one shot.

---

## Roadmap

- [x] Plan-driven pipeline with 29 registered interactive components
- [x] Domain-specific extraction (14 domains)
- [x] Two-pass frame extraction with smart scoring + 720p hi-res refinement
- [x] Inline video player with seekTo wiring
- [x] SSE streaming with progressive tab rendering
- [x] Redis caching (same video = instant)
- [x] Multi-provider LLM support (Anthropic / OpenAI / Google)
- [x] SponsorBlock filtering
- [x] Chunked extraction for long videos
- [x] Multi-language support with RTL and translation
- [x] Whisper + Gemini audio fallback for videos without captions
- [ ] Playlist processing with cross-video connections
- [x] Assistant chat with RAG (Qdrant vector search) — single-video + library scope, action channel
- [ ] Collections with drag-and-drop organization
- [ ] Speaker diarization
- [ ] Browser extension
- [ ] Mobile app

---

## Contributing

This project uses **Claude Code** for AI-assisted development.

1. Read [CLAUDE.md](./CLAUDE.md) for project context
2. Follow patterns in existing pipeline phases
3. Domain additions: update `domains.json` + add schema in `prompts/schemas/` + add enrichment prompt in `prompts/enrich/`
4. Follow patterns in `.claude/skills/`

---

## License

MIT

---

<p align="center">
  <b>Stop losing knowledge from videos. Turn them into apps.</b>
</p>
