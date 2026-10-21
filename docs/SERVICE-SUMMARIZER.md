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
| aiobotocore | Async S3 client (transcripts + frame storage); boto3 signs presigned URLs |
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
    │   ├── llm.py                # LLMService (call_llm + call_llm_fast, each under call_deadline)
    │   ├── llm_provider.py       # LiteLLM multi-provider abstraction; a refusal → one re-send on PREVIOUS_MODEL
    │   ├── llm_model_rules.py    # Per-model request rules: thinking off + effort, sampling params, no prefill
    │   ├── llm_call.py           # ProviderCall / ModelAnswer, call_deadline, how a refusal reaches the caller
    │   ├── usage_tracker.py      # LLM usage tracking
    │   ├── override_state.py     # In-memory override state
    │   ├── video_purge.py        # Qdrant + S3 prefix + Redis purge for one video (per-store warnings)
    │   ├── status_callback.py    # Status callback
    │   │
    │   ├── pipeline/             # Plan-based summarization pipeline
    │   │   ├── tier_probe.py         # Tier probe: domain+format+has_visual_demo+transcript_usable at transcript-ready (Haiku, temp 0)
    │   │   ├── plan.py / plan_prompt.py # Plan (Sonnet 5.5, effort medium): full marked transcript → tabs + briefs,
    │   │   │                         #   evidence, terms (+ _render_playbook, _enforce_domain_policy)
    │   │   ├── memory.py             # Memory (LLM_MEMORY_MODEL) ∥ plan: outline + establishes lines, evidence, tldr (the thesis), 3/5/7 stamped takeaways
    │   │   ├── plan_reconcile.py     # Plan-parse guard: unregistered path, inactive domain; a learning.keyIdeas tab switches learning on
    │   │   ├── reconcile.py          # Plan-done reconcile: doubly-false view, step 1, screen-code check
    │   │   ├── reconcile_pass.py     # Reconcile steps 1b–5 (primary moment source, backfill, quiz, dedupe, info_grid fold, 3–6) + row writer
    │   │   ├── video_memory.py       # <video_memory> block (plan + memory) for every {video_context} slot
    │   │   ├── visual_annotations.py # Frame captions + OCR → <visual_annotations> block (never clean_text)
    │   │   ├── triage.py             # Triage validation/fallback (TriageResult model + tab validation)
    │   │   ├── prompt_builder.py     # Prompt loading (registry-first) + key-frame block
    │   │   ├── prompt_registry.py    # Registry fetch, placeholder-drift guard, trace link
    │   │   ├── extraction_prompt.py  # base_extraction = system rules / cached [video+transcript+memory] / job
    │   │   ├── field_requests.py     # Split extraction: the fields the reconciled tabs read (+ siblings, side reads, scalars)
    │   │   ├── field_groups.py       # ... cut into ≤ maxTextGroups text groups (text1 first) + one visual group
    │   │   ├── extraction_chunks.py  # ... over the run's chunks (one, or chapter batches for long videos)
    │   │   ├── extraction_group_prompt.py # ... each call's three blocks: 1h rules+schemas+example / 5m video+chunk+memory / job
    │   │   ├── extraction_groups.py  # ... fired warm-first under the run's semaphore; one more call for a lost answer
    │   │   ├── group_merge.py        # ... per-group validation + key filter, the deterministic merge
    │   │   ├── group_failures.py     # ... what a failed group costs (droppedTabs, degraded)
    │   │   ├── schema_fields.py      # Schema skeletons + "emit ONLY these keys" lines for the group job
    │   │   ├── tab_ledger.py         # The tab_ready emission contract (position, final, provisional, replaces)
    │   │   ├── extractor.py          # Today's single call (EXTRACTION_PARALLEL=false): single/overflow/chunked
    │   │   ├── extraction_quality.py # Populated-dataSource score — a logged metric, no retry
    │   │   ├── extraction_merger.py  # Per-domain merge + dedup for chunked extraction (EXTRACTION_PARALLEL=false)
    │   │   ├── enrichment.py         # The quiz only (quizEnrichment domains, demand-gated by the plan; 3/5/8 questions by length, each stamped)
    │   │   ├── synthesis.py          # masterSummary + seoDescription (hero from memory; fast tier) — now only when the hero rewrite is off or gave none
    │   │   ├── hero_rewrite.py       # The hero rewrite (HERO_REWRITE_ENABLED): one cache-reading LLM_EXTRACTION_MODEL call replaces synthesis; hero_job.py builds its request (cached blocks, job text, the evidence), hero_validate.py checks its answer (hero_text.py holds the prose readers), hero_compact.py renders the extraction it reads
    │   │   ├── quiz_v2.py            # Quiz v2 (QUIZ_V2_ENABLED): LLM_MEMORY_MODEL (Haiku 5.5) from the transcript + key ideas; every question's quote checked
    │   │   ├── quote_verify.py       # Is a quote in the transcript? The extraction pre-pass + the run's index (verified / unverified / translated); re-exports quote_index
    │   │   ├── quote_index.py        # The quote matcher: QuoteIndex (normalized exact-first, else fuzzy; a "…"-cut quote is fragments in order within 40 words and 60 s)
    │   │   ├── pipeline_timing.py    # pipeline.timing recorder v4 (phases, LLM calls, failures incl. refusals, downloads, counts)
    │   │   ├── translation.py        # Source-language translation (flat-list batched)
    │   │   ├── translation_flat.py   # Flat-list translate call, batch sizing, mirror check (shared with RAG English)
    │   │   ├── faithfulness.py       # Faithfulness sampling (the extraction sample)
    │   │   ├── faithfulness_context.py    # Claim-localized transcript windows for the judge
    │   │   ├── faithfulness_hero_quiz.py  # The judge's hero + quiz sample (faithfulness_hero / faithfulness_quiz)
    │   │   ├── assembly/             # Assembly package (extraction → component props)
    │   │   │   ├── core.py               # assemble_response + per-tab drop accounting
    │   │   │   ├── registry.py           # ASSEMBLER_REGISTRY (29 assemblers) + component inference
    │   │   │   ├── assemblers_*.py       # primary/learning/overhaul/secondary assembler groups
    │   │   │   ├── promotion.py          # Degrade-never-drop demote ladder (degradedFrom; tier_list → spot_explorer → info_grid → display_section) + info_grid → spot_explorer promotion (a row's evidence is a card quote only when it is the item's checked quote)
    │   │   │   ├── moment_frame_fill.py  # Exact-timestamp frame fill for frameless moments
    │   │   │   ├── attachments.py        # Secondary attachments (excluded_kinds dedup)
    │   │   │   ├── normalizers.py, density.py, cross_tab.py, text_utils.py
    │   │   │   ├── quiz_stamps.py, tab_references.py # Quiz times in seconds; no hero / goal sentence names a tab that does not ship (summarizer-workflow.md §Assembly notes)
    │   │   │   ├── moment_sources.py, steps_timeline.py, info_grid_fold.py, quote_spots.py, ingredients.py # R7 / D6 / R6 / quote cards / ingredient rows (summarizer-workflow.md §Assembly notes)
    │   │   ├── phases/               # Phase runners (metadata, text, transcript, transcript_fallback,
    │   │   │                         #   probe, triage=plan, memory, moments, frames, frames_done,
    │   │   │                         #   split_extraction, group_emission, extraction, enrichment,
    │   │   │                         #   late_quiz, synthesis, assembly, qdrant_index, translation)
    │   │   ├── post_processor.py     # Tab cleanup, celebrations, count validation (tag-aware)
    │   │   └── pipeline_helpers.py   # SSE events, timer, data classes, run_task_with_heartbeat
    │   │
    │   ├── transcription/        # Transcript fetching & storage
    │   │   ├── transcript.py         # youtube-transcript-api layer + transcript cleaning & formatting
    │   │   ├── transcript_fetcher.py # Multi-source fallback chain
    │   │   ├── cached_transcript.py  # Chain step 0: the S3 blob (stale when it holds another caption track)
    │   │   ├── transcript_chunker.py # Chapter-aware transcript splitting (chain: see Chunked Extraction)
    │   │   ├── transcript_store.py   # S3 transcript persistence
    │   │   ├── gemini_transcriber.py # Gemini Flash transcription
    │   │   └── whisper_transcriber.py # Whisper fallback
    │   │
    │   ├── transcript/           # cleaner.py (basic cleaning incl. English fillers), render.py ([m:ss] markers)
    │   │
    │   ├── media/                # Frame extraction, vision & S3 storage
    │   │   ├── scene_extractor.py    # Scene keyframe extraction (yt-dlp + FFmpeg)
    │   │   ├── frame_manifest.py     # Manifest-v2 frame cache (S3 sidecar: timestamps, hiresCount, vision descriptions)
    │   │   ├── frame_scorer.py       # Frame scoring (visual, face, skin, center-detail, text, uniqueness)
    │   │   ├── frame_analyzer.py     # Vision (LLM_VISION_MODEL, Sonnet 5.5) in ≤5 parallel batches of ≤8 frames (vision.txt)
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
    │       ├── caption_picker.py     # Caption-track choice: spoken language first, then manual over auto
    │       ├── sponsorblock.py       # Sponsor segment detection
    │       ├── description_analyzer.py # Description analysis
    │       └── playlist.py           # Playlist extraction (yt-dlp)
    │
    ├── repositories/
    │   ├── base.py               # Repository protocols
    │   └── mongodb_repository.py # MongoDB implementation
    │
    ├── prompts/
    │   ├── plan.txt              # Plan prompt (tabs + briefs, evidence, terms, playbook, probe hint)
    │   ├── memory.txt            # Memory prompt (outline + establishes, evidence, thesis tldr, {takeaway_count} stamped takeaways)
    │   ├── component_toolkit.txt # Component descriptions + datasource paths (injected into plan/triage). Density table is generated from domains.json `densityGates` via `{density_gates}` placeholder
    │   ├── base_extraction.txt   # Extraction template: <rules> / <video> (cached) / <your_job>
    │   ├── extraction_group_job.txt # Split extraction (EXTRACTION_PARALLEL): one field group's <your_job> (emit-only keys + shape, tabs it serves; visual group: annotations + key frames)
    │   ├── extraction_group_rules.txt # Split extraction: completeness / argument / no-invented-number rules, appended to <quality_rules> in block 1; <argument_example>s for the key-ideas group's block 3
    │   ├── tier_probe.txt        # Tier-probe prompt (14 domains + 19 formats incl. unboxing and talk, has_visual_demo, transcript_usable — domains.json is the source)
    │   ├── chapter_detect.txt    # AI chapter detection prompt (fast model)
    │   ├── quality_rules.txt     # JSON extraction quality rules
    │   ├── enrich_quiz.txt       # The quiz prompt when QUIZ_V2_ENABLED=false (flavor per quizEnrichment domain; {question_count}, {video_length}, <hero>)
    │   ├── quiz_v2.txt           # Quiz v2 rules (evidence quote + timestamp as written, m:ss, per question; the task / hero / key ideas / transcript are bound around it, no placeholders)
    │   ├── hero_rewrite.txt      # Hero rewrite job ({takeaway_count}, {duration}, {tab_labels}, {stamp_rule}, {video}, {extracted})
    │   ├── vision.txt            # Frame-vision prompt (registry summarizer:vision)
    │   ├── description_analysis.txt, translate_flat.txt
    │   ├── synthesis.txt         # Synthesis prompt (masterSummary + seo; hero fields only as fallback) — used when the hero rewrite is off or gave none
    │   ├── examples/             # One per domain that has one (no learning fallback)
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
    │   ├── domain_config.py      # Reads domains.json (Docker mount or local fallback; cached per process). Also renders the planner's `{valid_components}` list + `{density_gates}` table from config
    │   └── assembly_policy.py    # Assembly's registry policy: momentCapsByDuration, assemblyPolicy, measureUnits, injectedTabs
    │
    └── models/
        ├── schemas.py            # Pydantic models
        ├── domain_types.py       # Domain data models (Food, Travel, Music, etc.)
        ├── pipeline_types.py     # Pipeline models (PlanResult, TabBrief, SynthesisResult, EnrichmentData, etc.)
        ├── probe_types.py, memory_types.py # Tier-probe answer; memory input/result (outline, evidence, hero)
        ├── idea_types.py         # Argued items: LearningKeyIdea, PodcastQA, TechTopic, the shared Claim
        ├── domain_validation.py  # Validation report (_dropped/_defaulted/_misTargeted) helpers
        ├── sse_events.py         # Pydantic contract for every SSE event (validated on emission)
        └── vie_response_v2.py    # VIEResponse v2 models (TabEntry, CrossTabLink, etc.)
```

---

## Environment Variables

```bash
# Database
MONGODB_URI=mongodb://vie-mongodb:27017/video-insight-engine

# LLM Provider Configuration
LLM_PROVIDER=anthropic          # anthropic, openai, or gemini
LLM_FAST_PROVIDER=              # Optional: separate provider for the fast tier (prod + .env.example: openai → gpt-4o-mini; blank → Haiku 4.5)
LLM_FALLBACK_PROVIDER=          # Optional: cross-provider fallback after one same-provider retry (primary-model calls; openai → gpt-4o)
LLM_MODEL=                      # Optional: override the primary model (blank = MODEL_MAP default, anthropic/claude-sonnet-5-5 — in this service only the plan + /health read it; vie-assistant reads it too)
LLM_FAST_MODEL=                 # Optional: override the fast-tier model (synthesis, chapter detect, blank stage settings; vie-assistant's quiz tool too)

# Per-stage models (pipeline-1min 4.0d A/B, gates/g4-model-ab.md). config.py and both
# compose anchors carry the same literal default, so a blank .env line keeps it.
LLM_CLASSIFIER_MODEL=anthropic/claude-haiku-4-5-20251001   # Tier probe (D21)
LLM_EXTRACTION_MODEL=anthropic/claude-sonnet-5-5           # Extraction (groups + single call), effort low
LLM_MEMORY_MODEL=anthropic/claude-haiku-5-5                # Memory; blank = LLM_EXTRACTION_MODEL (config only)
LLM_JUDGE_MODEL=openai/gpt-6-luna                          # Faithfulness judge (reasoning off); blank = the fast tier (config only)
LLM_DESCRIPTION_MODEL=openai/gpt-6-luna                    # Description analysis (reasoning off)
LLM_TRANSLATION_MODEL=openai/gpt-6-luna                    # Output, title and RAG transcript translation (reasoning off)
LLM_ENRICHMENT_MODEL=openai/gpt-4o-mini                    # The quiz when QUIZ_V2_ENABLED=false (quiz v2 runs on LLM_MEMORY_MODEL, no key of its own)
LLM_SYNTHESIS_MODEL=                                       # Blank = the fast tier; the synthesis call runs only when the hero rewrite is off or gave none
# LLM_CHAPTER_DETECT_MODEL exists in config.py (blank = fast tier) but no compose anchor passes it
# A refused answer is asked once of config.PREVIOUS_MODEL (Sonnet 5.5 → Sonnet 4.6,
# Haiku 5.5 → Haiku 4.5, gpt-6-luna → gpt-4o-mini) — not an env setting.

# Provider API Keys (set for providers you use)
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...           # Required: the openai/* stage defaults (description, quiz, translation) + an openai fast tier; also Whisper
GEMINI_API_KEY=                 # Enables Gemini audio transcription (faster than Whisper)
GOOGLE_API_KEY=                 # Required if using Gemini as LLM provider

# S3 Media Storage (transcripts, frames, audio)
S3_BUCKET=vie-transcripts       # Dev default (both composes fall back to it); prod sets its own bucket (DEPLOY.md)
S3_PRESIGNED_URL_EXPIRY=21600   # Presigned URL validity (seconds); >= api FRAME_URL_TTL_SECONDS
AWS_REGION=us-east-1            # Dev default; prod compose REQUIRES it = the bucket's region (frame URLs are presigned for it)
AWS_ACCESS_KEY_ID=your-key      # AWS credentials
AWS_SECRET_ACCESS_KEY=your-secret

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
YOUTUBE_PROXY_EXIT_COUNT=1             # Sticky exits (USERNAME-1…N) a 429 or bot check rotates through on every YouTube call (metadata + captions: every exit, no new one after 15 s; downloads: max 3); 1 = no rotation
YTDLP_PLAYER_CLIENTS=android           # yt-dlp player clients for audio downloads + the video downloads' retry; empty = yt-dlp defaults
YTDLP_HIRES_PLAYER_CLIENTS=web_embedded,android  # both video downloads try these first (android has no video-only renditions); retries with the line above
FRAME_TIER_ENABLED=true                # Adaptive visual tiers (HIGH: overselect + vision reselect before hires)
FRAME_VISION_ENABLED=true              # Frame descriptions by the vision model; false = OCR only
FRAME_VISION_PARALLEL=true             # Vision in parallel batches; false = one call with every frame
LLM_VISION_MODEL=anthropic/claude-sonnet-5-5  # 4.0d pick (both composes default to it); empty outside compose = the primary model

# Output-quality phase 2 rollback levers (config.py + both compose anchors carry the same literal)
HERO_REWRITE_ENABLED=true              # true: one hero_rewrite call on LLM_EXTRACTION_MODEL (reads the extraction's prompt cache) replaces the synthesis call; false: memory hero + synthesis
QUIZ_V2_ENABLED=true                   # true: quiz v2 on LLM_MEMORY_MODEL (timestamp + checked evidence quote per question); false: the enrich_quiz call on LLM_ENRICHMENT_MODEL

# Extraction concurrency + chunking
EXTRACTION_PARALLEL=true               # Field groups (text at plan-done, visual at frames-done); false = today's single call / chunked batches one at a time (extraction only: reconcile, evidence ranges, toolkit markers, PIPELINE_VERSION stay)
EXTRACTION_PARALLEL_BATCHES=6          # Max extraction calls in flight per run (× WORKER_CONCURRENCY per worker); drop to 4 if pipeline.timing records 429s
CHUNKED_EXTRACTION_THRESHOLD=900       # (EXTRACTION_PARALLEL=false only) Videos longer than this (s) may split the transcript into chapter batches
MAX_TOKENS_PER_BATCH=50000             # One batch's estimated transcript tokens
MAX_MINUTES_PER_BATCH=40               # One batch's span of video
EXTRACTION_FORCE_SPLIT_CHUNKS=4        # (EXTRACTION_PARALLEL=false only) Sub-batches when a long video's chunked transcript collapses to one batch

# Transcript text
TRANSCRIPT_CLEANING_ENABLED=false      # spaCy + TF-IDF pass over ctx.clean_text (Qdrant, faithfulness, S3); no prompt reads it
TRANSCRIPT_CLEANING_TIMEOUT=30.0       # Budget for that pass (first call per process pays the spaCy cold start)

# Prompts
PROMPT_SOURCE=registry                 # registry (Langfuse label wins) | disk (local .txt re-read per call, dev-only; ignored unless ENVIRONMENT is a dev name)

# SSE streaming
SSE_HEARTBEAT_SECONDS=12.0             # Keepalive cadence during silent phases — must stay under the gateway's 300s undici timeout

LOG_LEVEL=INFO
LOG_FORMAT=console              # console or json
```

---

## Vector Store Indexing (transcript + output)

After every successful pipeline run, three background tasks index the video's
content into Qdrant for the assistant to retrieve (none for an eval-user run,
`evalRun` — D25):

| Task | Source field | Phase | Content |
|---|---|---|---|
| `store_transcript_chunks` | `source="transcript"` | assembly | `chunk_transcript()` output (English text; original-language text retained in `text_original` for non-English videos) |
| `store_default_output_chunks` | `source="default_output"` | assembly | Per-component chunking of the assembled tabs via `output_chunker.py` — emits one chunk per natural retrieval unit (one `keyTakeaways[i]`, one quiz question, one comparison row, etc.) |
| `store_visual_chunks` | `source="visual"` | assembly | The rendered `<visual_annotations>` entries (frame captions + OCR, `[m:ss]`), packed whole into chunks with first/last-frame timestamps — the transcript points are speech only. Embeds before it deletes the previous points |

Generation is English-canonical, so the assembled tabs are English for every
video and are indexed at assembly; translation only adds the source-language
artifact (the embedding model is English-trained).

The transcript and output paths pre-delete by `(video_id, source)` before upsert (and pre-delete
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

A plan-first pipeline (since v9; `PIPELINE_VERSION` v10): description analysis, tier probe, plan ∥ memory, the extraction field groups (1–5 text groups + at most one visual group, per chunk — a long video has several chunks), the hero rewrite (it replaces the synthesis call, which stays as its fallback), the quiz when demanded (quiz v2), plus 1-5 vision batches and the sampled faithfulness judge (its extraction sample, plus a hero and a quiz call after assembly); non-English videos add batched translation calls. With `EXTRACTION_PARALLEL=false` extraction is one call (or one per chapter batch). Output-quality phase 1 (registry, prompts, extraction schemas, assembly) adds no call and no setting and ships under v10; phase 2 swaps two calls behind two settings (`HERO_REWRITE_ENABLED`, `QUIZ_V2_ENABLED`) and adds the judge's two, also under v10 — the task's one bump, v10 → v11, comes at its close-out ([IDEMPOTENCY.md](./IDEMPOTENCY.md#when-to-bump-pipeline_version)); what each prompt asks for: [summarizer-workflow.md §6](./summarizer-workflow.md#what-each-prompt-asks-for-output-quality). Phase order lives in `routes/pipeline_orchestration.py`; the call-order walkthrough with file anchors is [summarizer-workflow.md](./summarizer-workflow.md):

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
    │   one ≤720p download (media/hires_prefetch.py, ctx.lowres_video / ctx.hires_video).
    │   A rejected video starts nothing.

 3. TEXT BRANCH ∥ FRAMES ∥ FIELD GROUPS (phase 2, parallel via asyncio.Queue; the tier probe task
    │   starts alongside; the field-group extraction (5) is the third member unless
    │   EXTRACTION_PARALLEL=false; a member that raises cancels the others — frames never raises)
    └─▶ transcript waits for the caption fetch; frames for the low-res file. The description
    │   analysis is NOT a member: extraction must not wait for its LLM call (30 s total cap);
    │   its readers (chunked chapter split, assembly) await it for ≤ 5 s and assembly emits its SSE
    ┌─▶ TRANSCRIPT (Multi-Source Fallback Chain)
    │   └─▶ 0. S3 cached transcript (avoids all YouTube calls; skipped as stale when it
    │        holds another caption track than this run's pick)
    │   └─▶ 1. yt-dlp subtitles (track picked in metadata: spoken language first, then
    │        manual over auto — caption_picker.py)
    │   └─▶ 2. youtube-transcript-api (spoken language first; with rate limit retry)
    │   └─▶ 3. OpenAI Whisper (audio fallback, ~5-15min, ~$0.16/26min) — first, because it
    │        reports the language natively
    │   └─▶ 4. Gemini Flash (audio transcription, ~30-90s, ~$0.04/26min) — when Whisper is
    │        disabled or fails
    │   └─▶ (audio paths 3+4 bypass LiteLLM → emit their own llm_usage cost row +
    │        Langfuse generation via transcription/usage.py; see llm-cost-model.md)
    │   └─▶ SSE: transcript_ready event → the text branch continues with step 4
    │       while frames still run
    │
    └─▶ FRAMES (smart frame selection, two-pass, non-critical)
        └─▶ S3 cache check (media/frame_manifest.py) — MANIFEST-only: `videos/{id}/{SCENE_S3_PREFIX}/manifest.json`
        │   (v2) carries per-frame timestamps, galleryIndices, hiresCount and the
        │   persisted visionDescriptions. A hit skips extraction AND restores the
        │   vision descriptions (cached frames have no local file, so vision can't
        │   re-run); `hiresCount == 0` (every hi-res seek failed, or a deferred hi-res
        │   pass that never landed) is treated as a MISS so a low-res run self-heals
        │   on the next reprocess.
        │   (prefix is versioned via SCENE_S3_PREFIX, default "scenes-v4" — bump to
        │    invalidate after quality changes; "scenes" holds pre-hires low-res
        │    frames, "scenes-v2" pre-adaptive-vision frames. NOTE: scene_NNNN keys
        │    are overwritten in place per run.)
        └─▶ Per-video asyncio.Lock prevents duplicate concurrent extractions
        └─▶ Adaptive visual tier (media/visual_tier.py, domains.json visualCriticality,
        │   FRAME_TIER_ENABLED), decided once at Step 6b of scene extraction from the tier probe
        │   (waits ≤ 3 s for it, else the metadata rule; pipeline.timing step frames.tier_wait):
        │   HIGH over-selects FRAME_OVERSELECT_COUNT candidates and
        │   vision-describes them BEFORE hires refinement so subject-matter frames beat
        │   presenter shots (floor FRAME_RESELECT_FLOOR); LOW skips vision; STANDARD = top-8.
        └─▶ Pass 1 — detection: reads the run's 360p download (started by the metadata
        │   phase): the smallest ≥360p video-only rendition, H.264 first — or the 720p
        │   download's file when it lands first as the same ≤360p rendition
        │   (hires_prefetch.first_detection_file; pass 1 is then cancelled; a real
        │   720p file is read only when pass 1 fails — scene scoring on it costs
        │   several times more). Both video downloads try YTDLP_HIRES_PLAYER_CLIENTS first:
        │   android alone serves only the muxed 360p rendition (format 18, audio
        │   included, 2-4x the bytes, often bigger than the 720p file — g4 §8), and
        │   YouTube's SABR experiment sometimes drops web_embedded's renditions too, so
        │   both downloads can still be format 18. Audio downloads and the video
        │   downloads' retry use YTDLP_PLAYER_CLIENTS (default "android" — YouTube 403s
        │   the web client's download URLs from some environments; never mix in
        │   "default": merged format lists let bestvideo pick a 403ing web DASH format).
        │   Metadata/subtitle extraction deliberately does NOT use it (android lacks subs).
        │   Every download's pipeline.timing entry records bytes, wallMs and mbPerSec;
        │   the video downloads add the picked format + height, the clients, the proxy
        │   exit (n/m) and the error. A bot check, a 429 or a 403 on the video data
        │   moves the download to the next sticky exit (on prod 2026-10-14 exit -2 got
        │   the 403 for every client while exit -1 downloaded). A frame download that
        │   still fails — or a deferred hi-res pass the run gave up on — marks the run
        │   degraded (pipeline.timing degraded + degradedReasons
        │   "media_download_failed", and the row's degraded flag — the admin runs
        │   badge; audio downloads don't count: a failed one fails the transcript path
        │   itself). The run start and a Redis-served completion clear the previous
        │   run's flags. yt-dlp is pinned to a nightly (requirements.txt).
        └─▶ FFmpeg scene detection on local file (~10-15s, 20-50x realtime; frames capped at
        │   SCENE_DETECT_SCALE_WIDTH and never upscaled; quality via SCENE_JPEG_QUALITY).
        │   Zero-candidate ladder: threshold 0.3 → 0.15 → uniform seeks, ffmpeg rc checked
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
        │   Only a 720p file already on disk is read: while it still downloads, the
        │   360p detection frames are uploaded (manifest hiresCount 0) and vision reads
        │   them (vision + OCR at 360p is the trade-off) — frames-done never waits for
        │   the big file unless pass 1 failed — and a deferred pass
        │   (media/hires_upgrade.py, started after vision) seeks them in the 720p file
        │   once it lands and overwrites the same S3 objects (a 720p duplicate, or a
        │   seek that landed on the shot before the cut, keeps its 360p object), then
        │   sets the manifest's hiresCount. Assembly leaves the file open while it
        │   runs; the runner gives it ≤ 60 s after `done`, side by side with the RAG
        │   transcript wait (a failed run cancels it), then closes the file.
        │   pipeline.timing step frames.hires_upgrade.
        └─▶ Batch parallel S3 upload (only selected frames — not all detected) + manifest write
        └─▶ OCR + Vision LLM analysis run in parallel:
            ├─▶ OCR: Tesseract on text-heavy frames
            └─▶ Vision (media/frame_analyzer.py, prompt vision.txt): top FRAME_VISION_MAX_FRAMES (8)
                frames → LLM_VISION_MODEL (Sonnet 5.5) in parallel
                batches — ≤ 4 frames = one call, else ≤ 8 per call over ≥ 2 calls, ≤ 5 in flight
                (FRAME_VISION_PARALLEL=false → one call), frames strided by time across calls;
                output ceiling per frame type (250 tokens, 500 for text-heavy frames); per-call
                timeout = 2× expected wall within [30 s, FRAME_VISION_TIMEOUT]; one retry unless the
                call used its whole timeout or the retry would pass the stage deadline; a failed
                batch loses only its own frames. HIGH tier describes the over-selected candidates
                in the Step-6b reselect hook instead (no second pass). Descriptions are persisted
                into the manifest for future cache hits
        └─▶ SSE: frames event (selected frames only — timestamps, presigned URLs, OCR text)
        └─▶ Graceful degradation: failure returns empty result, pipeline continues

 4. TEXT BRANCH (continues after transcript_ready, while frames run)
    └─▶ Tier probe (pipeline/tier_probe.py, prompt tier_probe.txt; replaces the classifier):
    │   LLM_CLASSIFIER_MODEL (Haiku 4.5, D21), temperature 0, 80 tokens, 4 s, no retry;
    │   reads metadata + three short NATIVE transcript windows (nothing translates before the
    │   plan) → domain, format (19 incl. unboxing; talk / lecture / interview / podcast told apart
    │   by what the speaker does — makes a case, teaches, a host questions a guest, the regular
    │   hosts among themselves), has_visual_demo, transcript_usable (missing key = true).
    │   Started at transcript-ready; frames read it at Step 6b (tier), the
    │   plan for its playbook ("<domain>:<format>") and a hint line. Failure → None: metadata
    │   tier, no hint. Skipped when an admin override is active.
    └─▶ Garbage captions → audio (phases/transcript_fallback.py, 2.5): transcript_usable false
    │   (not a coherent transcript of speech — e.g. TNhaISOUy6Q's Polish ASR of English speech)
    │   on a caption transcript (ytdlp / api / proxy, or an S3 blob they produced), not music,
    │   an audio layer allowed (WHISPER_ENABLED / GEMINI_API_KEY, the duration cap) → the
    │   transcript phase re-runs on the audio layers only (Whisper → Gemini; same cleaning,
    │   SponsorBlock, language detection), pipeline.timing step transcript_audio, then a second
    │   probe on the new text; once per run. Audio fails → the caption transcript stays.
    │   Every unusable answer adds "<source>-unusable" to transcriptMeta.attempted; a run that
    │   goes on with one plans without a probe, and frames treat it as none
    └─▶ The marked transcript, rendered ONCE (`ctx.prompt_transcript`: `[m:ss]` markers,
    │   sponsor reads cut, source-language cleaning) — plan and memory read the same string,
    │   never frame annotations
    └─▶ Plan ∥ Memory (pipeline/plan.py + plan_prompt.py, pipeline/memory.py)
    │   └─▶ Plan: primary model (Sonnet 5.5) at effort medium (`PLAN_EFFORT`), the FULL
    │   │   marked transcript, 3,500 max tokens, 60 s, 1 retry, json_mode. The static half
    │   │   (everything above `<video>`: 16,290 tokens on Sonnet 4.6, gates/g3.md §Prod;
    │   │   ≈ 22.1k on Sonnet 5.5's tokenizer, gates/g4-model-ab.md) goes as user block 1
    │   │   with cache_control `{type: ephemeral, ttl: 1h}` (`PLAN_CACHE_TTL` in plan.py); the
    │   │   video half is uncached. Emits contentTags, modifiers, tabs with briefs ({what,
    │   │   where, expect}), evidence (has_* booleans that gate domain requirements) and
    │   │   terms; truncation is counted (counts.truncated). A talk, lecture, podcast or
    │   │   interview opens on spot_explorer over learning.keyIdeas (`<spoken_word>`, four
    │   │   spoken-word examples); `expect` is sized to the video's length (`<sizing>`,
    │   │   content-model §9); a tab goal names the viewer's outcome (`<goal_rule>`) — one
    │   │   that does not is logged at validation (`plan_prompt.goal_rule_violation`), the tab
    │   │   ships. A tab on learning.keyIdeas switches the learning domain on
    │   │   (`plan_reconcile.activate_argument_domains`, ≤ 3 content tags)
    │   └─▶ Memory: LLM_MEMORY_MODEL (Haiku 5.5), 25 s, ONE attempt, temperature 0 (dropped for Haiku 5.5),
    │   │   1,600 max tokens → outline (4-12 contiguous sections, each with an `establishes`
    │   │   line; dropped when it stops short of the duration), evidence, tldr (the thesis),
    │   │   takeaways (3 / 5 / 7 for ≤ 15 / ≤ 45 / longer minutes, each closing with its [m:ss]).
    │   │   No timed transcript → no call. Its tldr + takeaways go out at once as the
    │   │   first synthesis_complete (the hero — the preview: with HERO_REWRITE_ENABLED the hero
    │   │   rewrite replaces it in the late group). Failure → None; every reader has a fallback
    │   └─▶ Selectable component list + density guidance are config-generated from domains.json
    │   │   (`{valid_components}` via `render_valid_component_names`, `{density_gates}` via
    │   │   `render_density_gate_table` in `shared_config/domain_config.py`) — edit `components`
    │   │   in domains.json to change what the planner can pick. `densityGates` is **advisory**.
    │   └─▶ Domain playbooks (domains.json `playbooks`, keyed "<domain>:<format>": gaming:unboxing,
    │   │   review:unboxing, learning:talk, learning:lecture, podcast:podcast, podcast:interview,
    │   │   review:review, news:explainer; `playbookAliases` route other probe formats to one, e.g.
    │   │   review:commentary → review:review): `_render_playbook()` injects
    │   │   required/preferred/forbidden components + planGuidance;
    │   │   effective_requirements(): forbidden = UNION, required = playbook overrides domain
    │   │   (conditional on the plan's own evidence), max always from domain
    │   └─▶ Post-validation `_enforce_domain_policy()` strips forbidden components before
    │   │   extraction (quiz_arena only for quizEnrichment domains)
    │   └─▶ Fallback: category-based plan when the call fails or confidence < 0.6
    └─▶ Plan-done reconcile (pipeline/reconcile.py + reconcile_pass.py, 3.2) once plan AND memory
    │   are in. Evidence view: a has_* key is true when either reader said true, false only when
    │   BOTH answered false (doubly false), absent otherwise (one silent, or memory failed); it
    │   replaces the plan's evidence for every reader (`evidence` in triage_complete /
    │   pipeline.triage; each reader's own answers as `planEvidence` / `memoryEvidence`).
    │   Steps: (1) a tab whose dataSource `requiresEvidence` is doubly false → re-pointed, kept
    │   (moment_track), demoted onto a free eligible source, else dropped — a `has_code` tab is
    │   deferred to frames-done instead (`deferred_to_frames`); (1b) a Moments tab on
    │   learning.timestamps reads the primary's own moment source (podcast.segments, …;
    │   `repointed` / `primary_moment_source`); (2) the primary's required
    │   components backfilled from its defaultTabs unless doubly false (`not_backfilled`, under
    │   the defaultTab id it would have added) — tech's has_code-gated code_playground with no
    │   tab planned for it is deferred too; (3) quiz dropped outside quizDomains or when
    │   is_learnable is doubly false, else moved last; (4) dedupe (dataSource, component) — the
    │   tab the primary requires (equivalents included) stays, else the first; (4b) a planned
    │   info_grid joins the planned tab that answers its question as one of its `folds`
    │   (R6, `folded` / `info_grid_alone`; host by `infoGridHosts`; never below 3 tabs);
    │   (5) 3–6 tabs. Every decision → `pipeline.reconcile` (row shape: DATA-MODELS.md); drops →
    │   droppedTabs `reconcile_<reason>`. The plan-parse guard (plan_reconcile.py) keeps only
    │   registry validity + `inactive_domain`.
    │   Frames-done (both paths — split: before the visual group is cut; today's: the
    │   `visual_inject` step, before extraction): when anything was deferred, the screen decides
    │   (vision `scene_type: code`, or code-shaped on-screen text). Code → the tabs are kept
    │   (`kept_screen_code`), has_code turns true and the deferred requirement is backfilled (its
    │   tech.snippets joins the visual group — no latency cost); no code, or no vision/OCR → the
    │   step-1 chain and `not_backfilled`; then steps 2, 3 and 5 re-run. A changed tab list
    │   sends a fresh `meta`.
    └─▶ `<video_memory>` block (pipeline/video_memory.py) rendered once from plan + memory —
    │   fills every writer's `{video_context}` slot (extraction, synthesis, quiz)
    └─▶ SSE: synthesis_complete (memory-done); triage_complete + meta at plan-done, after reconcile
    └─▶ Plan-done (plan and memory both in): the provisional Moments tab (phases/moments.py,
    │   pipeline-1min 2.1) — tab_ready `provisional: true, final: false` built from the
    │   moment fallback chain (memory outline → YouTube chapters → description timestamps,
    │   the latter only if the analysis already landed; an outline moment's description is its
    │   section's `establishes` line), with the id/label/goal of the tab
    │   that replaces it: the plan's first moment_track tab, else the always-Moments tab
    │   (`key_moments`, see 6); the web strip labels it "Preview" until then. `position` is an estimate (overview + the planned tabs before
    │   it, quiz excluded). No source with an entry → no tab; a food / fitness / project plan
    │   whose steps are the timeline (step_player / step_flow_canvas / workout_room, moment_track
    │   not required; D6, output-quality 0b) → no tab (moment_fallback.steps_are_the_timeline)

 4b. FRAMES-DONE (both paths; code — phases/frames_done.py)
    └─▶ Frame captions + OCR → `ctx.visual_annotations` (`<visual_annotations>`, chronological
    │   `[m:ss] caption | on-screen text`; pipeline/visual_annotations.py). Never written into
    │   clean_text: probe, plan, memory, the Qdrant transcript points and the faithfulness
    │   judge's transcript part read speech only. Readers: extraction (the visual group's job,
    │   sliced per chunk; today's single call: its job block, sliced per batch), the faithfulness
    │   judge, `source="visual"` Qdrant points. Replaces Phase 2.5 `inject_visual_context`.
    └─▶ Then the code tabs reconcile deferred are decided on the screen (see 4, "Frames-done"); a
    │   changed tab list sends a fresh `meta`. Timing step `visual_inject` (name kept).
    └─▶ Split path: runs inside the phase-2 group right after frames (`then_frames_done`, which
    │   first waits for plan-done) and sets `ctx.frames_done` — the visual group's signal. Today's
    │   path: after the whole phase-2 group, before its one extraction call.
    └─▶ `pipeline.timing` on the split path: the `transcript_frames` wall spans the field groups
    │   too, and `frames` includes its wait for plan-done + the `visual_inject` step (see 5)

 5. EXTRACTION — FIELD GROUPS (default, EXTRACTION_PARALLEL=true; phases/split_extraction.py, 3.3–3.8)
    └─▶ A member of the phase-2 group: it waits for plan-done, never for frames (only its visual
    │   group does), so the text groups run while frames + vision still run
    └─▶ Chunks (pipeline/extraction_chunks.py `plan_chunks`, 3.7): ONE chunk — the whole marked
    │   transcript, every annotation — unless one batch can't cover the video
    │   (`needs_chapter_batching`: longer than MAX_MINUTES_PER_BATCH or over MAX_TOKENS_PER_BATCH
    │   estimated tokens). Then the chapter chain + `batch_chapters` cut it (see Chunked
    │   Extraction), each batch a chunk with its `<batch_partial_context>` and its own annotation +
    │   key-frame window; a sentence force-split when the chain gives ≤ 1 batch.
    │   CHUNKED_EXTRACTION_THRESHOLD is not read on this path
    └─▶ Requested fields (pipeline/field_requests.py): the union over the reconciled tabs of the
    │   planned dataSource (+ the fields of the info_grids folded into it), its registry siblings
    │   (only inside a domain some tab plans on), the rendering component's side reads (no
    │   learning.timestamps stand-in for a Moments tab with a field of its own), the overview's
    │   tip/level reads, and every requested domain's scalars — never an inactive domain's fields,
    │   no unplanned modifier output
    └─▶ Groups (pipeline/field_groups.py, domains.json `grouping`): text fields bucketed by domain,
    │   split by field when Σ outputWeight × expect exceeds `groupOutputBudget` (1,500), a slice
    │   estimated under `minGroupOutput` (400) riding with the domain's previous one, at most
    │   `maxTextGroups` (5) text groups, ordered by the first tab they serve — text1 holds the first
    │   planned tab. Every `waitsForVisual` field (tech.snippets / patterns / cheatSheet /
    │   setup.commands, gaming.highlights) forms ONE visual group, re-cut at frames-done from what no
    │   text group asked for (nothing left → not fired)
    └─▶ Request (pipeline/extraction_group_prompt.py, cut from the same rendered base_extraction
    │   prompt): three user blocks, no system prompt — block 1 `ttl: 1h` = the rules + every
    │   requested domain's full schema + the primary's example; block 2 (5 min) = `<video>` + the
    │   chunk's transcript + `<video_memory>`; block 3 (uncached) = the group's job
    │   (`extraction_group_job.txt`: "emit ONLY these keys" + their shape, the tabs it serves; the
    │   visual group adds the annotation guide, the chunk's annotations and key frames; the group
    │   requesting learning.keyIdeas, in a learning- or podcast-primary run, ends with that
    │   primary's worked example from `extraction_group_rules.txt`).
    │   LLM_EXTRACTION_MODEL, json_mode, 16,384 max tokens, 1 retry; each call's timeout follows
    │   its estimate (Σ outputWeight × expect ÷ 60 tok/s + 30 s, 120–300 s); the group-job template
    │   is read once per run. Cache layout and hit rates: llm-cost-model.md
    └─▶ Firing (pipeline/extraction_groups.py, warm-first): per chunk, text1 is the only streamed
    │   call; its first chunk — or GROUP_RELEASE_FALLBACK_S (5 s; 1.5 s before hotfix
    │   4.1; a race counts in counts.cacheRewrites) — releases the chunk's other text
    │   groups, which then read blocks 1 + 2 instead of writing them. The visual group fires once
    │   frames-done cut it. Every call holds a slot of the run's EXTRACTION_PARALLEL_BATCHES
    │   semaphore (text1 calls first)
    └─▶ Landing: each answer is validated as it lands (`group_merge.py`, 3.5): only the group's
    │   requested keys the model filled are kept (field-name spellings mapped to the registry key, no
    │   model defaults for unrequested/omitted keys). Per landed call: `extraction_progress`
    │   `{section: "groups", batch, of}`, then a preview `tab_ready` of every planned tab whose
    │   requested fields have all landed (phases/group_emission.py, 3.6: `final: false`, no
    │   crossTabLinks/attachments, at its plan-order position, re-sent only when its content
    │   changed; never the overview, a moment_track tab or the quiz). The quiz call starts here
    │   (`ctx.quiz_task`) once the text groups holding a quizDomains field landed (the visual group
    │   when none does; a strip-only quiz also waits until frames-done can no longer drop its host).
    │   The hero rewrite starts here too (`ctx.hero_task`, HERO_REWRITE_ENABLED): once every text
    │   group has landed on every chunk (text-groups-done) `GroupLanding` hands the extraction merged
    │   so far to `phases/synthesis.start_hero_task`; the late group awaits it (timing step
    │   `hero_call`). A failed or cancelled run stops both the quiz and the hero call
    └─▶ Merge, once every call landed: ONE merge — chunk-major, text1..N, visual; lists concatenate
    │   (later duplicates by name/label/term/title/ingredient/tool/location dropped), first
    │   non-empty scalar wins, number/step/order/index renumbered only when 1..n in every list.
    │   Quality = requested fields filled; `pipeline.extraction._groups` (one row per call). ONE
    │   `extraction_complete`
    └─▶ `pipeline.timing` on this path: `transcript_frames` spans the extraction too;
    │   `split_extraction` = the member (from the group's start), `extraction` = plan-done → last
    │   group landed (the DONE log's `extraction=`); `frames` includes its wait for plan-done and the
    │   `visual_inject` step; group calls are `llmCalls` spans `extraction_<group>` (`_c<k>` per
    │   later chunk; ledger feature `summarize:extraction:<group>`), the streamed warm-up call with
    │   `ttftMs`; a started quiz call is the `quiz_call` step, a started hero call the `hero_call` step
    └─▶ Failure paths (3.8, `group_failures.py`): `{}` or the group's keys answered `null`/`{}`
    │   = an empty group that landed (a later chunk's window, a video without that content).
    │   A lost answer — not a JSON object, no key of the group, content validation dropped or
    │   reset, or the warm-up stream raising / bringing nothing — gets ONE more call under the
    │   same span, unstreamed, `max_retries=0` (≤ 2 calls; the row records `retried`
    │   `parse`/`empty`/`stream`); still nothing → `failed`. A failed group's fields count as
    │   landed (no tab waits), assembly drops/demotes its tabs through today's fallbacks
    │   (`droppedTabs` reason `extraction_group_failed`) and the run is `degraded`. Every
    │   group of every chunk failed → today's error (`FAILED`, `LLM_ERROR`, "Extraction
    │   failed to produce data"; the orchestrator still runs assembly afterwards). A failed plan
    │   call → the fallback plan's default tabs are requested unchanged.

 5b. EXTRACTION — TODAY'S SINGLE CALL (EXTRACTION_PARALLEL=false; phases/extraction.py → extractor.py)
    └─▶ The rollback lever: after the whole phase-2 group (frames-done step first), ONE call via
    │   run_parallel_phases (heartbeats) — the same request bytes for the same plan as before the
    │   split (replay identity test). It reverts extraction only: reconcile, the `<video_memory>`
    │   evidence ranges, the toolkit markers and PIPELINE_VERSION stay; the flag also runs a long video's chunked
    │   batches one at a time. A full rollback is a redeploy of the previous main
    └─▶ Prompt (pipeline/extraction_prompt.py): system = the rules (no per-video text);
    │   user block 1 = [video + transcript + video_memory] with the one cache breakpoint;
    │   block 2 = the job — planned tabs' briefs + registry caps, schemas/{tag}.txt (no
    │   SCALING tables or invention floors), the domain's example (none if the domain has
    │   none), visual annotations, ≤ 12 key-frame captions. Per-call values are bound in one
    │   pass, so on-screen text is never read as a placeholder (llm-cost-model.md)
    └─▶ Strategy (`extractor._resolve_strategy`): longer than CHUNKED_EXTRACTION_THRESHOLD (900 s)
    │   with ≥ 2 chapters that `batch_chapters` makes > 1 batch → chunked (see Chunked
    │   Extraction); otherwise ≤ 5,333 words → single (16K tokens, 240 s); more → overflow (one
    │   call, 32K tokens, 300 s + words/100, ≤ 600 s). Over 20,000 words, or a chaptered video
    │   that fits one batch, is force-split into EXTRACTION_FORCE_SPLIT_CHUNKS batches instead
    │   (overflow when that gives fewer than two)
    └─▶ Pydantic validation on all output; `extraction_progress` (`section` "all", or
    │   "chunked" / "chunked-sequential" per batch), `extraction_complete`

 5c. AFTER EXTRACTION (both paths)
    └─▶ Coverage (meta.extractionCoverage, `degraded`) and quality (share of planned
    │   dataSources populated) are METRICS: logged, never a retry. `degraded` = a dropped
    │   batch (split path: a failed field group) or critical coverage; a split run without a
    │   timestamped field records the call counts alone (`meta.extractionCoverage` =
    │   `{batchesTotal, batchesSucceeded, batchesDropped}`, `flag_failed_groups`). The
    │   synthesis-fed retry, RETRY_SCORE_THRESHOLD and validate_extraction_counts are gone (1c.5)
    └─▶ Faithfulness judge (LLM_JUDGE_MODEL = gpt-6-luna) fires in the background once extraction
        has data — on the split path when the whole phase-2 group is done (transcript +
        annotations; informational). A second launch follows assembly (output-quality 2.4,
        `_launch_hero_quiz_check`): up to two more calls score the hero's stamped takeaways
        (`faithfulness_hero`, only when the hero rewrite wrote the hero) and 2 quiz questions
        (`faithfulness_quiz`, only when `QUIZ_V2_ENABLED`) against the transcript around their
        stamps — flag-off runs make no extra judge call (pipeline/faithfulness_hero_quiz.py;
        OBSERVABILITY.md)

 6. ASSEMBLY → LATE GROUP → SAVE (phases/assembly.py)
    └─▶ Description analysis (≤ 5 s wait; past it meta goes without it) → SSE description_analysis
    └─▶ Synthesis FIRST only when memory left the tldr or takeaways empty (the overview and the
    │   min-3-tab Key Info fallback need them; "synthesis" = the synthesis slot — the hero rewrite
    │   when enabled, else the synthesis call); otherwise memory's hero seeds meta + overview
    └─▶ Assembly: code, 0 LLM calls — transforms extraction → component-addressed TabEntry[]. First a
    │   quote pre-pass on a copy of the extraction and the quiz (pipeline/quote_verify.py: a quote
    │   not found in the transcript is cleared, or kept as prose when the source is not English),
    │   and the quiz's stamps are read in seconds (assembly/quiz_stamps.py)
    └─▶ 29 assemblers in ASSEMBLER_REGISTRY (assembly/registry.py — spot_explorer, moment_track,
    │   code_playground, quiz_arena, tier_list, claims_tracker, … + secondary/attachment assemblers)
    └─▶ Output-quality shapes and caps (module map: summarizer-workflow.md §Assembly notes; sets and
    │   bands from domains.json `assemblyPolicy` / `momentCapsByDuration` / `injectedTabs`):
    │   learning.keyIdeas → spot cards (claim, why, quote, timestamp, kind badge, speaker, sections),
    │   capped at keyIdeasCapFactor × the §9 count (10 / 16 / 24 / 32 by length); a Moments track
    │   capped strictly by the §9 bands, and a label-only one yields to a described sibling or the
    │   described fallback chain (R7); the answer tab's stat_banner only from `kind: number`
    │   key ideas with their stated `figure`, no text mined (≥ 5 tiles or none, ≤ 8);
    │   folded info_grids join their host (R6) and every info_grid row ships with a value (R5);
    │   quiz questions carry their timestamp; the tabs assembly adds take their goals from
    │   `injectedTabs`
    └─▶ Domain policy: components in effective_requirements(domain, format).forbidden are dropped
    │   (reason `domain_forbidden`); per-domain max caps enforced (reason `domain_max_cap`)
    └─▶ Degrade-never-drop ladder (assembly/promotion.py): a tab failing its primary component's
    │   validation demotes down _DEMOTE_LADDER (e.g. step_player → checklist → display_section,
    │   tier_list → spot_explorer → info_grid → display_section) instead of dropping; demoted tabs are stamped
    │   `degradedFrom` and re-pass enforce_density. Non-demotable by design: quiz_arena,
    │   video_filmstrip, lyrics_karaoke, frame_strip.
    └─▶ Per-tab drop accounting: assemble_response returns {"meta", "tabs", "dropped"} — drop
    │   reasons: assembler_raised, validation_empty_list, assembler_returned_none,
    │   domain_forbidden, domain_max_cap, all_timestamps_impossible, steps_are_the_timeline
    │   (D6, below), duplicate_tab (the post-promotion dedupe), and on the split path
    │   extraction_group_failed (one of the first three for a tab that waited for a failed
    │   group's field — assembly's own reason kept in `detail`); an info_grid tab R5 leaves
    │   without a valued row is `validation_empty_list` too (feeds tabsDropped/droppedTabs
    │   telemetry instead of the old designed−assembled subtraction); the backstop's
    │   `requirement_evidence_false` entry (a required component the evidence rules out, `id` =
    │   the defaultTab it would have added, `""` when a live tab holds it) and the dedupe's
    │   `duplicate_items` entry (a twin that shed its shared items and still ships) are listed,
    │   never counted. The plan stage's drops are prepended (reconcile's as `reconcile_<reason>`, see
    │   4): reason `invalid_datasource` = an unregistered dataSource with no same-domain
    │   registry path for the tab's component (plan.py `_validate_data_sources`); when that
    │   leaves no tab, the domain defaults stand in, `planFallback: true` is persisted and
    │   tabsDesigned counts only the plan's drops
    └─▶ Frame thumbnail injection: items with timestamps get thumbnailUrl from nearest S3 frame;
    │   a relaxed second pass (±15s window) backfills still-thumbless moment items
    └─▶ Timestamp hygiene: moment_track items beyond video duration (+10s) are dropped;
    │   a tab emptied by that drop is removed (dropped reason `all_timestamps_impossible`)
    └─▶ A Moments tab always exists (Kfir 2026-10-12, 2.1): with no moment_track tab left after
    │   the domain requirements (a podcast's backfilled segments win), the always-Moments tab
    │   (`key_moments`, "Key Moments"; `key_moments_2`… if a plan tab holds the id) joins after
    │   the planned/required tabs, built by the moment fallback chain (memory outline →
    │   YouTube chapters → description timestamps; assembly/moment_fallback.py) and framed
    │   like any moment tab. A real tab: saved, Redis-cached, RAG-indexed, translated. No
    │   source with an entry → no tab and no drop record (never invented); never added
    │   where the domain policy forbids moment_track. Exception (D6, output-quality 0b):
    │   when the steps are the timeline (a food, fitness or project response whose domain
    │   does not require moment_track ships a step player, step flow canvas or workout
    │   room), every moment_track tab is dropped (reason `steps_are_the_timeline`) and
    │   no always-Moments tab is added — recipes, workouts and step tutorials are
    │   navigated by their steps (undone when the dedupe then drops the step tab: the
    │   Moments come back); any other primary (a podcast or lecture with a stray step
    │   tab) keeps its Moments
    └─▶ Gallery tab: ~12 curated frames from gallery_frames (not all uploaded frames)
    └─▶ Cross-tab links resolved from static LINK_RULES
    └─▶ SSE: every `tab_ready` goes through the run's TabLedger (pipeline/tab_ledger.py, the
    │   emission contract). Field groups (default): the tabs already streamed per group as
    │   previews, so assembly previews only what the client lacks — the overview, tabs assembly
    │   added (backfills, filmstrip, minimum-3 fallbacks), tabs the last groups or the frames
    │   changed — at their exact positions, `final: false`. EXTRACTION_PARALLEL=false: every
    │   tab but the moment tabs, each with `position` = index in the persisted tab order,
    │   `final: true` when no late phase can change the tab (no quiz pending; the overview not
    │   while a late synthesis is pending), else `final: false`
    └─▶ LATE GROUP (parallel, with heartbeats):
    │   └─▶ Moment fill (assembly/moment_frame_fill.py): a frame AT each still-frameless
    │   │   moment's timestamp by seeking the run's one local ≤720p file (downloaded by the
    │   │   frames phase; a manifest cache hit starts it here for ≥ 3 moments only, inside the
    │   │   budget with 30 s kept for seeks; cap 12 frames, 150 s) → the moment tabs stream
    │   │   WITH their images (replacing the provisional Moments tab by id, or under another
    │   │   id with `replaces: "<provisional id>"`); the file is
    │   │   deleted after it (its last reader)
    │   └─▶ The synthesis slot (unless it ran first; phases/synthesis.py). With HERO_REWRITE_ENABLED
    │   │   the hero rewrite (pipeline/hero_rewrite.py, hero_rewrite.txt): ONE call on
    │   │   LLM_EXTRACTION_MODEL (Sonnet 5.5), JSON mode, 2,000 max tokens, 25 s, one retry that
    │   │   carries the failed checks — only when the hero would be None or a D18 line would drop;
    │   │   what finalize repairs itself costs no second call. It reads the field groups' cached blocks 1 (+ 2 on a one-chunk
    │   │   run) and the extraction compacted to ≤ 5,000 tokens with ids (hero_compact.py), so
    │   │   code copies each takeaway's timestamp from the item it cites; hero_validate.py checks
    │   │   numbers, sizes, stamps (no transcript read → a typed stamp only within 30 s of a clock the
    │   │   call read), quoted spans of 3+ words (quote_verify), tab names and D18 (worthItFor /
    │   │   skipIf ≤ 20 words, nothing about the creator). Started at text-groups-done and awaited here (EXTRACTION_PARALLEL=false:
    │   │   called here over the whole extraction). Its meta — tldr, brief (= masterSummary),
    │   │   keyTakeaways as "<claim> [m:ss]" strings, worthItFor, skipIf, seoDescription — replaces
    │   │   memory's hero; patches meta + overview in place → SSE synthesis_complete (superset, with
    │   │   brief / worthItFor / skipIf). No valid hero, or the flag off → today's synthesis
    │   │   (pipeline/synthesis.py): fast tier, 1,200 tokens,
    │   │   20 s, 1 retry. Input: <video_memory> + compact FINAL extraction (≤ 6,000 chars,
    │   │   every list cut to the first cap of 8/5/3/1 that fits) + the viewer's tab labels.
    │   │   Writes masterSummary + seoDescription (≤ 160 chars); tldr/takeaways only as the
    │   │   fallback. Patches meta + overview in place → SSE synthesis_complete (superset);
    │   │   the patched overview goes out with the final re-emit. Failure → memory's hero
    │   │   only, masterSummary/seo empty
    │   └─▶ The quiz (phases/enrichment.py, only when needs_quiz: a planned quiz tab, or a
    │       sparse quick_quiz host with evidence that doesn't rule learning out, in a
    │       quizEnrichment domain). QUIZ_V2_ENABLED (default): quiz v2 (pipeline/quiz_v2.py,
    │       quiz_v2.txt) on LLM_MEMORY_MODEL (Haiku 5.5), 18 s per attempt, 1 retry, 37 s total, no
    │       cache breakpoint — reads the marked transcript and the key ideas (learning.keyIdeas
    │       first), writes the video's count (3 / 5 / 8, `quizPolicy.questionsByDuration`) + 2 spare
    │       questions, each with a ≤ 25-word evidence quote and the answer's timestamp; every quote
    │       is checked against the transcript (quote_verify): found → the stamp is set from where it
    │       is said; an English-source quote not found → the question is dropped; a translated one
    │       is kept as prose with an in-video stamp; fewer than 3 survivors → no quiz tab. The
    │       checked questions past the count stay as 2 spares (never dumped): once the hero
    │       landed too, settle_late_quiz (phases/enrichment.py) drops a question whose keyed option
    │       restates a shipped takeaway and refills from them; fewer than 3 left → no quiz tab. False:
    │       one enrich_quiz call on LLM_ENRICHMENT_MODEL (gpt-4o-mini), 30 s total (14 s per
    │       attempt, 1 retry), ≥ 2 valid questions; it asks for 3 / 5 / 8 by length, reads the video
    │       length and the memory hero (no question the hero alone answers), and every question
    │       carries the timestamp of its answer (one past the end + 10 s is dropped). Both bill
    │       `summarize:enrichment`. On the field-group path the call already started when its
    │       groups landed (5) and this step awaits it (cancelled when the plan no longer uses a
    │       quiz). When it lands the response is assembled again (phases/late_quiz.py):
    │       enrichment_complete, the quiz tab last; the quick_quiz host, links and overview
    │       count follow
    └─▶ Settle (phases/synthesis.settle_tab_references, after the late group): the filmstrip the
    │   framed Moments shadow is dropped, every hero / goal sentence naming a tab that does not ship
    │   is removed (assembly/tab_references.py), and a hero page gets meta.readingMinutes (code, 238
    │   words a minute over the final hero + tabs) → SSE synthesis_complete again (a corrected
    │   superset marked `final: true`, which replaces the consumers' copy) when the hero changed —
    │   always with the hero rewrite
    └─▶ SSE: the final re-emit — every tab not yet sent final goes out `final: true` at its
    │   final position, in order (all tabs when a quiz was pending or the tabs streamed per
    │   group — then the overview goes last; else the overview after a late synthesis); each tab
    │   of the response gets exactly one final emission
    └─▶ SSE: complete (tabCount, tabIds = the final tab order, processingTimeMs, degraded) →
    │   save. A tab streamed earlier but missing from tabIds was withdrawn (logged)
    └─▶ Store result to MongoDB + Redis cache (if enabled). Redis response-cache keys are
    │   namespaced by PIPELINE_VERSION (packages/shared/src/config/pipeline-version.json,
    │   currently v10 — bumped v9→v10 by the pipeline-1min 4.0 model refresh).
    │   bypassCache=true submissions arrive with force_refresh (worker payload) OR the
    │   `forceRefresh` flag stamped on the videoSummaryCache version row by the api —
    │   either skips the Redis response cache; the flag is $unset on completion.
    └─▶ Shared artifacts — skipped for an eval-user run (`evalRun`, D25): the Redis response
    │   copy (English videos) and Qdrant points (transcript, default_output, visual)
    └─▶ Transcript S3 storage (background, non-blocking)
    └─▶ SSE: done event + [DONE] signal (non-English: after translation)

 7. TRANSLATION (non-English videos only, ~5-15s)
    └─▶ Triggered when ctx.source_language_code is set (non-English, detected from the
    │   transcript; ctx.language is always "en" — the English-primary output)
    └─▶ translate_to_source() — batched, salvage-tolerant translation
    │   └─▶ Walks the assembled tree, collects every translatable prose
    │   │   string into one flat list (`_collect_strings` + `_SKIP_KEYS`
    │   │   leaf-only deny-list for structural/asset/enum keys)
    │   └─▶ Fast-model calls (`translation_full`) on LLM_TRANSLATION_MODEL
    │   │   (default openai/gpt-6-luna, reasoning off) via
    │   │   src/prompts/translate_flat.txt (flat-list
    │   │   contract: the model returns a list of equal length, applied
    │   │   back into a deep copy via `_set_at_path`)
    │   └─▶ Batching: consecutive runs of ≤ 2,500 source characters
    │   │   (`translation_flat.TRANSLATION_BATCH_CHARS`, shared with the
    │   │   RAG text translation) and ≤ 100 strings
    │   │   (`_MAX_TRANSLATION_BATCH`), up to 8 calls in flight, retry
    │   │   halves included (`_MAX_CONCURRENT_TRANSLATION_BATCHES`). A
    │   │   batch answers ≈ 800 tokens (≈ 10 s on gpt-4o-mini), so the
    │   │   phase takes about one batch's time: EqaMrzd9nZU = 7 calls
    │   │   of ≤ 41 strings, was one 200-string call (4,996 out tokens,
    │   │   56.5 s) + a 12-string one; +280 prompt tokens per call.
    │   │   `max_tokens` is sized to the batch (1,024 + its length,
    │   │   ≤ 16,384), so the 8 calls reserve ≈ 35k tokens against the
    │   │   provider's per-minute limit, not 8 × 16k.
    │   └─▶ Salvage-on-failure (`_translate_or_salvage`): a batch that
    │   │   fails (LLM error / length mismatch / echoed its strings —
    │   │   `is_mirror` per call) is retried once split in
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
    └─▶ No Qdrant work here: the transcript points' English text (the
    │   embedding model is English-only) comes from a background task that
    │   assembly's save starts — captions translated as text on
    │   LLM_TRANSLATION_MODEL (gpt-6-luna, reasoning off; stage
    │   `rag_translation`), Whisper translate only when this run transcribed the audio
    │   (docs/RAG.md §Non-English transcripts). Once `done` is out, the
    │   runner waits for it (≤ 60 s) before persisting pipeline.timing.
    └─▶ Owns the Redis response-cache write for non-English videos:
    │   assembly phase intentionally SKIPS the cache write when
    │   ctx.source_language_code is set. Translation then writes the final
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
    └─▶ Every generation stage writes English (English-canonical; the
    │   probe, plan and memory prompts carry an explicit output-language
    │   line) — this phase translates that English output into the
    │   source language.
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

    async def call_llm(self, prompt, max_tokens=2000, timeout=None, *, ..., effort="low") -> str:
        effective_timeout = timeout if timeout is not None else settings.LLM_TIMEOUT_SECONDS
        # asyncio.timeout that also bounds a refused answer's one re-send: hitting it
        # there raises LLMRefusalError instead of a timeout the retry would re-send
        async with call_deadline(effective_timeout):
            return await self._provider.complete(prompt, max_tokens=max_tokens, ..., effort=effort)
```

`LLMProvider` (`llm_provider.py`) keeps a call model-agnostic (`llm_call.ProviderCall`)
and shapes it for the model it goes to only when it sends it
(`llm_model_rules.request_for_model`: thinking off + effort, the sampling params the
model accepts, no assistant prefill). A refused answer is recorded and the same call
asked once of `config.PREVIOUS_MODEL[model]` with that model's params; no answer →
`LLMRefusalError` (ERROR-HANDLING.md §Refusals).

### LLM Retry Utility

```python
# src/utils/llm_retry.py — All pipeline stages use this instead of calling llm_service directly
async def call_llm_with_retry(
    llm_service, prompt, *, max_tokens=4096, timeout=60.0, max_retries=2, stage_name="unknown",
    use_fast_model=False, json_mode=False, propagate_rate_limit=False, model_override=None,
    temperature=None, system_prompt=None, on_first_chunk=None, effort="low",
    propagate_refusal=False,
) -> str | None:
    """Retry transient errors (retry-after aware), then the cross-provider fallback;
    None when every attempt failed. 400-class errors raise (see ERROR-HANDLING.md).
    A refusal (LLMRefusalError: the previous model's one re-send got no answer) ends
    the call — no retry, no fallback — as None, or raised with propagate_refusal."""

# Stage-specific configurations (per attempt; models = config.py / compose defaults):
# Tier probe:  4s,   retries=0  (LLM_CLASSIFIER_MODEL = Haiku 4.5, temperature 0)
# Plan:        60s,  retries=1  (primary = Sonnet 5.5, effort medium, 3,500 tokens)
# Memory:      25s,  retries=0  (LLM_MEMORY_MODEL = Haiku 5.5; temperature 0 dropped for it)
# Extraction groups: 120-300s from the group's estimate, retries=1 (+ one more call for a lost answer;
#              Haiku 4.5; the warm-up passes propagate_refusal — a refused warm-up is not re-asked)
# Extraction (EXTRACTION_PARALLEL=false): 240s single / dynamic overflow / 300s per batch, retries=2
# Synthesis:   20s,  retries=1  (fast tier, 1,200 tokens)
# Quiz:        14s per attempt, 30s total, retries=1 (LLM_ENRICHMENT_MODEL = gpt-4o-mini)
# Description: 25s per attempt, 30s total, retries=1 (LLM_DESCRIPTION_MODEL = gpt-6-luna)
# Every call but the plan runs at the default effort "low" (frame vision on Sonnet 5.5 included);
# effort only reaches Haiku 5.5 / Sonnet 5.5 (llm_model_rules).
```

### Pipeline Modules

```python
# All pipeline modules follow the same pattern:
# - Accept llm_service, repository, and domain-specific args
# - Call call_llm_with_retry() for LLM interactions (not llm_service directly)
# - Return typed results (Pydantic models or dicts)

# src/services/pipeline/plan.py
async def run_plan(llm_service, transcript, video_data, ...) -> PlanResult | None
# Single primary-model call (Sonnet 5.5, PLAN_EFFORT = "medium") over the full marked
# transcript: tabs + briefs, evidence, terms

# src/services/pipeline/memory.py
async def run_memory(llm_service, MemoryInput) -> MemoryResult | None

# src/services/pipeline/triage.py (fallback/validation only)
# TriageResult model + tab validation when plan confidence < 0.6

# src/services/pipeline/phases/split_extraction.py (default) — field groups over the run's chunks
async def run_phase_split_extraction(ctx) -> AsyncGenerator[str, None]

# src/services/pipeline/extractor.py (EXTRACTION_PARALLEL=false)
async def extract(llm_service, triage_result, transcript, video_data, ...) -> AsyncGenerator[dict, None]

# src/services/pipeline/enrichment.py — the quiz only; needs_quiz(plan, format) gates it

# src/services/pipeline/synthesis.py
async def synthesize(llm_service, title, channel, duration, output_type, extraction_summary,
                     video_context, *, tab_labels, hero_fallback) -> SynthesisResult
```

**Model mapping (config.py):**
```python
MODEL_MAP = {
    "anthropic": {"default": "anthropic/claude-sonnet-5-5", "fast": "anthropic/claude-haiku-4-5-20251001"},
    "openai": {"default": "openai/gpt-4o", "fast": "openai/gpt-4o-mini"},
    "gemini": {"default": "gemini/gemini-2.5-flash", "fast": "gemini/gemini-2.5-flash-lite"},
}

# The model a refusal falls back to — once, same call, same provider, never chained (4.0b).
# Haiku 4.5 and Sonnet 4.6 stay configured for this.
PREVIOUS_MODEL = {
    "anthropic/claude-haiku-5-5": "anthropic/claude-haiku-4-5-20251001",
    "anthropic/claude-sonnet-5-5": "anthropic/claude-sonnet-4-6",
    "openai/gpt-6-luna": "openai/gpt-4o-mini",
}
```

The primary (`default`) model only reaches the plan and `/health`: every other stage
is pinned per stage (Environment Variables above). Picks and evidence per call type:
`dev/completed/pipeline-1min/gates/g4-model-ab.md` §Picks.

---

## Content Tag System

The plan (LLM) picks the content tags from the full transcript + metadata, steered by the tier probe's domain; reconcile and the field groups only extract the active tags' fields. Each tag has a domain schema injected into a shared extraction template.

### 14 Primary Content Tags + 2 Modifiers

Category fallback = the metadata categories `domains.json` `categoryMap` maps to the tag (its
`default` is learning); the quiz runs only for `quizEnrichment.quizDomains`.

| ContentTag | Category Fallback | Domain Schema | Enrichment |
|------------|-------------------|---------------|------------|
| learning | education, study, lecture (default) | `schemas/learning.txt` | quiz (when the plan demands one) |
| tech | coding, programming, code, tutorial | `schemas/tech.txt` | quiz (when the plan demands one) |
| fitness | fitness, workout, exercise | `schemas/fitness.txt` | - |
| food | cooking, recipe | `schemas/food.txt` | - |
| music | music | `schemas/music.txt` | - |
| travel | travel | `schemas/travel.txt` | - |
| review | reviews, review | `schemas/review.txt` | - |
| project | diy, craft | `schemas/project.txt` | - |
| language | languages, pronunciation, grammar, vocabulary | `schemas/language.txt` | quiz (when the plan demands one) |
| science | physics, chemistry, biology, math | `schemas/science.txt` | quiz (when the plan demands one) |
| podcast | podcast, interview, talk_show | `schemas/podcast.txt` | quiz (when the plan demands one) |
| news | news, journalism, current_events, politics | `schemas/news.txt` | quiz (when the plan demands one) |
| gaming | gaming, gameplay, esports, lets_play, speedrun | `schemas/gaming.txt` | - |
| sport | sport(s), match_analysis, highlights | `schemas/sport.txt` | - |
| narrative | — | `schemas/narrative.txt` | Modifier only |
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
| `phase` | `{phase}` — progress marker (`metadata_retry`, `transcript_retry`, `transcript_cached`, `transcript`, `audio_transcription`, `whisper_transcription`, `metadata_fallback`, `translation`) |
| `description_analysis` | `{links, resources, socialLinks}` (emitted at assembly, after a ≤ 5 s wait for the analysis) |
| `triage_complete` | `{contentTags, modifiers, primaryTag, userGoal, tabs (with briefs), confidence, contentFormat, evidence, planEvidence, memoryEvidence, terms}` — at plan-done, after reconcile (API-REFERENCE.md#triage_complete) |
| `extraction_progress` | `{section, percent, batch?, of?}` — the field groups (default) emit `section="groups"` with `batch`/`of` per landed group call (`of` follows the visual group's fate); with `EXTRACTION_PARALLEL=false` one call emits `section="all"`, chunked batches `section="chunked"` with `batch`/`of`, rate-limited fallback batches `section="chunked-sequential"` |
| `extraction_complete` | `{data}` — the domain-keyed extraction (+ `batches_total` / `batches_succeeded` from chunked batches); ONE per run — the field groups send it once every group landed, after the merge |
| `enrichment_complete` | `{quiz}` — only when the plan demands a quiz; arrives after the first tabs, before the quiz tab |
| `synthesis_complete` | Up to three times: `{tldr, keyTakeaways}` at memory-done, then the superset `{tldr, keyTakeaways, masterSummary, seoDescription}` after the first tabs (the hero rewrite adds `brief`, `worthItFor`, `skipIf`), then a corrected superset after the late group when the hero changed (the hero rewrite's adds `readingMinutes`; marked `final: true`); consumers merge the earlier emissions and replace with the final one (API-REFERENCE.md#synthesis_complete) |
| `frames` | `{videoId, frames: [{index, timestamp, url, s3Key?, ocrText?}]}` (scene frames) |
| `meta` | `{title, contentTags, modifiers, primaryTag, tabCount, tabLabels, contentFormat?, language?, isRTL?, degraded?}` — at plan-done after reconcile (the plan's tabs, before any `tab_ready`), again at frames-done when the deferred code tabs changed the tab list; a cached serve sends the stored tabs and `degraded` |
| `tab_ready` | `{id, label, emoji, component, props, goal?, crossTabLinks?, attachments?, degradedFrom?, position, final, provisional?, replaces?}` (progressive tab; contract in `pipeline/tab_ledger.py`, API-REFERENCE.md#tab_ready). `position` = index in the response's tab order, exact when `final` (an estimate before); `final: true` = the tab's last emission this run, exactly one per tab of the response; `provisional: true` = the plan-done Moments placeholder, never final, replaced by id (or by the tab whose `replaces` names it). A corrected `final` can follow for the same id — the last final wins. A tab id can arrive several times — clients replace by id and sort by `position`. With the field groups (default) a tab first arrives as a **preview** once its group(s) landed (`final: false`, without `crossTabLinks` / `attachments`, at its plan-order position) and goes final in the closing re-emit, the overview last. Cached serves send every tab once, `final: true` |
| `complete` | `{tabCount, tabIds, processingTimeMs, degraded}` (v2 completion; `tabIds` = the final tab order — a tab streamed but missing here was withdrawn) |
| `error` | `{message, code?}` — `code` is an `ErrorCode` value (see docs/ERROR-HANDLING.md) |
| `token` | `{phase, token}` — legacy token streaming; protocol slot kept, no current emitter |
| `heartbeat` | `{ts}` — keepalive emitted every `SSE_HEARTBEAT_SECONDS` (12s) during long silent phases (e.g. multi-minute Whisper) so the API-gateway SSE proxy does not abort an idle-but-live stream. **Frontend treats it as a no-op.** |
| `done` | `{videoSummaryId, processingTimeMs?, degraded?, cached?}` (terminal + confetti trigger; `cached: true` on cached serves). For non-English videos this is **deferred to the translation phase** — assembly marks the doc `processing` and translation emits `done` after writing `status="completed"`. |
| `[DONE]` | Terminal signal |

> `sponsor_segments` was documented here for years but never emitted by any
> code path — removed 2026-07-12 (project-score-9 4.5c). The `done` event
> carries no `phase` field (that was a doc-only invention).

**Event broker** (`services/cache/pipeline_event_stream.py`, `routes/pipeline_broker.py`):
every chunk of a run is appended to the Redis stream `vie:pipeline:events:<videoSummaryId>`;
SSE consumers replay it from the start (`XREAD` from `0`). Winning the producer lock
(`vie:pipeline:lock:<id>`, SET NX in a Lua script) deletes the previous run's stream in the
same atomic step, so a re-run of the same row (retry, regenerate, stall re-dispatch) never
replays the old run's events (2.1). The stream never exists without a TTL: every event
renews a running TTL (`PIPELINE_LOCK_TTL_SECONDS`, also renewed by the lock heartbeat) and
`mark_done` shortens it to `PIPELINE_STREAM_TTL_SECONDS` after the done sentinel — a
producer that dies mid-run leaves a stream that expires. Streams leaked before 2.1 (no TTL,
no lock) are swept by `scripts/expire_leaked_pipeline_streams.py` (dry run by default;
`--apply` sets a TTL, never deletes; usage in the script's docstring).

### Key Design Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| Triage-first | LLM picks tags + designs tabs | More flexible than fixed output types |
| Schema injection | `base_extraction.txt` + `schemas/{tag}.txt` | One extraction prompt, domain schemas swapped in |
| Plan stage | Single Sonnet call replaces Manifest + Triage | 2 calls → 1, saves ~30-50s, better coherence |
| Plan fallback | Falls back to category-based mapping if confidence < 0.6 | Safety net when LLM plan fails or is low-confidence |
| Quality as metrics | Coverage + populated-dataSource score are logged, never retried (1c.5) | The synthesis-fed retry fired in 7 of 55 runs and improved 0 of 3 measurable ones |
| Legacy coercion | `field_validator(mode="before")` | Accepts old string format for travel tips and music analysis |
| Finance modifier costs-only | `costs[]` + `savingTips[]`, no budget | Primary domain owns budget structure |
| Field-group extraction (v9) | The fields the reconciled tabs read, cut into ≤ 5 text groups + 1 visual group per chunk; text groups fire at plan-done, the visual group at frames-done; tabs stream per group | Extraction no longer waits for frames, the first tabs arrive per group, and no output tokens go to fields no tab reads. `EXTRACTION_PARALLEL=false` keeps the single call (by word count: single / overflow / chunked) as the rollback lever |
| Category fallback | Map video category to content tag | Safety net when triage confidence < 0.6 |
| Shared domain config | `@vie/shared` `domains.json` via Docker mount | Single source of truth for domains, tabs, gradients, categories, **planner-selectable `components`**, and the **`densityGates`** table across TS + Python. `plan.txt` `{valid_components}` + `component_toolkit.txt` `{density_gates}` are generated from it (no hardcoded prompt lists); `test_contract_parity.py` asserts the generated prompt matches config. NOTE: `densityGates` is advisory LLM-steering text — assembler enforcement (`_TAB_ITEM_CAPS`, per-assembler min/max, `density.py`) is independent. The enforced blocks — `domainRequirements`, `playbooks` + `playbookAliases`, `quizPolicy`, `momentCapsByDuration`, `assemblyPolicy`, `measureUnits`, `injectedTabs`, `infoGridHosts`, `grouping` — are listed in DATA-MODELS.md. The summarizer and the worker read the file once per process (`domain_config._load_config` is cached; several modules bind its sets at import), so restart both after editing it — `PROMPT_SOURCE=disk` re-reads prompts, never the registry. |

---

## Output Schema

The save (`phases/assembly.py:_result_document` → `repository.save_structured_result`,
allowlisted keys) writes the current shape; older rows can still hold the v1 / v2 fields
(`triage`, `output`, `assembledMeta`, `assembledTabs`), which the cached serve and the API
read as fallbacks (Frontend Meta Resolution below). Field-level detail:
[DATA-MODELS.md](./DATA-MODELS.md).

```python
# videoSummaryCache row after a run (English; non-English adds sourceLanguage in translation):
{
    "status": "completed",                  # "processing" for non-English until translation
    "youtubeId": "...", "title": "...", "creator": "...", "duration": 627, "thumbnailUrl": "...",
    "meta": {                               # VIEResponseMeta: contentTags, modifiers, primaryTag,
        ...                                 #   userGoal, tldr, keyTakeaways, masterSummary,
    },                                      #   seoDescription, extractionCoverage?, degraded?
    "tabs": [                               # Component-addressed TabEntry[] in the final order
        {
            "id": "key_points", "label": "Key Points", "emoji": "💡",
            "component": "key_points",      # Maps to COMPONENT_REGISTRY on frontend
            "props": {"items": [...]},      # Pre-resolved props for the component
            "crossTabLinks": [{"targetTab": "concepts", "label": "Related Concepts"}],
        }
    ],
    "language": "en", "isRTL": False,
    "pipelineVersion": "v10",                # packages/shared/src/config/pipeline-version.json
    "pipeline": {                           # the summarizer's own record of the run
        "triage": {...},                    # the triage_complete payload (reconciled tabs + evidence)
        "reconcile": [...],                 # every plan-parse / plan-done / frames-done tab decision
        "extraction": {...},                # validated data + _defaulted, _dropped?, _misTargeted?,
                                            #   _groups (split path: one row per group call)
        "enrichment": {...},                # {quiz} — only when the plan demanded one
        "synthesis": {...},
        "assembly": {...},                  # tabsDesigned / tabsAssembled / tabsDropped / droppedTabs
        # "timing" is $set separately after the phases end (pipeline.timing)
    },
    "degraded": True,                       # only when true: a failed group / dropped batch / critical coverage
                                            # (the run's end also sets it, with degradedReasons, when a frame
                                            # download failed — admin badge only, no meta.degraded banner)
    "processedAt": ..., "processingTimeMs": 25432,
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
│     └─▶ Skipped as stale ("s3-stale" in attempted)     │
│         when this run fetched captions and the blob is │
│         a caption-origin blob of another track (key +  │
│         bucket; pre-2.4 blobs record none). Whisper /  │
│         Gemini blobs never go stale                    │
│                         │                               │
│                         ▼                               │
│  1. yt-dlp Subtitles                                   │
│     └─▶ Track picked during video metadata fetch       │
│         (caption_picker.py): the spoken language       │
│         (the one `-orig` ASR, else yt-dlp `language`)  │
│         → manual in it → its untranslated auto track;  │
│         none → the language waterfall + English pref.  │
│     └─▶ Legacy codes aliased: iw→he, ji→yi, in→id      │
│     └─▶ Source: "ytdlp"                                │
│                         │                               │
│                         ▼                               │
│  2. youtube-transcript-api                             │
│     └─▶ Spoken language first (manual, then generated) │
│         when yt-dlp knew it, then English-first        │
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

**Unusable captions (2.5).** Captions that fetched fine but that the tier probe
judges not a coherent transcript of speech (`transcript_usable: false`) re-run
the audio layers alone (`fetch_audio_transcript`: Whisper → Gemini, the same
gates; no S3, captions or youtube-transcript-api) once, from the text branch —
see step 4 above. Never for music; the Whisper transcript lands in S3 through
assembly, so the next run reads it.

**Caption 429 handling.** A 429 is IP-scoped, so the response depends on the
proxy setup (`services/cache/caption_negative_cache.py`, Redis key
`vie:captions:429`, TTL `CAPTION_429_NEG_TTL_SECONDS` = 900s):

- *Direct or a single proxy exit* — the caption API retries 3× with backoff on
  the same IP; a persisting 429 (or a timedtext 429 from the metadata phase)
  writes the marker, and until it expires every run skips the caption API and
  goes straight to audio.
- *`YOUTUBE_PROXY_EXIT_COUNT` > 1* (Webshare sticky `USERNAME-N` exits) — the
  metadata-phase timedtext fetch and the caption API both move to the next
  exit on a 429 (`download_utils.ytdlp_proxy_exit_urls`, every exit since hotfix
  4.1 — no new one after `EXIT_ROTATION_BUDGET_S` = 15 s, at least 3; the downloads
  keep at most 3 —
  no backoff, one shared loop `download_utils.try_proxy_exits`). Every blocked exit
  sets the fetch's retry notice: the phase awaiting it emits one `phase`
  `metadata_retry` / `transcript_retry` event (`pipeline/youtube_retry.py`; the
  timedtext fetch in the caption task, the caption API in the transcript phase), the
  web shows "YouTube is rate-limiting, retrying" until the `metadata` event /
  `transcript_ready` or the next phase event. Only a 429 on
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

## Chunked Extraction (Long Videos)

Long videos are cut into chapter batches by the same chain on both paths; the paths
differ in WHEN they cut and how the batches run:

- **Field groups (default, `EXTRACTION_PARALLEL=true`; 3.7)** — `extraction_chunks.plan_chunks`
  cuts only when one batch can't cover the video (`needs_chapter_batching`: longer than
  `MAX_MINUTES_PER_BATCH` (40 min) or over `MAX_TOKENS_PER_BATCH` (50k) estimated tokens).
  Each batch becomes a chunk; the run's calls are chunks × groups under one
  `EXTRACTION_PARALLEL_BATCHES` semaphore, each chunk's text1 warming that chunk's head
  block. `CHUNKED_EXTRACTION_THRESHOLD` is not read.
- **Single call (`EXTRACTION_PARALLEL=false`)** — videos longer than
  `CHUNKED_EXTRACTION_THRESHOLD` (900 s) go through chapter splitting
  (`phases/extraction.py::_split_chapters`); the chapters are batched (one at a time). A
  video whose chapters fit one batch: ≤ 5,333 words → one call, else a force-split into
  `EXTRACTION_FORCE_SPLIT_CHUNKS` batches (overflow when that gives < 2 parts).

### Pipeline Flow

```
Field groups (default):
≤ 40 min and ≤ 50k tokens:  ONE chunk → its groups (1–5 text + ≤ 1 visual)
longer / larger:            chapters → batch_chapters (≤ MAX_TOKENS_PER_BATCH, ≤ MAX_MINUTES_PER_BATCH)
                            → one chunk per batch (≤ 1 batch → sentence force-split into enough parts
                            for both limits) → chunks × groups in parallel → chunk-major merge

EXTRACTION_PARALLEL=false:
≤ 15 min:              one extraction call (single/overflow)
> 15 min, one batch:   ≤ 5,333 words → one call; else force-split into EXTRACTION_FORCE_SPLIT_CHUNKS
                       → calls one at a time → merge (overflow when < 2 parts)
> 15 min, N batches:   chapters → batch_chapters (≤ MAX_TOKENS_PER_BATCH, ≤ MAX_MINUTES_PER_BATCH) → calls one at a time → merge
```

### Chapter Splitting (`transcript_chunker.py::split_transcript_into_chapters`)

Chunk texts are sliced from `ctx.prompt_segments` with the same source-language
cleaning as the single-call transcript. Chain (first usable result wins):
1. **YouTube chapters** — yt-dlp `video_data.chapters` (≥ 2)
2. **Description timestamps** — author-listed markers from the description analysis
   (≥ 2; awaited ≤ 5 s, else skipped)
3. **Content chapters, only when extraction needs > 1 batch** (`needs_chapter_batching`:
   duration > `MAX_MINUTES_PER_BATCH` or token estimate > `MAX_TOKENS_PER_BATCH`):
   the **memory outline** when it yields ≥ 2 chunks, else **AI chapter detection**
   (`chapter_detect.txt`, fast model, title/description sanitized, own
   `summarize:chapter_detect` feature tag). A video under both limits skips this step —
   chapters would buy one batch nothing.
4. **Time-based splitting** — ~5-minute segments
5. **Single chunk** — the whole transcript

Chapters from 1–3 longer than `MAX_MINUTES_PER_BATCH` are subdivided.

### Batched Extraction (`extractor.py`, `EXTRACTION_PARALLEL=false`)

The field groups reuse the batch transcript, `<batch_partial_context>` and annotation window
below for each chunk (`extraction_chunks._batch_chunks`); the rest of this list is the single
call's path.


- Groups chapters into batches of ≤50K tokens (`batch_chapters()`)
- `extractor.py` serves only today's path (`EXTRACTION_PARALLEL=false`), and there the batches run one at a time; `true` runs the phase-3 field groups instead (`phases/split_extraction.py`), whose calls share the run's `EXTRACTION_PARALLEL_BATCHES` semaphore
- Chapters that fit one batch: ≤ 5,333 words → one call (single); else force-split into `EXTRACTION_FORCE_SPLIT_CHUNKS` sub-batches (default 4) so a long single-batch video is not one huge call (on this path they run one after another); overflow when the split gives < 2 parts
- Force-split bypasses `batch_chapters()`: `_resolve_strategy` returns `one_chunk_per_batch=True` and `_chunked_extraction` builds `batches=[[c] for c in chunks]` so the sub-batches don't re-collapse under `MAX_TOKENS_PER_BATCH`
- **Batch-aware prompt**: each batch sends its own transcript slice in the cached head block and, in the job block, a `<batch_partial_context>` block from `_build_batch_context(batch_idx, total_batches, batch, full_duration_seconds)` — it tells the model it sees only a slice of the video and to `Return EMPTY arrays for fields not present in your segment — other batches cover them` — plus the batch's own slice of the visual annotations (no guide block when the slice is empty). Different slices mean batches share no cached prefix
- Per-batch progress streamed via `asyncio.Queue` → SSE `extraction_progress` with `batch`/`of`
- Rate-limit aware: batches whose LLM call raises `RateLimitError` / `ServiceUnavailableError` propagate up (via `call_llm_with_retry(propagate_rate_limit=True)`) and are queued for a sequential second pass with `_RATE_LIMIT_BACKOFF_SECONDS = 2.0`; sequential events carry `section="chunked-sequential"` so the UI can surface the fallback. Sequential retries inherit the same `batch_context` so the prompt stays consistent across attempts
- Chapter headers injected into transcript for better context

> **Phase 6 rationale (2026-05-14; the retry it mentions was removed in pipeline-1min 1c.5):** v6 verification on `K-mA3MZ_EzU` showed parallel batches scored 0.40 on the merged extraction (below `RETRY_SCORE_THRESHOLD=0.6`) because each batch saw a 1/4 transcript slice but was told via `<completeness>` to extract for the full 108-min video — so empty `steps[]` from a batch that genuinely had no steps in its slice looked like a coverage gap. The synthesis-fed retry then re-ran all 4 batches, doubling extraction cost ($0.41 → $0.92). The Phase 6 `<batch_partial_context>` block targets the root cause: the model is now explicitly told its input is partial and `DO NOT pad fields to meet "no empty array" or density quotas`, so the merged extraction reflects true field-presence and the retry only fires on genuine quality misses.

### Extraction Merger (`extraction_merger.py`, `EXTRACTION_PARALLEL=false`)

Per-domain merge logic for combining batch results (the field groups merge in
`group_merge.py` instead — chunk-major, first non-empty scalar; see 5 above):
- **Identity-based dedup** — name, label, term (case-insensitive)
- **Ordered list re-numbering** — step, order, number fields
- **Longest scalar selection** — keeps richest description/text
- **Recursive dict merge** — deep merge of nested structures

### Synthesis on long videos

No hierarchical mode any more: synthesis reads `<video_memory>` (built from the full
transcript) plus the compact final extraction (≤ 6,000 chars), whatever the length.

### Fast Model Routing

Model routing by stage (defaults from the pipeline-1min 4.0d A/B — rule: quality within
noise first, then latency, then cost; evidence per call type in
`dev/completed/pipeline-1min/gates/g4-model-ab.md` §Picks). The tier probe, description,
quiz and translation calls pass `use_fast_model=True` + `model_override` (fast-model
calls: no cross-provider fallback); memory and extraction pass `model_override` alone —
primary-model calls with an override, so the cross-provider fallback applies wherever
retries are allowed (extraction: P P F; memory's single attempt has none).
A setting left out of the environment keeps the compose / `config.py` default; the
"empty →" fallbacks below apply only to an empty value outside compose:
- Plan: primary model = Sonnet 5.5 at effort medium (60s timeout, 1 retry, 3,500 tokens; the cross-provider fallback applies). At effort low it flipped `is_learnable` on T1dQhQAm8Tc in both A/B runs (the quiz tab dropped)
- Tier probe (replaced the classifier): `LLM_CLASSIFIER_MODEL` = Haiku 4.5 (4s, no retry). Haiku 5.5 matched 16/18 golden visual-demo labels vs 18/18
- Memory: `LLM_MEMORY_MODEL` = Haiku 5.5, blank = `LLM_EXTRACTION_MODEL` in config.py only — under compose a blank line keeps Haiku 5.5 (25s, single attempt; a primary-model call with an override)
- Synthesis: the fast tier (`LLM_SYNTHESIS_MODEL` blank; 20s, 1 retry)
- Quiz (enrichment): `LLM_ENRICHMENT_MODEL` = gpt-4o-mini (14s per attempt, 30s total). gpt-6-luna was as usable, at twice the p50
- Description analysis: `LLM_DESCRIPTION_MODEL` = gpt-6-luna (25s per attempt, 30s total)
- Translation (output, title, RAG transcript): `LLM_TRANSLATION_MODEL` = gpt-6-luna
- Frame vision (`frame_analyzer.analyze_frames_with_vision`, ≤ 5 parallel batches; its own provider and retry): `LLM_VISION_MODEL` = Sonnet 5.5 (empty → the primary model). History: routed to fast in Phase 1B / P3, reverted 2026-05-14 after a 5-frame spot-check (`scripts/spotcheck_frame_vision.py`) found `openai/gpt-4o-mini` was only ~20% cheaper *and* hallucinated OCR on dense-text frames; in 4.0d Haiku 5.5 labelled cooking frames `talking_head` (dropped as presenter filler), Sonnet 5.5 read the card text Sonnet 4.6 reads at half the wall time
- AI chapter detection (`transcript_chunker._detect_chapters_with_ai`): the fast tier (`LLM_CHAPTER_DETECT_MODEL` is not passed by compose), scoped under its own `llm_feature_var.set("summarize:chapter_detect")` context so cost is attributed correctly in `llm_usage` (was previously absorbed by the outer `summarize:extraction` tag)
- Faithfulness judge: `LLM_JUDGE_MODEL` = gpt-6-luna, reasoning off (20s, 1 retry; a fast-tier call with an override — blank = the fast tier in config.py only, under compose a blank line keeps gpt-6-luna). Rollback: `openai/gpt-4o-mini`. Informational (D22); the golden baseline's faithfulness was re-scored under it (`gates/g4.md` §10). The hero + quiz sample (2.4) uses the same model through the same stage (`faithfulness`): 20 s, one attempt per call
- Extraction runs on `LLM_EXTRACTION_MODEL` = Sonnet 5.5 at effort low (empty → primary; a primary-model call with an override, like memory: the cross-provider fallback applies, P P F). The split path's group calls end their rules with the completeness rule (`extraction_group_rules.txt`, added once per run by `extraction_group_prompt.with_group_rules`; the `EXTRACTION_PARALLEL=false` call keeps today's bytes). A/B round 2: Sonnet 5.5 missed the fewest transcript-supported items and invented no details; Haiku 4.5 (the model before) invented steps, quantities and code snippets. The fast-first path and the synthesis-fed retry were removed in pipeline-1min 1c.3/1c.5.

### Prompt Safety Net (`llm_retry.py`)

Hard character limit per model before every LLM call (`MODEL_CHAR_LIMITS`):
- Sonnet 4.6 / 5.5, Haiku 4.5 / 5.5, gpt-6-luna: 600K chars; gpt-4o / gpt-4o-mini / gpt-5-mini: 380K; Gemini: 3M; any other model 300K
- Warning log when truncation triggers
- Prevents context overflow even without chunking

### Configuration (`config.py`)

| Setting | Default | Purpose |
|---------|---------|---------|
| `CHUNKED_EXTRACTION_THRESHOLD` | 900 (15 min) | Duration threshold for chunked path (`EXTRACTION_PARALLEL=false` only; the field groups chunk past `MAX_MINUTES_PER_BATCH` / `MAX_TOKENS_PER_BATCH`) |
| `MAX_TOKENS_PER_BATCH` | 50000 | Max tokens per extraction batch |
| `MAX_MINUTES_PER_BATCH` | 40 | Max minutes of video per extraction batch |
| `EXTRACTION_PARALLEL` | True | True: field groups (text groups at plan-done, the visual group at frames-done); False: today's single call / chunked batches one at a time, byte-identical request for the same plan — an extraction-only lever (reconcile, `<video_memory>` evidence ranges, toolkit markers and `PIPELINE_VERSION` stay; full rollback = redeploy the previous main) |
| `EXTRACTION_PARALLEL_BATCHES` | 6 | Max split-extraction group calls in flight — per run, so a worker holds up to this × `WORKER_CONCURRENCY` calls (today's path runs its batches one at a time) |
| `EXTRACTION_FORCE_SPLIT_CHUNKS` | 4 | Sub-batches when chunked input collapses to 1 batch (`EXTRACTION_PARALLEL=false` only) |

### Key Files

| File | Purpose |
|------|---------|
| `src/services/transcription/transcript_chunker.py` | Chapter splitting with fallback chain |
| `src/services/pipeline/phases/extraction.py` | `_split_chapters`: description timestamps + memory outline into the chain |
| `src/services/pipeline/extraction_merger.py` | Per-domain merge + dedup (today's path, `EXTRACTION_PARALLEL=false`) |
| `src/services/pipeline/group_merge.py` | Split path: per-group validation + key filter, the deterministic group merge, requested-field quality |
| `src/prompts/chapter_detect.txt` | AI chapter detection prompt |
| `src/utils/llm_retry.py` | Retry / cross-provider fallback per call (a refusal ends it), stage model overrides, prompt truncation |

---

## S3 Media Storage

All media (transcripts, frames, future audio) is stored in one S3 bucket, `S3_BUCKET` — `vie-transcripts` on dev (the compose default); production names its own bucket and must set `AWS_REGION` to that bucket's region (prod compose refuses to start without it; [DEPLOY.md](./DEPLOY.md)).

### Architecture

```
S3_BUCKET
└── videos/{youtube_id}/
    ├── transcript.json       ← processed transcript
    ├── scenes-v4/            ← SCENE_S3_PREFIX (default scenes-v4)
    │   ├── manifest.json     ← manifest v2: timestamps, galleryIndices, hiresCount, visionDescriptions
    │   └── scene_{idx}.jpg   ← selected scene frames (720p)
    ├── scenes-v3/, scenes-v2/, scenes/
    │   └── scene_{idx}.jpg   ← legacy prefixes: pre-pipeline-1min selection (v3),
    │                           pre-adaptive-vision (v2), pre-hires low-res (kept for old summaries)
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
│         + caption {lang, kind} for a yt-dlp transcript │
│     └─▶ Overwritten when a stale blob is replaced      │
│                                                         │
│  2. Upload Video Frames                                 │
│     └─▶ Scene frames: "videos/{id}/{SCENE_S3_PREFIX}/   │
│         scene_{idx}.jpg" (default scenes-v4)            │
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
└─────────────────────────────────────────────────────────┘
```

### Design Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| Single bucket | `S3_BUCKET` for all media (`vie-transcripts` on dev) | Simpler ops, per-video folder isolation |
| Presigned URLs | All access via signed URLs | Security: private bucket, time-limited access |
| `s3_key` in MongoDB | Store key, not URL | URLs change (expiry), key is permanent |
| Sync presigning | No async for URL generation | Local crypto operation (HMAC-SHA256), no network call |
| Lazy S3 initialization | aiobotocore imported on first use | Container starts without S3 dependency |
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

Health check endpoint. `model` = the primary model (`settings.llm_model`) — the plan's; the
other stages' models are the per-stage settings.

**Response:**
```json
{
  "status": "healthy",
  "service": "summarizer",
  "model": "anthropic/claude-sonnet-5-5",
  "database": "connected",
  "s3": "healthy"
}
```
