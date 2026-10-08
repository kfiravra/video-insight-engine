# Service: vie-summarizer

Python service that processes YouTube videos into structured summaries.

**Type:** HTTP service (FastAPI + BackgroundTasks)

---

## Tech Stack

| Technology | Purpose |
|------------|---------|
| Python 3.11+ | Runtime |
| FastAPI | Web framework + SSE streaming |
| youtube-transcript-api | Fetch transcripts |
| yt-dlp | Video metadata + chapters |
| LiteLLM | Multi-provider LLM abstraction (Anthropic, OpenAI, Gemini) |
| pymongo | MongoDB driver |
| aioboto3 | Async S3 client (transcripts + frame storage) |
| Pydantic | Validation & Settings |
| google-genai | Gemini native SDK (audio transcription) |
| structlog | Structured logging |

---

## Project Structure

```
services/summarizer/
├── Dockerfile
├── requirements.txt
├── pyproject.toml
└── src/
    ├── __init__.py
    ├── main.py                   # FastAPI app + routes
    ├── config.py                 # Settings + model mapping
    ├── dependencies.py           # DI providers
    ├── exceptions.py             # Custom exceptions
    ├── logging_config.py         # structlog configuration
    ├── middleware.py             # Request ID middleware
    │
    ├── routes/
    │   ├── deps.py               # require_internal_secret (X-Internal-Secret, constant-time)
    │   ├── stream.py             # SSE streaming endpoint
    │   ├── override.py           # Detection override endpoint
    │   └── purge.py              # POST /internal/videos/{id}/purge (global video delete)
    │
    ├── services/
    │   ├── llm.py                # LLMService (call_llm + call_llm_fast)
    │   ├── llm_provider.py       # LiteLLM multi-provider abstraction
    │   ├── usage_tracker.py      # LLM usage tracking
    │   ├── override_state.py     # In-memory override state
    │   ├── video_purge.py        # Qdrant + S3 prefix + Redis purge for one video (per-store warnings)
    │   ├── status_callback.py    # Status callback
    │   │
    │   ├── pipeline/             # Plan-based summarization pipeline
    │   │   ├── tier_probe.py         # Tier probe: domain+format+has_visual_demo at transcript-ready (Haiku, temp 0)
    │   │   ├── plan.py               # Plan stage → merged manifest+triage in single Sonnet call
    │   │   │                         #   (+ _render_playbook, _enforce_domain_policy)
    │   │   ├── triage.py             # Triage validation/fallback (TriageResult model + tab validation)
    │   │   ├── prompt_builder.py     # Schema-injection prompt builder + video_context
    │   │   ├── extractor.py          # Adaptive extraction (single/overflow/chunked) + prompt caching
    │   │   ├── extraction_quality.py # Extraction quality check + synthesis-fed retry
    │   │   ├── extraction_merger.py  # Per-domain merge + dedup for chunked extraction
    │   │   ├── enrichment.py         # Quiz/flashcards (enrichment map; recall-only for podcast/gaming)
    │   │   ├── synthesis.py          # TLDR, takeaways (Sonnet, hierarchical for long)
    │   │   ├── translation.py        # Source-language translation (flat-list batched)
    │   │   ├── faithfulness.py       # Faithfulness sampling
    │   │   ├── assembly/             # Assembly package (extraction → component props)
    │   │   │   ├── core.py               # assemble_response + per-tab drop accounting
    │   │   │   ├── registry.py           # ASSEMBLER_REGISTRY (29 assemblers) + component inference
    │   │   │   ├── assemblers_*.py       # primary/learning/overhaul/secondary assembler groups
    │   │   │   ├── promotion.py          # Degrade-never-drop demote ladder (degradedFrom)
    │   │   │   ├── moment_frame_fill.py  # Exact-timestamp frame fill for frameless moments
    │   │   │   ├── attachments.py        # Secondary attachments (excluded_kinds dedup)
    │   │   │   ├── normalizers.py, density.py, cross_tab.py, text_utils.py
    │   │   ├── phases/               # Phase runners (metadata, transcript, frames, extraction,
    │   │   │                         #   enrichment, synthesis, assembly, translation)
    │   │   ├── post_processor.py     # Tab cleanup, celebrations, count validation (tag-aware)
    │   │   └── pipeline_helpers.py   # SSE events, timer, data classes, run_task_with_heartbeat
    │   │
    │   ├── transcription/        # Transcript fetching & storage
    │   │   ├── transcript.py         # Transcript cleaning & formatting
    │   │   ├── transcript_fetcher.py # Multi-source fallback chain
    │   │   ├── transcript_chunker.py # Chapter-aware transcript splitting
    │   │   ├── transcript_store.py   # S3 transcript persistence
    │   │   ├── gemini_transcriber.py # Gemini Flash transcription
    │   │   └── whisper_transcriber.py # Whisper fallback
    │   │
    │   ├── media/                # Frame extraction, vision & S3 storage
    │   │   ├── scene_extractor.py    # Scene keyframe extraction (yt-dlp + FFmpeg) + manifest-v2 cache
    │   │   ├── frame_scorer.py       # Frame scoring (visual, face, skin, center-detail, text, uniqueness)
    │   │   ├── frame_analyzer.py     # Vision LLM analysis (scene type, visual_subject, content)
    │   │   ├── visual_tier.py        # Adaptive visual tier (high/standard/low from metadata)
    │   │   ├── scene_detect.py       # Scene detection + zero-candidate ladder (0.3 → 0.15 → uniform seeks)
    │   │   ├── hires_refiner.py      # Pass-2 720p re-extraction of selected frames (local file seeks)
    │   │   ├── hires_prefetch.py     # The run's one 720p file (ctx.hires_video): frames + moment fill
    │   │   ├── local_video.py        # One-shot ≤720p yt-dlp download
    │   │   ├── frame_ocr.py          # OCR on text-heavy frames (Tesseract)
    │   │   ├── frame_extractor.py    # Single-frame ffmpeg seek (refiner + moment fill)
    │   │   ├── image_dedup.py        # Perceptual hashing for dedup
    │   │   ├── s3_client.py          # Async S3 client
    │   │   └── download_utils.py     # Download helpers + yt-dlp player-client routing
    │   │
    │   └── video/                # YouTube & metadata
    │       ├── youtube.py            # Video metadata (yt-dlp)
    │       ├── sponsorblock.py       # Sponsor segment detection
    │       ├── description_analyzer.py # Description analysis
    │       └── playlist.py           # Playlist extraction (yt-dlp)
    │
    ├── repositories/
    │   ├── base.py               # Repository protocols
    │   └── mongodb_repository.py # MongoDB implementation
    │
    ├── prompts/
    │   ├── plan.txt              # Plan prompt → merged manifest+triage (identity, tabs, extraction guidance)
    │   ├── triage.txt            # Triage prompt (fallback only, injects component_toolkit.txt)
    │   ├── component_toolkit.txt # Component descriptions + datasource paths (injected into plan/triage). Density table is generated from domains.json `densityGates` via `{density_gates}` placeholder
    │   ├── base_extraction.txt   # Schema-injection extraction template + video_context + prompt caching
    │   ├── tier_probe.txt        # Tier-probe prompt (14 domains + 18 formats incl. unboxing, has_visual_demo — domains.json is the source)
    │   ├── chapter_detect.txt    # AI chapter detection prompt (fast model)
    │   ├── quality_rules.txt     # JSON extraction quality rules
    │   ├── enrich/               # Per-domain enrichment prompts (+ video_context + tab_goals)
    │   │                         #   incl. enrich_recall.txt (flashcards-only: podcast, gaming)
    │   ├── synthesis.txt         # Synthesis prompt (+ video_context + tone matching)
    │   └── schemas/              # Domain schemas (injected into base_extraction)
    │       ├── learning.txt, tech.txt, fitness.txt, food.txt, music.txt, travel.txt
    │       ├── review.txt, project.txt, language.txt, science.txt
    │       ├── gaming.txt, news.txt, podcast.txt, sport.txt
    │       ├── narrative.txt     # Modifier
    │       └── finance.txt       # Modifier
    │
    ├── utils/
    │   ├── json_parsing.py       # Robust JSON recovery
    │   ├── llm_retry.py          # LLM call with timeout + retry + backoff
    │   ├── worker_pool.py        # ProcessPoolExecutor for CPU-bound tasks
    │   ├── content_extractor.py  # Summary/bullet extraction
    │   ├── transcript_slicer.py  # Time-range transcript slicing
    │   ├── language_utils.py     # Language detection, RTL check, language instructions
    │   └── constants.py          # Constants
    │
    ├── shared_config/
    │   ├── __init__.py
    │   └── domain_config.py      # Reads domains.json (Docker mount or local fallback). Also renders the planner's `{valid_components}` list + `{density_gates}` table from config
    │
    └── models/
        ├── schemas.py            # Pydantic models
        ├── domain_types.py       # Domain data models (Food, Travel, Music, etc.)
        ├── pipeline_types.py     # Pipeline models (PlanResult, ManifestResult, TriageResult, etc.)
        └── vie_response_v2.py    # VIEResponse v2 models (TabEntry, CrossTabLink, etc.)
```

---

## Environment Variables

```bash
# Database
MONGODB_URI=mongodb://vie-mongodb:27017/video-insight-engine

# LLM Provider Configuration
LLM_PROVIDER=anthropic          # anthropic, openai, or gemini
LLM_FAST_PROVIDER=              # Optional: separate provider for fast model
LLM_FALLBACK_PROVIDER=          # Optional: cross-provider fallback after one same-provider retry (primary-model calls)
LLM_MODEL=                      # Optional: override default model
LLM_FAST_MODEL=                 # Optional: override fast model
LLM_MAX_TOKENS=4096
LLM_FAST_MAX_TOKENS=2048

# Provider API Keys (set for providers you use)
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=                 # Required if using OpenAI or Whisper
GEMINI_API_KEY=                 # Enables Gemini audio transcription (faster than Whisper)
GOOGLE_API_KEY=                 # Required if using Gemini as LLM provider

# S3 Media Storage (transcripts, frames, audio)
S3_BUCKET=vie-transcripts
S3_PRESIGNED_URL_EXPIRY=21600   # Presigned URL validity (seconds); >= api FRAME_URL_TTL_SECONDS
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=your-key      # AWS credentials
AWS_SECRET_ACCESS_KEY=your-secret
PROMPT_VERSION=v1.0             # For generation tracking

# Vector store / RAG indexing
QDRANT_HOST=vie-qdrant
QDRANT_PORT=6333
QDRANT_ENABLED=true
EMBEDDING_MODEL_NAME=all-MiniLM-L6-v2  # Drop-in alternatives must keep VECTOR_SIZE=384 (e.g. BAAI/bge-small-en-v1.5)

# Whisper audio fallback resilience
WHISPER_ENABLED=true
WHISPER_MAX_DURATION_MINUTES=600
WHISPER_CLIENT_TIMEOUT_SECONDS=300.0   # Per-request HTTP timeout for one chunk (bounds a stalled upload)
WHISPER_MAX_RETRIES=1                  # Retries per chunk (SDK default is 2 — capped to bound worst-case latency)
WHISPER_CHUNK_CONCURRENCY=3            # Concurrent chunk transcription (respects OpenAI per-key rate limits)

# Frame pipeline (media/)
SCENE_EXTRACTION_ENABLED=true
SCENE_S3_PREFIX=scenes-v4              # Versioned frame/manifest prefix — bump to invalidate the frame cache
SCENE_HIRES_ENABLED=true               # Pass-2 720p refinement of the selected frames
SCENE_HIRES_FALLBACK_TIMEOUT=180.0     # Hi-res seek budget in the run's local 720p file (media/hires_refiner.py)
YOUTUBE_PROXY_URL=                     # One proxy for every YouTube request (metadata, captions, every download)
YOUTUBE_PROXY_EXIT_COUNT=1             # Sticky exits (USERNAME-1…N) a 429 or bot check rotates through on every YouTube call (max 3); 1 = no rotation
YTDLP_PLAYER_CLIENTS=android           # yt-dlp player clients for pass 1 + audio downloads; empty = yt-dlp defaults
YTDLP_HIRES_PLAYER_CLIENTS=web_embedded,android  # 720p download only (android caps at 360p); retries with the line above
FRAME_TIER_ENABLED=true                # Adaptive visual tiers (HIGH: overselect + vision reselect before hires)
FRAME_VISION_ENABLED=true              # Frame descriptions by the vision model; false = OCR only
FRAME_VISION_PARALLEL=true             # Vision in parallel batches; false = one call with every frame
LLM_VISION_MODEL=                      # Blank = the primary model (Sonnet), which vision stays on

# Extraction concurrency + chunking
EXTRACTION_PARALLEL=true               # Chunked batches run in parallel; false = one batch at a time
EXTRACTION_PARALLEL_BATCHES=6          # Max extraction calls in flight per run; drop to 4 if pipeline.timing records 429s
CHUNKED_EXTRACTION_THRESHOLD=900       # Videos longer than this (s) may split the transcript into chapter batches
MAX_TOKENS_PER_BATCH=50000             # One batch's estimated transcript tokens
MAX_MINUTES_PER_BATCH=40               # One batch's span of video
EXTRACTION_FORCE_SPLIT_CHUNKS=4        # Sub-batches when a long video's chunked transcript collapses to one batch

# Prompts
PROMPT_SOURCE=registry                 # registry (Langfuse label wins) | disk (local .txt re-read per call, dev-only; ignored unless ENVIRONMENT is a dev name)

# SSE streaming
SSE_HEARTBEAT_SECONDS=12.0             # Keepalive cadence during silent phases — must stay under the gateway's 300s undici timeout

LOG_LEVEL=INFO
LOG_FORMAT=console              # console or json
```

---

## Vector Store Indexing (transcript + output)

After every successful pipeline run, two background tasks index the video's
content into Qdrant for the assistant to retrieve:

| Task | Source field | Phase | Content |
|---|---|---|---|
| `store_transcript_chunks` | `source="transcript"` | assembly | `chunk_transcript()` output (English text; original-language text retained in `text_original` for non-English videos) |
| `store_default_output_chunks` | `source="default_output"` | assembly (English videos) / translation (non-English videos) | Per-component chunking of the assembled tabs via `output_chunker.py` — emits one chunk per natural retrieval unit (one `keyTakeaways[i]`, one quiz question, one comparison row, etc.) |

For non-English videos, output indexing is deferred to the translation phase
so it embeds the promoted English `ctx.assembled_tabs` instead of
source-language strings — the embedding model is English-trained, and
embedding source-language tabs on it produces poor retrieval quality.

Both paths pre-delete by `(video_id, source)` before upsert (and pre-delete
runs *before* chunking, so a chunker exception still cleans up prior runs'
orphans). Output chunks from all tabs upsert in a **single batched call**
with per-chunk `tab_id` / `tab_component` metadata, instead of one
round-trip per tab. Keys are deterministic:
`sha256(f"{source}:{video_id}:{tab_id or ''}:{prop_path or ''}:{chunk_idx}")`,
with the legacy `f"{video_id}_{idx}"` format preserved for transcript-only
points (so existing pre-migration points remain addressable).

### Output chunker rules

`output_chunker.py` registers an explicit handler per component. Components
not in the registry emit zero chunks (silent fallbacks would index button
labels and IDs as embeddings, polluting retrieval). Cross-cutting rules:

- Drop chunks under 6 whitespace-separated tokens (low signal)
- Never embed code, numbers, timestamps, URLs, enum flags, raw JSON
- For the catch-all `display_section`, only flatten string values whose key
  appears in the whitelist: `text`, `description`, `summary`, `explanation`,
  `content`, `analysis`, `caption`, `label`, `instruction`, `tip`, `note`

Adding a new component to `assemblers.py` requires adding a row to
`_COMPONENT_HANDLERS` in `output_chunker.py` before that component's content
will be retrievable.

---

## Processing Pipeline (Plan-Driven)

The pipeline uses 3-6 LLM calls with a plan-first architecture:

```
 1. CONNECT via SSE
    └─▶ GET /summarize/stream/{videoSummaryId}
    └─▶ If cached: stream structured result immediately
    └─▶ If pending: start processing pipeline

 2. FETCH METADATA (one yt-dlp extract_info) → validate_duration → t=0 group
    └─▶ Title, channel, thumbnail, duration, chapters, picked caption track
    └─▶ Category pre-detection from metadata
    └─▶ SSE: metadata event. The phase ends here (~5 s); once the video is
    │   accepted it starts, in the background: the caption fetch, description
    │   analysis (links, resources, social links, timestamps), and — unless the
    │   frame manifest is cached (checked during extract_info) — the low-res and
    │   720p downloads (media/hires_prefetch.py, ctx.lowres_video / ctx.hires_video).
    │   A rejected video starts nothing.

 3. TRANSCRIPT + FRAMES + DESCRIPTION (parallel via asyncio.Queue)
    └─▶ transcript waits for the caption fetch; frames for the low-res file;
    │   the description member emits the description_analysis SSE when it lands
    ┌─▶ TRANSCRIPT (Multi-Source Fallback Chain)
    │   └─▶ 0. S3 cached transcript (avoids all YouTube calls)
    │   └─▶ 1. yt-dlp subtitles (embedded in video metadata)
    │   └─▶ 2. youtube-transcript-api (with rate limit retry)
    │   └─▶ 3. Gemini Flash (audio transcription, ~30-90s, ~$0.04/26min)
    │   └─▶ 4. OpenAI Whisper (audio fallback, ~5-15min, ~$0.16/26min)
    │   └─▶ (audio paths 3+4 bypass LiteLLM → emit their own llm_usage cost row +
    │        Langfuse generation via transcription/usage.py; see llm-cost-model.md)
    │   └─▶ SSE: transcript_ready event
    │
    └─▶ FRAMES (smart frame selection, two-pass, non-critical)
        └─▶ S3 cache check — MANIFEST-only: `videos/{id}/{SCENE_S3_PREFIX}/manifest.json`
        │   (v2) carries per-frame timestamps, galleryIndices, hiresCount and the
        │   persisted visionDescriptions. A hit skips extraction AND restores the
        │   vision descriptions (cached frames have no local file, so vision can't
        │   re-run); `hiresCount == 0` (every hi-res seek failed) is treated as a
        │   MISS so a 403-era low-res run self-heals on the next reprocess.
        │   (prefix is versioned via SCENE_S3_PREFIX, default "scenes-v4" — bump to
        │    invalidate after quality changes; "scenes" holds pre-hires low-res
        │    frames, "scenes-v2" pre-adaptive-vision frames. NOTE: scene_NNNN keys
        │    are overwritten in place per run.)
        └─▶ Per-video asyncio.Lock prevents duplicate concurrent extractions
        └─▶ Adaptive visual tier (media/visual_tier.py, domains.json visualCriticality,
        │   FRAME_TIER_ENABLED): HIGH over-selects FRAME_OVERSELECT_COUNT candidates and
        │   vision-describes them BEFORE hires refinement so subject-matter frames beat
        │   presenter shots (floor FRAME_RESELECT_FLOOR); LOW skips vision; STANDARD = top-8.
        └─▶ Pass 1 — detection: reads the run's WORST-quality download (started by the
        │   metadata phase; ~15-20s; 144p is plenty for scene detection + scoring).
        │   All yt-dlp VIDEO/AUDIO downloads (detection, whisper audio; the 720p file uses
        │   YTDLP_HIRES_PLAYER_CLIENTS first) route through YTDLP_PLAYER_CLIENTS (default "android" — YouTube
        │   403s the web client's download URLs from some environments; never mix in
        │   "default": merged format lists let bestvideo pick a 403ing web DASH format).
        │   Metadata/subtitle extraction deliberately does NOT use it (android lacks subs).
        └─▶ FFmpeg scene detection on local file (~10-15s, 20-50x realtime;
        │   scale/quality via SCENE_DETECT_SCALE_WIDTH / SCENE_JPEG_QUALITY)
        └─▶ Smart scoring (CPU only, ~2-3s, 6 signals): visual interest (0.20), face (0.10),
        │   skin fraction inverted (0.15), center-crop detail (0.20), text density (0.15),
        │   uniqueness (0.20) — single image decode per frame; Haar failure caches as
        │   disabled and returns neutral 0.5
        └─▶ Time-slot selection: ~25 frames evenly distributed across video duration
        └─▶ Gallery classification: top ~12 frames by score for Visual Moments tab
        └─▶ Pass 2 — hi-res refinement (hires_refiner.py, SCENE_HIRES_ENABLED):
        │   re-extracts only the SELECTED frames at 720p by seeking the run's ONE local
        │   ≤720p file (SCENE_HIRES_CONCURRENCY parallel, SCENE_HIRES_FALLBACK_TIMEOUT
        │   budget; per-frame fallback to low-res on failure). media/hires_prefetch.py
        │   (ctx.hires_video) starts that download right after pass 1 so it overlaps scene
        │   detection + scoring; it stays on disk for moment fill and is deleted after it
        │   (or when the run fails). No stream-URL seeks: client-bound googlevideo URLs
        │   403 plain ffmpeg, and proxied runs would seek from the blocked host IP.
        │   Swaps frame paths in place → S3 upload, vision, and OCR all get the best available.
        └─▶ Batch parallel S3 upload (only selected frames — not all detected) + manifest write
        └─▶ OCR + Vision LLM analysis run in parallel:
            ├─▶ OCR: Tesseract on text-heavy frames
            └─▶ Vision: top 8 frames → fast tier (scene_type, visual_subject, content) ~$0.02-0.03;
                descriptions are persisted into the manifest for future cache hits
        └─▶ SSE: frames event (selected frames only — timestamps, presigned URLs, OCR text)
        └─▶ Graceful degradation: failure returns empty result, pipeline continues

 3.5 VISUAL CONTEXT INJECTION (after both parallel phases complete)
    └─▶ Injects [VISUAL at M:SS] annotations from vision LLM into transcript at correct positions
    └─▶ Injects [ON-SCREEN TEXT at M:SS] annotations from OCR for non-vision frames
    └─▶ Uses segment timestamps for precise positioning (fallback: character estimation)
    └─▶ Filters talking_head frames with no educational value
    └─▶ Deduplicates: vision frames don't also get OCR annotations
    └─▶ Toggle: FRAME_VISION_ENABLED=false skips vision (OCR-only like before)

 4. CLASSIFIER + PLAN (1-2 LLM calls)
    └─▶ Classifier (fast model): domain + format + traits classification (14 domains, 18 formats
    │   incl. `unboxing` — TCG box openings route gaming+unboxing, consumer products review+unboxing)
    │   └─▶ 10s timeout, 1 retry, ~$0.001 per video, json_mode
    │   └─▶ Overrides rule-based category_hint when confidence > 0.6
    │   └─▶ Sets content_format on PipelineContext (tutorial, commentary, reaction, etc.)
    │   └─▶ ContentTraits: 8 booleans (has_steps, has_drills, has_comparison, has_narrative,
    │   │   has_code, has_visual_demo, is_opinionated, is_list) — drives component routing in Plan
    │   └─▶ Skipped when admin override is active
    └─▶ Plan (Sonnet, 30s timeout, 2 retries, json_mode + prompt caching)
        └─▶ Single call replaces old Manifest + Triage (2 calls → 1, saves ~30-50s)
        └─▶ Analyzes: creator identity, core promise, unique angle, extraction guidance
        └─▶ Designs: contentTags, modifiers, tab layout with component toolkit
        └─▶ Selectable component list + density guidance are config-generated from domains.json
            (`{valid_components}` via `render_valid_component_names`, `{density_gates}` via
            `render_density_gate_table` in `shared_config/domain_config.py`, injected at plan time) —
            edit `components` in domains.json to change what the planner can pick. `densityGates`
            is **advisory LLM-steering only**; the assembler's per-component hard caps are independent.
        └─▶ Domain playbooks (domains.json `playbooks`, keyed "<domain>:<format>", currently
        │   gaming:unboxing + review:unboxing): `_render_playbook()` injects required/preferred/
        │   forbidden components + planGuidance into the prompt. Merge semantics via
        │   effective_requirements(): forbidden = UNION, required = playbook overrides domain,
        │   max always from domain.
        └─▶ Post-validation `_enforce_domain_policy()`: strips forbidden components from the
        │   validated tab plan BEFORE extraction burns tokens (quiz_arena is educational-only —
        │   allowed for learning/language/tech/science, forbidden elsewhere)
        └─▶ Item counts for extraction quality validation
        └─▶ video_context flows to all downstream phases (compact ~300 chars)
        └─▶ 14 primary tags (domains.json): learning, tech, fitness, food, music, travel, review,
        │   project, language, science, gaming, news, podcast, sport
        └─▶ 2 modifier tags: narrative, finance
        └─▶ Fallback: category-based mapping if confidence < 0.6
        └─▶ SSE: triage_complete, meta events

 5. ADAPTIVE EXTRACTION (1-5+ LLM calls, json_mode + prompt caching)
    └─▶ Schema-injected: base_extraction.txt + schemas/{tag}.txt per content tag
    └─▶ Prompt caching: static template (schemas/rules/instructions) cached, transcript dynamic
    └─▶ SHORT (<30 min):
    │   └─▶ <5.3K words: single extraction call
    │   └─▶ 5.3K+ words: overflow extraction (single call, dynamic timeout)
    └─▶ LONG (>30 min, with chapters):
    │   └─▶ Chapter splitting: YouTube chapters → AI detect → time-split → single fallback
    │   └─▶ Batch chapters by 50K token limit
    │   └─▶ Parallel extraction per batch (max 3 concurrent, asyncio.Semaphore)
    │   └─▶ Fast model for multi-batch, default model for single batch
    │   └─▶ Per-domain merge: dedup lists, re-number ordered items, keep richest scalars
    └─▶ Pydantic validation on all output
    └─▶ Post-extraction: count validation against plan (advisory, logs warnings)
    └─▶ SSE: extraction_progress events, then extraction_complete

 5b. EXTRACTION QUALITY CHECK (0-1 additional LLM calls)
    └─▶ Scores extraction coverage: populated tabs vs plan tabs (score 0.0-1.0)
    └─▶ Skips tabs with meta/synthesis/enrichment dataSources
    └─▶ If score < 0.6: synthesis-fed retry — runs synthesis early, builds retry prompt with evidence
    └─▶ Re-extracts and keeps the better result (higher populated count)
    └─▶ Max 1 retry, cost: ~$0.05-0.15 extra for ~10-30% of videos
    └─▶ Exception-safe: retry failure uses original extraction

 6b. EXTRACTION COUNT VALIDATION (advisory, no LLM calls)
    └─▶ Compare plan item counts vs extraction output (60% threshold)
    └─▶ Logs warnings only — does not retry
    └─▶ Exception-safe: validation failure is non-blocking

 7. ENRICHMENT (0-1 LLM calls, 45s timeout, 2 retries)
    └─▶ Domains with enrichment mapping get quiz + flashcards + scenarios
    └─▶ Domain gate via domains.json enrichment map (dynamic, not hardcoded);
    │   podcast + gaming route to enrich/enrich_recall.txt (flashcards only, no quiz/scenarios)
    └─▶ enrich() takes content_format; quiz/scenarios are code-stripped when quiz_arena is
    │   forbidden for the domain (guardrail on top of the prompt routing)
    └─▶ Non-critical: failure returns None gracefully
    └─▶ SSE: enrichment_complete event (if applicable)

 8. SYNTHESIS (1 LLM call, Sonnet, 30s timeout, 2 retries)
    └─▶ TLDR, takeaways, master summary
    └─▶ Hierarchical mode for long videos (>5 chapters): chapter summaries + truncated extraction (6K chars)
    └─▶ Exception-safe: failure emits empty synthesis_complete
    └─▶ SSE: synthesis_complete event

 9. ASSEMBLY + SAVE + COMPLETE
    └─▶ Assembly: code, 0 LLM calls — transforms extraction → component-addressed TabEntry[]
    │   (no longer <10ms: moment_frame_fill below may run frame I/O with heartbeats)
    └─▶ 29 assemblers in ASSEMBLER_REGISTRY (assembly/registry.py — spot_explorer, moment_track,
    │   code_playground, quiz_arena, tier_list, claims_tracker, … + secondary/attachment assemblers)
    └─▶ Domain policy: components in effective_requirements(domain, format).forbidden are dropped
    │   (reason `domain_forbidden`); per-domain max caps enforced (reason `domain_max_cap`)
    └─▶ Degrade-never-drop ladder (assembly/promotion.py): a tab failing its primary component's
    │   validation demotes down _DEMOTE_LADDER (e.g. step_player → checklist → display_section,
    │   tier_list → info_grid → display_section) instead of dropping; demoted tabs are stamped
    │   `degradedFrom` and re-pass enforce_density. Non-demotable by design: quiz_arena,
    │   video_filmstrip, lyrics_karaoke, frame_strip.
    └─▶ Per-tab drop accounting: assemble_response returns {"meta", "tabs", "dropped"} — drop
    │   reasons: assembler_raised, validation_empty_list, assembler_returned_none,
    │   domain_forbidden, domain_max_cap, all_timestamps_impossible (feeds tabsDropped/droppedTabs
    │   telemetry instead of the old designed−assembled subtraction). The plan stage's drops are
    │   prepended: reason `invalid_datasource` = an unregistered dataSource with no same-domain
    │   registry path for the tab's component (plan.py `_validate_data_sources`); when that
    │   leaves no tab, the domain defaults stand in, `planFallback: true` is persisted and
    │   tabsDesigned counts only the plan's drops
    └─▶ Frame thumbnail injection: items with timestamps get thumbnailUrl from nearest S3 frame;
    │   a relaxed second pass (±15s window) backfills still-thumbless moment items
    └─▶ Timestamp hygiene: moment_track items beyond video duration (+10s) are dropped;
    │   a tab emptied by that drop is removed (dropped reason `all_timestamps_impossible`)
    └─▶ Gallery tab: ~12 curated frames from gallery_frames (not all uploaded frames)
    └─▶ Cross-tab links resolved from static LINK_RULES
    └─▶ SSE: tab_ready events (progressive rendering), each with `position` = index in the
    │   persisted tab order. moment_track tabs are HELD BACK: assembly/moment_frame_fill.py
    │   extracts a frame AT each still-frameless moment's timestamp by seeking the run's one
    │   local 720p file (already downloaded by the frames phase; a manifest cache hit starts
    │   it here, for ≥3 moments only; cap 12 frames, 150s budget) and then deletes it; heartbeats
    │   keep the SSE hop alive meanwhile, then the moment tabs stream WITH their images and the
    │   client slots them by `position` so streamed order == persisted order
    └─▶ Store result to MongoDB + Redis cache (if enabled). Redis response-cache keys are
    │   namespaced by PIPELINE_VERSION (packages/shared/src/config/pipeline-version.json,
    │   currently v8 — bumped v6→v8 by the frame-quality + moment-redesign work).
    │   bypassCache=true submissions arrive with force_refresh (worker payload) OR the
    │   `forceRefresh` flag stamped on the videoSummaryCache version row by the api —
    │   either skips the Redis response cache; the flag is $unset on completion.
    └─▶ Transcript S3 storage (background, non-blocking)
    └─▶ Qdrant chunks (if enabled): transcript embeddings (background task)
    └─▶ SSE: complete event (tabCount, processingTimeMs)
    └─▶ SSE: done event + [DONE] signal

10. TRANSLATION (non-English videos only, ~5-15s)
    └─▶ Triggered when ctx.language != "en" (detected from transcript)
    └─▶ translate_to_source() — batched, salvage-tolerant translation
    │   └─▶ Walks the assembled tree, collects every translatable prose
    │   │   string into one flat list (`_collect_strings` + `_SKIP_KEYS`
    │   │   leaf-only deny-list for structural/asset/enum keys)
    │   └─▶ Haiku calls via src/prompts/translate_flat.txt (replaces
    │   │   the deleted src/prompts/translate.txt; flat-list contract
    │   │   means the model returns a list of equal length, applied
    │   │   back into a deep copy via `_set_at_path`)
    │   └─▶ Batching: strings are split into batches of ≤200
    │   │   (`_MAX_TRANSLATION_BATCH`), up to 4 batches concurrently
    │   │   (`_MAX_CONCURRENT_TRANSLATION_BATCHES`) — keeps long videos
    │   │   under the per-call token ceiling and provider rate limits.
    │   └─▶ Salvage-on-failure (`_translate_or_salvage`): a batch that
    │   │   fails (LLM error / length mismatch) is retried once split in
    │   │   half; any half that still fails is salvaged as English
    │   │   (left untranslated) rather than discarding the whole
    │   │   translation. Returns `(strings, salvaged_mask)` — a
    │   │   same-length boolean mask marking salvaged positions.
    │   └─▶ Mirror detection samples only genuinely-translated strings
    │       (salvaged positions excluded via `salvaged_mask`, so kept
    │       English doesn't inflate the mirror ratio): rejects output if
    │       all 3 longest are byte-identical AND >50% of all strings are
    │       byte-identical (small-payload threshold: 33% under 10 strings).
    │       Protects against the "small model echoes input" failure mode.
    │   └─▶ `_SKIP_KEYS` denies: id, component, language, url, s3Key, code,
    │       timestamp, time, seconds, startSeconds, endSeconds, emoji,
    │       correctIndex, difficulty, mood, sets, frameCaption,
    │       frameSceneType, frameEvidence. Notably does NOT deny
    │       reps/rest/duration — the fitness schema emits prose at
    │       those keys ("30 שניות", "AMRAP", "until failure").
    └─▶ Promote English to primary on ctx.assembled_tabs / ctx.assembled_meta
    │   and stash the original-language artifact under sourceLanguage
    │   = {code, name, isRTL, tabs, meta} (tabs/meta deep-copied so a
    │   downstream mutation of ctx.assembled_tabs can't corrupt the
    │   persisted source block).
    └─▶ Whisper translate (audio → English text) feeds the Qdrant
    │   embedding path so RAG search works cross-language.
    └─▶ Owns the Redis response-cache write for non-English videos:
    │   assembly phase intentionally SKIPS the cache write when
    │   ctx.language != "en". Translation then writes the final
    │   English-primary payload (including the sourceLanguage block)
    │   to Redis. Without this split, source-language tabs would
    │   freeze into Redis for the full TTL and silently break the
    │   FE language toggle on every cache hit.
    └─▶ Owns the terminal status + `done` event for non-English videos:
    │   assembly leaves the doc `status="processing"` and does NOT emit
    │   `done`; translation writes `status="completed"` and emits the
    │   `done` event only after the sourceLanguage block is persisted, so
    │   the FE refetch on `done` always sees a completed doc with the
    │   toggle (fixes fake-completed English-only on interrupted runs).
    └─▶ language_instruction injected into upstream LLM phases (plan,
    │   extraction, synthesis, enrichment) — produces source-language
    │   output that this phase then translates.
    └─▶ Non-blocking: any failure (LLM error, mirror, length mismatch)
        returns the input unchanged with no sourceLanguage key — the FE
        simply renders no language toggle.
```

---

## Key Implementations

### LLM Service (Thin Wrapper)

```python
# src/services/llm.py
class LLMService:
    """Thin wrapper around LLMProvider. Pipeline modules use call_llm_with_retry()."""

    def __init__(self, provider: LLMProvider):
        self._provider = provider

    async def call_llm(self, prompt: str, max_tokens: int = 2000, timeout: float | None = None) -> str:
        effective_timeout = timeout or settings.LLM_TIMEOUT_SECONDS
        async with asyncio.timeout(effective_timeout):
            return await self._provider.complete(prompt, max_tokens=max_tokens, timeout=effective_timeout)
```

### LLM Retry Utility

```python
# src/utils/llm_retry.py — All pipeline stages use this instead of calling llm_service directly
async def call_llm_with_retry(
    llm_service, prompt, *, max_tokens=4096, timeout=60.0, max_retries=2, stage_name="unknown"
) -> str | None:
    """Timeout + retry + exponential backoff. Returns raw string or None (never raises)."""

# Stage-specific configurations:
# Plan:        timeout=30s,  retries=2  (Sonnet, replaces old Manifest + Triage)
# Extraction:  timeout=120s, retries=1  (dynamic timeout for overflow)
# Enrichment:  timeout=45s,  retries=2
# Synthesis:   timeout=30s,  retries=2
```

### Pipeline Modules

```python
# All pipeline modules follow the same pattern:
# - Accept llm_service, repository, and domain-specific args
# - Call call_llm_with_retry() for LLM interactions (not llm_service directly)
# - Return typed results (Pydantic models or dicts)

# src/services/pipeline/plan.py
async def run_plan(llm_service, transcript, video_data, ...) -> PlanResult | None
# Single Sonnet call replaces old Manifest + Triage (2 calls → 1)

# src/services/pipeline/triage.py (fallback/validation only)
# TriageResult model + tab validation when plan confidence < 0.6

# src/services/pipeline/extractor.py
async def extract(llm_service, transcript, triage, ...) -> AsyncGenerator[dict, None]

# src/services/pipeline/enrichment.py
async def enrich(llm_service, content_tag, extraction_data, ...) -> EnrichmentData | None

# src/services/pipeline/synthesis.py
async def synthesize(llm_service, extraction_text, video_title, ...) -> SynthesisResult

# src/services/pipeline/post_processor.py
def validate_extraction_counts(manifest: ManifestResult, extraction: dict) -> list[str]
```

**Model mapping (config.py):**
```python
MODEL_MAP = {
    "anthropic": {"default": "anthropic/claude-sonnet-4-6", "fast": "anthropic/claude-haiku-4-5-20251001"},
    "openai": {"default": "openai/gpt-4o", "fast": "openai/gpt-4o-mini"},
    "gemini": {"default": "gemini/gemini-2.5-flash", "fast": "gemini/gemini-2.5-flash-lite"},
}
```

---

## Content Tag System

The pipeline uses triage (LLM) to determine content tags from manifest + metadata. Each tag has a domain schema injected into a shared extraction template.

### 10 Primary Content Tags + 2 Modifiers

| ContentTag | Category Fallback | Domain Schema | Enrichment |
|------------|-------------------|---------------|------------|
| learning | education (default) | `schemas/learning.txt` | quiz, flashcards, scenarios |
| tech | coding, programming | `schemas/tech.txt` | quiz, flashcards, scenarios |
| fitness | fitness | `schemas/fitness.txt` | - |
| food | cooking | `schemas/food.txt` | - |
| music | music | `schemas/music.txt` | - |
| travel | travel | `schemas/travel.txt` | - |
| review | reviews | `schemas/review.txt` | - |
| project | diy, craft | `schemas/project.txt` | - |
| language | — | `schemas/language.txt` | - |
| science | — | `schemas/science.txt` | - |
| narrative | podcast, interview | `schemas/narrative.txt` | Modifier only |
| finance | — | `schemas/finance.txt` | Modifier only |

### SSE Event Protocol

Every event below has a Pydantic contract in
`src/models/sse_events.py`, validated on emission by `sse_event()`
(`pipeline_helpers.py`): outside production a payload/name violation raises
(drift fails the suite); in production it logs `sse_event_contract_violation`
and emits anyway. `tests/test_sse_event_models.py` pins this table's event
list against the model registry.

| Event | Data |
|-------|------|
| `cached` | `{videoSummaryId}` (only for cached results) |
| `metadata` | `{title, channel, thumbnailUrl, duration}` (fields may be `null` on cached legacy docs) |
| `transcript_ready` | `{duration}` |
| `phase` | `{phase}` — progress marker (`transcript_cached`, `transcript`, `audio_transcription`, `whisper_transcription`, `metadata_fallback`, `translation`) |
| `description_analysis` | `{links, resources, socialLinks}` (concurrent with manifest) |
| `triage_complete` | `{contentTags, modifiers, primaryTag, tabs, confidence}` |
| `extraction_progress` | `{section, percent, batch?, of?}` — chunked path emits `batch`/`of` per batch with `section="chunked"`; rate-limited fallback batches use `section="chunked-sequential"` |
| `extraction_complete` | `{domain-keyed data}` |
| `enrichment_complete` | `{quiz?, flashcards?, scenarios?}` (enrichment-mapped domains; recall-only domains emit flashcards without quiz/scenarios) |
| `synthesis_complete` | `{tldr, keyTakeaways, masterSummary, seoDescription}` |
| `frames` | `{videoId, frames: [{index, timestamp, url, s3Key?, ocrText?}]}` (scene frames) |
| `meta` | `{title, contentTags, modifiers, primaryTag, tabCount, tabLabels, degraded?, ...}` (progressive meta) |
| `tab_ready` | `{id, label, emoji, component, props, crossTabLinks?, position?}` (progressive tab; `position` = index in the persisted tab order — held-back moment_track tabs arrive last and are spliced in by it) |
| `complete` | `{tabCount, processingTimeMs, degraded}` (v2 completion) |
| `error` | `{message, code?}` — `code` is an `ErrorCode` value (see docs/ERROR-HANDLING.md) |
| `token` | `{phase, token}` — legacy token streaming; protocol slot kept, no current emitter |
| `heartbeat` | `{ts}` — keepalive emitted every `SSE_HEARTBEAT_SECONDS` (12s) during long silent phases (e.g. multi-minute Whisper) so the API-gateway SSE proxy does not abort an idle-but-live stream. **Frontend treats it as a no-op.** |
| `done` | `{videoSummaryId, processingTimeMs?, degraded?, cached?}` (terminal + confetti trigger; `cached: true` on cached serves). For non-English videos this is **deferred to the translation phase** — assembly marks the doc `processing` and translation emits `done` after writing `status="completed"`. |
| `[DONE]` | Terminal signal |

> `sponsor_segments` was documented here for years but never emitted by any
> code path — removed 2026-07-12 (project-score-9 4.5c). The `done` event
> carries no `phase` field (that was a doc-only invention).

### Key Design Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| Triage-first | LLM picks tags + designs tabs | More flexible than fixed output types |
| Schema injection | `base_extraction.txt` + `schemas/{tag}.txt` | One extraction prompt, domain schemas swapped in |
| Plan stage | Single Sonnet call replaces Manifest + Triage | 2 calls → 1, saves ~30-50s, better coherence |
| Plan fallback | Falls back to category-based mapping if confidence < 0.6 | Safety net when LLM plan fails or is low-confidence |
| Count validation advisory | Logs warnings at 60% threshold | Never blocks pipeline, just flags missing items |
| Legacy coercion | `field_validator(mode="before")` | Accepts old string format for travel tips and music analysis |
| Finance modifier costs-only | `costs[]` + `savingTips[]`, no budget | Primary domain owns budget structure |
| Adaptive extraction | 1-3 calls by word count | Prevents token overflow on long videos |
| Category fallback | Map video category to content tag | Safety net when triage confidence < 0.6 |
| Shared domain config | `@vie/shared` `domains.json` via Docker mount | Single source of truth for domains, tabs, gradients, categories, **planner-selectable `components`**, and the **`densityGates`** table across TS + Python. `plan.txt` `{valid_components}` + `component_toolkit.txt` `{density_gates}` are generated from it (no hardcoded prompt lists); `test_contract_parity.py` asserts the generated prompt matches config. NOTE: `densityGates` is advisory LLM-steering text — assembler enforcement (`_TAB_ITEM_CAPS`, per-assembler min/max, `density.py`) is independent. |

---

## Output Schema

The pipeline stores results in two formats for backward compatibility:

### v2 Schema (current — component-addressed tabs)

```python
# MongoDB document structure (after pipeline completes):
{
    # v1 fields (still written for backward compat)
    "triage": {
        "contentTags": ["learning"],       # 1-2 primary content tags
        "modifiers": [],                    # 0-2 modifier tags (narrative, finance)
        "primaryTag": "learning",           # First content tag
        "tabs": [                           # LLM-designed tab layout
            {"id": "key_points", "label": "Key Points", "emoji": "💡", "dataSource": "learning.keyPoints"},
            {"id": "concepts", "label": "Core Concepts", "emoji": "🧠", "dataSource": "learning.concepts"}
        ],
        "confidence": 0.95
    },
    "output": { ... },                      # Domain-keyed extraction data
    "enrichment": { ... },                  # Quiz, flashcards, scenarios (all enrichment-mapped domains)
    "synthesis": {
        "tldr": "...",
        "keyTakeaways": ["..."],
        "masterSummary": "...",
        "seoDescription": "..."
    },

    # v2 fields (assembly stage output — used by frontend)
    "assembledMeta": {
        "videoId": "...",
        "videoTitle": "...",
        "creator": "...",
        "contentTags": ["learning"],
        "modifiers": [],
        "primaryTag": "learning",
        "userGoal": "...",
        "tldr": "...",
        "keyTakeaways": [...],
        "masterSummary": "...",
        "seoDescription": "..."
    },
    "assembledTabs": [                      # Component-addressed tabs
        {
            "id": "key_points",
            "label": "Key Points",
            "emoji": "💡",
            "component": "key_points",      # Maps to COMPONENT_REGISTRY on frontend
            "props": {                      # Pre-resolved props for the component
                "items": [...]
            },
            "crossTabLinks": [
                {"targetTab": "concepts", "label": "Related Concepts"}
            ]
        }
    ]
}
```

### Concept Canvas Schema (learning & science domains)

The `concept_canvas` component (`assemble_concept_canvas` in `assembly/assemblers.py`)
renders a typed, grouped knowledge graph. Output shape is `{concepts, groups}`:

```jsonc
{
  "concepts": [
    {
      "name": "Term as the speaker uses it",
      "emoji": "🔁",
      "definition": "Speaker's explanation (≤ ~200 chars)",
      "example": "…",                 // optional
      "analogy": "…",                 // optional
      "group": "Foundations",          // thematic cluster (lane)
      "timestamp": 312,                // optional — drives frame-evidence thumbnail
      "connections": [
        { "to": "EXACT other concept name", "type": "causes" }
      ]
    }
  ],
  "groups": ["Foundations", "Mechanics", "Applications"]  // ordered, de-duped lane order
}
```

- **Typed connections** — `type` ∈ `causes | requires | contrasts | partOf | relatesTo`
  (`_CONCEPT_RELATIONS`). A bare string connection (legacy data) coerces to
  `{to, type: "relatesTo"}` via `_normalize_connections`.
- **Validation** — connection targets are matched against real concept names
  (case-insensitive); hallucinated / self-referential targets are dropped, casing
  canonicalized.
- **Grouping** — the LLM assigns each concept a `group`; when absent, groups are
  derived from the connection graph (`_cluster_groups`, connected components),
  defaulting to `"Concepts"` (`_DEFAULT_GROUP`).
- **Cap** — capped at 30 concepts (`_CONCEPT_CANVAS_MAX`); grouping, not
  truncation, is the density strategy. Prompted via `schemas/learning.txt` and
  `schemas/science.txt`. See [DATA-MODELS.md](./DATA-MODELS.md) for the TS types
  (`ConceptRelation`, `ConceptConnection`, `ConceptItem`).

### Frontend Meta Resolution

The API builds a clean response using `meta-builder.ts`:
1. New shape: `doc.meta` (has `contentTags` directly)
2. Legacy v2: `doc.assembledMeta` + `doc.synthesis`
3. Oldest: `doc.triage` + `doc.synthesis`

### Security: Field Allowlist

`save_structured_result()` in `mongodb_repository.py` uses a field allowlist (`_ALLOWED_RESULT_KEYS`) to prevent arbitrary field injection into MongoDB documents.

See `src/models/domain_types.py` for domain data models, `src/models/pipeline_types.py` for pipeline types, and `src/models/vie_response_v2.py` for assembly output models.

---

## Transcript Fallback Chain

The summarizer uses a multi-source fallback chain to maximize transcript availability:

```
┌─────────────────────────────────────────────────────────┐
│                  TRANSCRIPT SOURCES                     │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  0. S3 Cached Transcript                               │
│     └─▶ Avoids all YouTube calls                       │
│     └─▶ Source: "s3" (cached-{original_source})        │
│                         │                               │
│                         ▼                               │
│  1. yt-dlp Subtitles                                   │
│     └─▶ Extracted during video metadata fetch           │
│     └─▶ Source: "ytdlp"                                │
│                         │                               │
│                         ▼                               │
│  2. youtube-transcript-api                             │
│     └─▶ With rate limit retry (tenacity)               │
│     └─▶ 3 attempts, exponential backoff (4-30s)        │
│     └─▶ Source: "api" or "proxy"                       │
│                         │                               │
│                         ▼                               │
│  3. Gemini Flash (if GEMINI_API_KEY set)               │
│     └─▶ Download raw audio via yt-dlp (no conversion)  │
│     └─▶ Upload to Gemini File API (google-genai SDK)   │
│     └─▶ Transcribe with generate_content()             │
│     └─▶ ~30-90s, ~$0.04 per 26-min video              │
│     └─▶ Music-aware prompt when category=music         │
│     └─▶ Source: "gemini"                               │
│                         │                               │
│                         ▼                               │
│  4. OpenAI Whisper (if enabled)                        │
│     └─▶ Download audio + convert to MP3 (FFmpeg)       │
│     └─▶ Chunk large files (>24MB) with pydub           │
│     └─▶ Transcribe chunks concurrently (Semaphore,     │
│         WHISPER_CHUNK_CONCURRENCY=3), per-request       │
│         timeout WHISPER_CLIENT_TIMEOUT_SECONDS=300s,    │
│         WHISPER_MAX_RETRIES=1                          │
│     └─▶ Deadline + partial-return: chunk 0 always runs; │
│         later chunks skip past the soft deadline and a  │
│         PARTIAL transcript is returned instead of       │
│         failing the whole video                         │
│     └─▶ ~5-15min, ~$0.16 per 26-min video             │
│     └─▶ Max WHISPER_MAX_DURATION_MINUTES (default 600)  │
│     └─▶ Source: "whisper"                              │
│                         │                               │
│                         ▼                               │
│  5. Metadata Fallback (music category only)            │
│     └─▶ Builds text from title, channel, description,  │
│         tags, and chapter titles                        │
│     └─▶ Only triggers when category == "music"         │
│     └─▶ Non-music videos still raise TranscriptError   │
│     └─▶ Skips sponsor filtering + AI chapter detection │
│     └─▶ Source: "metadata"                             │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

**Audio-fallback gate.** A caption-fetch failure (steps 1–2) drops to audio
transcription for *any* error **except** genuine video-access failures
(`VIDEO_UNAVAILABLE`, `VIDEO_RESTRICTED`, `LIVE_STREAM`), where the audio
download would also fail. In particular a YouTube **429 (`RATE_LIMITED`)** on the
caption API still attempts audio — the audio download is a separate, un-throttled
endpoint. Audio fallback is additionally gated by `WHISPER_ENABLED` and
`WHISPER_MAX_DURATION_MINUTES` (the gate lives in
`transcript_fetcher.py`, constant `_NO_AUDIO_FALLBACK`).

**Caption 429 handling.** A 429 is IP-scoped, so the response depends on the
proxy setup (`services/cache/caption_negative_cache.py`, Redis key
`vie:captions:429`, TTL `CAPTION_429_NEG_TTL_SECONDS` = 900s):

- *Direct or a single proxy exit* — the caption API retries 3× with backoff on
  the same IP; a persisting 429 (or a timedtext 429 from the metadata phase)
  writes the marker, and until it expires every run skips the caption API and
  goes straight to audio.
- *`YOUTUBE_PROXY_EXIT_COUNT` > 1* (Webshare sticky `USERNAME-N` exits) — the
  metadata-phase timedtext fetch and the caption API both move to the next
  exit on a 429 (`download_utils.ytdlp_proxy_exit_urls`, at most 3 exits, no
  backoff, one shared loop `download_utils.try_proxy_exits`). Only a 429 on
  every exit tried writes the marker; any other error ends the rotation
  without marking. The library's `RequestBlocked` (its 429 subclass
  `IpBlocked` plus the "not a bot" check) is recognised by type, since its
  message carries no "429" once a proxy config is attached.

**Blocked proxy exit (429 or bot check).** YouTube's "Sign in to confirm
you're not a bot" check is scoped to the exit IP like a 429 (2026-10-08: the
main Webshare exit was bot-checked on every video while other exits worked),
so with `YOUTUBE_PROXY_EXIT_COUNT` > 1 every proxied YouTube call moves to the
next exit on either (`download_utils.is_exit_blocked`): the metadata
`extract_info`, the timedtext fetch, the caption API, the low-res and 720p
yt-dlp downloads (`media/local_video.py`, within the same timeout), the audio
download and the playlist lookup (`video/playlist.py`, whose `ignoreerrors`
result is classified from the error line yt-dlp logs). Private, removed,
age-gated and 404 errors never rotate. When every tried exit is blocked the
call fails with its usual error (metadata: `VIDEO_UNAVAILABLE`). The exit that
last worked starts the next rotation in that process
(`download_utils.record_working_exit`), so a blocked exit costs one failed
attempt (~2.5 s) per process, not per call; logs name the working exit as
`session -N`, never the URL.

**Language detection.** Both the Gemini and Whisper paths keep the transcript
**verbatim in its source language** (no internal translation), which is what
powers the FE source-language toggle. Detection is text-first —
`detect_language_from_text()` (`langdetect`, now a hard dependency) with a
fallback to `detect_language_by_script()`. The script detector checks
hiragana/katakana **before** the shared CJK block so kanji-heavy Japanese is no
longer misdetected as Chinese. The detected language is carried on the
`NormalizedTranscript` so downstream phases consume one authoritative value
instead of re-deriving it.

### Configuration

```bash
# Gemini transcription (preferred audio fallback)
GEMINI_API_KEY=...      # Enables Gemini Flash transcription

# Whisper fallback settings
WHISPER_ENABLED=true
WHISPER_MAX_DURATION_MINUTES=600
OPENAI_API_KEY=sk-...                   # Required for Whisper
WHISPER_CLIENT_TIMEOUT_SECONDS=300.0    # Per-chunk HTTP timeout
WHISPER_MAX_RETRIES=1                   # Retries per chunk
WHISPER_CHUNK_CONCURRENCY=3             # Concurrent chunk transcription
```

---

## Chunked Extraction (Long Videos >30 min)

Videos longer than 30 minutes use a chapter-aware, batched extraction pipeline for better quality and reliability.

### Pipeline Flow

```
SHORT (<30 min):   Current pipeline unchanged (single/overflow extraction)
MEDIUM (30-120m):  Chapter split → 1-2 batch extraction calls → merge
LONG (2+ hours):   Chapter split → 2-5 batch calls → hierarchical synthesis → merge
```

### Chapter Splitting (`transcript_chunker.py`)

Fallback chain for splitting transcripts into chapters:
1. **YouTube chapters** — highest quality, from yt-dlp `video_data.chapters`
2. **AI chapter detection** — fast model LLM call with `chapter_detect.txt` prompt
3. **Time-based splitting** — ~5-minute segments when no chapters available
4. **Single chunk fallback** — treat entire transcript as one chunk (current behavior)

### Batched Extraction (`extractor.py`)

- Groups chapters into batches of ≤50K tokens (`batch_chapters()`)
- Parallel extraction per batch with `asyncio.Semaphore(EXTRACTION_PARALLEL_BATCHES)` (default 6); `EXTRACTION_PARALLEL=false` runs one batch at a time
- Single-batch chunked input is force-split into `EXTRACTION_FORCE_SPLIT_CHUNKS` sub-batches (default 4) so parallelism still engages on long single-chapter videos; falls back to overflow only when the split also yields one chunk
- Force-split bypasses `batch_chapters()`: `_resolve_strategy` returns `one_chunk_per_batch=True` and `_chunked_extraction` builds `batches=[[c] for c in chunks]` so the sub-batches don't re-collapse under `MAX_TOKENS_PER_BATCH`
- **Batch-aware prompt** (Phase 6): each batch's prompt carries a `<batch_partial_context>` block built by `_build_batch_context(batch_idx, total_batches, batch, full_duration_seconds)`. The block tells the model it sees only a slice of the video, overrides the base prompt's "no empty arrays" / density rules for partial transcripts, and explicitly instructs `Return EMPTY arrays for fields not present in your segment — other batches cover them`. The placeholder `{batch_context}` lives inside the `<transcript>` block (after the cache-split marker), so per-batch context never invalidates the Anthropic prompt cache
- Per-batch progress streamed via `asyncio.Queue` → SSE `extraction_progress` with `batch`/`of`
- Rate-limit aware: batches whose LLM call raises `RateLimitError` / `ServiceUnavailableError` propagate up (via `call_llm_with_retry(propagate_rate_limit=True)`) and are queued for a sequential second pass with `_RATE_LIMIT_BACKOFF_SECONDS = 2.0`; sequential events carry `section="chunked-sequential"` so the UI can surface the fallback. Sequential retries inherit the same `batch_context` so the prompt stays consistent across attempts
- Chapter headers injected into transcript for better context

> **Phase 6 rationale (2026-05-14):** v6 verification on `K-mA3MZ_EzU` showed parallel batches scored 0.40 on the merged extraction (below `RETRY_SCORE_THRESHOLD=0.6`) because each batch saw a 1/4 transcript slice but was told via `<completeness>` to extract for the full 108-min video — so empty `steps[]` from a batch that genuinely had no steps in its slice looked like a coverage gap. The synthesis-fed retry then re-ran all 4 batches, doubling extraction cost ($0.41 → $0.92). The Phase 6 `<batch_partial_context>` block targets the root cause: the model is now explicitly told its input is partial and `DO NOT pad fields to meet "no empty array" or density quotas`, so the merged extraction reflects true field-presence and the retry only fires on genuine quality misses.

### Extraction Merger (`extraction_merger.py`)

Per-domain merge logic for combining batch results:
- **Identity-based dedup** — name, label, term (case-insensitive)
- **Ordered list re-numbering** — step, order, number fields
- **Longest scalar selection** — keeps richest description/text
- **Recursive dict merge** — deep merge of nested structures

### Hierarchical Synthesis

For videos with >5 chapters, synthesis uses chapter summaries + truncated extraction (6K chars) instead of raw extraction data.

### Fast Model Routing

Model routing by stage:
- Plan: Sonnet (primary model, 30s timeout, 2 retries) — replaces old Manifest + Triage
- Classifier: fast model (10s timeout, 1 retry)
- Synthesis: `use_fast_model=True` (30s timeout)
- Enrichment: `use_fast_model=True`
- Frame vision (`frame_analyzer.analyze_frames_with_vision`): **primary (Sonnet)**. Originally routed to fast in Phase 1B / P3, reverted 2026-05-14 after a 5-frame spot-check (`scripts/spotcheck_frame_vision.py`) found `openai/gpt-4o-mini` was only ~20% cheaper *and* hallucinated OCR on dense-text frames
- AI chapter detection (`transcript_chunker._detect_chapters_with_ai`): `use_fast_model=True`, scoped under its own `llm_feature_var.set("summarize:chapter_detect")` context so cost is attributed correctly in `llm_usage` (was previously absorbed by the outer `summarize:extraction` tag)
- Extraction runs on `LLM_EXTRACTION_MODEL` (blank → primary); the fast-first path and the synthesis-fed retry were removed in pipeline-1min 1c.3/1c.5.

### Prompt Safety Net (`llm_retry.py`)

Hard character limit per model before every LLM call:
- Anthropic: 600K chars, OpenAI: 380K chars, Gemini: 3M chars
- Warning log when truncation triggers
- Prevents context overflow even without chunking

### Configuration (`config.py`)

| Setting | Default | Purpose |
|---------|---------|---------|
| `CHUNKED_EXTRACTION_THRESHOLD` | 900 (15 min) | Duration threshold for chunked path |
| `MAX_TOKENS_PER_BATCH` | 50000 | Max tokens per extraction batch |
| `MAX_MINUTES_PER_BATCH` | 40 | Max minutes of video per extraction batch |
| `EXTRACTION_PARALLEL` | True | Chunked batches run in parallel; False = one batch at a time |
| `EXTRACTION_PARALLEL_BATCHES` | 6 | Semaphore bound for parallel chunked batches |
| `EXTRACTION_FORCE_SPLIT_CHUNKS` | 4 | Sub-batches when chunked input collapses to 1 batch |

### Key Files

| File | Purpose |
|------|---------|
| `src/services/transcription/transcript_chunker.py` | Chapter splitting with fallback chain |
| `src/services/pipeline/extraction_merger.py` | Per-domain merge + dedup |
| `src/prompts/chapter_detect.txt` | AI chapter detection prompt |
| `src/utils/llm_retry.py` | Fast model routing + prompt truncation |

---

## S3 Media Storage

All media (transcripts, frames, future audio) is stored in a unified S3 bucket (`vie-transcripts`).

### Architecture

```
vie-transcripts (S3 bucket)
└── videos/{youtube_id}/
    ├── transcript.json       ← processed transcript
    ├── scenes-v3/
    │   └── scene_{idx}.jpg   ← selected scene frames (720p, SCENE_S3_PREFIX-versioned)
    ├── scenes-v2/, scenes/
    │   └── scene_{idx}.jpg   ← legacy prefixes: pre-adaptive-vision (v2) and
    │                           pre-hires low-res (kept for old summaries)
    └── frames/
        └── {timestamp}.jpg   ← on-demand single-frame extraction (frame_extractor.py)
```

```
┌─────────────────────────────────────────────────────────┐
│                 S3 MEDIA STORAGE                         │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  1. Store Raw Transcript                                │
│     └─▶ S3 key: "videos/{youtubeId}/transcript.json"   │
│     └─▶ Legacy fallback: "transcripts/{youtubeId}.json"│
│     └─▶ Contains: segments, source, language, fetchedAt│
│                                                         │
│  2. Upload Video Frames                                 │
│     └─▶ Scene frames: "videos/{id}/scenes-v3/           │
│         scene_{idx}.jpg" (SCENE_S3_PREFIX-versioned)    │
│     └─▶ On-demand: "videos/{youtubeId}/frames/{ts}.jpg" │
│     └─▶ Parallel upload via asyncio.gather              │
│     └─▶ S3 exists check skips duplicates                │
│     └─▶ Blocks store s3_key (permanent) in MongoDB     │
│                                                         │
│  3. Serve via Presigned URLs                            │
│     └─▶ Generated at response time (sync, local signing)│
│     └─▶ Default expiry: 6h (S3_PRESIGNED_URL_EXPIRY),  │
│         aligned with api FRAME_URL_TTL_SECONDS=21600    │
│     └─▶ Cached results refresh URLs before emitting    │
│                                                         │
│  4. Track Generation Metadata                           │
│     └─▶ model: LLM used for summarization              │
│     └─▶ promptVersion: for tracking prompt changes     │
│     └─▶ generatedAt: ISO timestamp                     │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

### Design Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| Single bucket | `vie-transcripts` for all media | Simpler ops, per-video folder isolation |
| Presigned URLs | All access via signed URLs | Security: private bucket, time-limited access |
| `s3_key` in MongoDB | Store key, not URL | URLs change (expiry), key is permanent |
| Sync presigning | No async for URL generation | Local crypto operation (HMAC-SHA256), no network call |
| Lazy S3 initialization | aioboto3 imported on first use | Container starts without S3 dependency |
| Graceful degradation | S3 failure doesn't block summarization | Core functionality works without S3 |

### Migration Scripts

**Transcript key migration** (legacy → new path):

```bash
# Dry run (preview what would be migrated)
python scripts/migrate-s3-keys.py --dry-run

# Run migration
python scripts/migrate-s3-keys.py --batch-size 10

# Migrate and delete old keys
python scripts/migrate-s3-keys.py --delete-old
```

**Backfill raw transcripts** for existing videos:

```bash
docker exec vie-summarizer python /app/scripts/backfill-transcripts.py --dry-run
docker exec vie-summarizer python /app/scripts/backfill-transcripts.py --batch-size 100
```

---

## Dockerfile

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

# Run FastAPI server
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

## Commands

```bash
# Run server
uvicorn src.main:app --host 0.0.0.0 --port 8000

# Development with auto-reload
uvicorn src.main:app --reload --port 8000

# Run tests
pip install -e ".[dev]"
pytest
```

---

## API Reference

### POST /summarize

Trigger video summarization (async).

**Request:**
```json
{
  "videoSummaryId": "507f1f77bcf86cd799439011",
  "youtubeId": "dQw4w9WgXcQ",
  "url": "https://youtube.com/watch?v=dQw4w9WgXcQ",
  "userId": "507f1f77bcf86cd799439012"
}
```

**Response (202 Accepted):**
```json
{
  "status": "accepted",
  "videoSummaryId": "507f1f77bcf86cd799439011"
}
```

### POST /playlist/extract

Extract playlist metadata using yt-dlp (fast, no video download).

**Request:**
```json
{
  "playlist_id": "PLxxx",
  "max_videos": 100
}
```

**Response:**
```json
{
  "playlist_id": "PLxxx",
  "title": "React Tutorial Series",
  "channel": "Fireship",
  "thumbnail_url": "https://img.youtube.com/...",
  "total_videos": 15,
  "videos": [
    {
      "video_id": "dQw4w9WgXcQ",
      "title": "React Hooks",
      "position": 0,
      "duration": 1200,
      "thumbnail_url": "https://img.youtube.com/..."
    }
  ]
}
```

**Error Codes:**
- `404`: Playlist not found or private
- `500`: yt-dlp extraction failed

### POST /regenerate/{video_summary_id}

Trigger regeneration of an existing video summary.

**Request:**
```json
{
  "force": false  // Optional: set true to re-fetch from YouTube if S3 unavailable
}
```

**Response (200 OK):**
```json
{
  "status": "ready",
  "video_summary_id": "507f1f77bcf86cd799439011",
  "message": "Video summary ready for regeneration. Connect to streaming endpoint to process.",
  "has_raw_transcript": true,
  "generation": {
    "model": "anthropic/claude-sonnet-4-6",
    "promptVersion": "v1.0",
    "generatedAt": "2026-02-05T10:30:00Z"
  }
}
```

**Error Codes:**
- `400`: Invalid video summary ID format
- `404`: Video summary not found

### POST /override/{video_summary_id}

Override detected category during active pipeline processing. Affects remaining chapters only.

**Request:**
```json
{
  "category": "fitness"
}
```

**Response (200):**
```json
{
  "category": "fitness",
  "outputType": "workout",
  "outputTypeLabel": "Workout Plan",
  "persona": "fitness"
}
```

**Error Codes:**
- `422`: Invalid category (returns list of valid categories)
- `503`: Override capacity reached

### GET /health

Health check endpoint.

**Response:**
```json
{
  "status": "healthy",
  "service": "summarizer",
  "model": "anthropic/claude-sonnet-4-6",
  "database": "connected",
  "s3": "healthy"
}
```
