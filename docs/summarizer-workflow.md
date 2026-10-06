# Summarizer Pipeline — Complete Workflow

> How a YouTube URL becomes an interactive knowledge app. This traces **who calls
> what, in what order, which prompt file each stage loads, and how data flows from
> step to step** until it becomes the final stored result.
>
> Service: `vie-summarizer` (Python + FastAPI + LiteLLM), port 8000.
> All paths below are relative to `services/summarizer/`.

---

## Table of contents

1. [Entry points — who triggers it](#1-entry-points--who-triggers-it)
2. [The orchestrator + the shared context](#2-the-orchestrator--the-shared-context)
3. [Phase order at a glance](#3-phase-order-at-a-glance)
4. [Phase-by-phase: who calls what, which prompt, data in → out](#4-phase-by-phase)
5. [The data-flow narrative — "who feeds who"](#5-the-data-flow-narrative--who-feeds-who)
6. [Prompt files → stage map](#6-prompt-files--stage-map)
7. [The three pivot artifacts](#7-the-three-pivot-artifacts)
8. [End-to-end scenario walkthroughs](#8-end-to-end-scenario-walkthroughs)
9. [SSE event timeline](#9-sse-event-timeline)

---

## 1. Entry points — who triggers it

There are **two ways** the pipeline starts, but both converge on the *exact same code*:

| Path | Trigger | Code |
|------|---------|------|
| **Queue (production)** | API gateway publishes a `VideoJobPayload` to RabbitMQ → `vie-summarizer-worker` consumes it | `worker/runner.py:WorkerRunner.process_message` → `worker/pipeline.py:drive_pipeline` |
| **SSE direct (dev / provider override)** | Browser hits `GET /summarize/stream/{video_summary_id}` | `routes/stream.py:stream_summary` |

Both funnel through a **Redis broker lock** (`pipeline_event_stream.acquire_lock`) so that
*no matter how many SSE clients or worker messages arrive for the same
`video_summary_id`, the pipeline runs exactly once*. The winner becomes the
**producer**; everyone else **consumes** the shared Redis event stream
(`routes/pipeline_broker.py`). This is why a React StrictMode double-mount, three
open tabs, and a worker job all collapse to a single pipeline run.

The producer calls **`stream_summarization()`** (`routes/pipeline_runner.py:264`), which:

1. Reads `youtubeId` + `userId` + `requestId` from the Mongo `entry` and structlog contextvars.
2. Sets LLM cost-tracking contextvars (`llm_video_id_var`, `llm_user_id_var`, `llm_request_id_var`, `llm_video_summary_id_var`) — so every `llm_usage` row this run writes is attributable to user/video/run/request.
3. Opens the **Langfuse parent trace** (`pipeline_trace`) — opened *before* the cache lookup so even cache hits are observable.
4. **Redis cache check**: if `response_cache.get_response(youtube_id)` returns a completed payload → stream it instantly (`$0.00`, the "same video = instant serve") and return. (Skipped when `force_refresh=True` — set by the dev provider-override flow, by `bypassCache=true` submissions via the worker payload, or by the `forceRefresh` flag the api stamps on the version row.)
5. On miss → sets Mongo status `PROCESSING`, builds the **`PipelineContext`**, and calls **`_run_pipeline_phases()`** (`routes/pipeline_runner.py:143`).

---

## 2. The orchestrator + the shared context

`PipelineContext` (`services/pipeline/context.py`) is the spine of "who feeds who".
There is **no return-value chaining** between phases — the context *is* the data bus.
Each phase **reads fields off `ctx` and writes its outputs back onto `ctx`**:

```python
@dataclass
class PipelineContext:
    # ── Immutable inputs (set once at construction) ──
    video_summary_id, youtube_id, entry, repository, llm_service, timer

    # ── Phase outputs (each phase fills its slice) ──
    video_data          # ← metadata
    transcript_data, clean_text, source_language_code   # ← transcript
    scene_frames_*, frame_descriptions                  # ← frames
    content_format, content_traits, plan_result, triage # ← plan
    extraction_data, extraction_coverage, chapters      # ← extraction
    synthesis_dict                                      # ← synthesis
    enrichment_data                                     # ← enrichment
    assembled_tabs, assembled_meta                      # ← assembly
    source_language                                     # ← translation
    phase_times                                         # ← timing for every phase
```

`_run_pipeline_phases` drives every phase in order, records `ctx.phase_times[...]`
for each, and at the end drains any fire-and-forget faithfulness tasks before the
Langfuse trace flushes. Each phase is an **async generator** that mutates `ctx` and
`yield`s SSE event strings up to the broker.

---

## 3. Phase order at a glance

```
Phase 1   metadata           (sequential — everything needs video_data)
Phase 2   transcript ║ frames    (PARALLEL — both only need youtube_id + video_data)
Phase 2.5 visual injection    (folds frame captions into the transcript text)
          ↳ right after Phase 2 the runner writes `transcriptMeta` (ok AND failed runs)
Phase 3   plan               ┐
Phase 4   extraction         │
Phase 5   synthesis          │  (sequential — each depends on the previous's output)
Phase 6   enrichment         │
Phase 7   assembly           ┘
Phase 8   translation        (ONLY if ctx.source_language_code is set — non-English)
```

`phases/__init__.py` is lazy-import indirection (keeps heavy deps like `yt_dlp` out
of test collection). `run_phase_plan` is aliased `run_phase_triage` for backward-compat.

The parallel block (Phase 2) uses `run_parallel_phases()` (`pipeline_helpers.py`),
an `asyncio.Queue` that streams events in arrival order and emits a `heartbeat`
event every `SSE_HEARTBEAT_SECONDS` of silence — otherwise a multi-minute Whisper
run sends zero bytes and the API gateway's undici proxy aborts the idle SSE
connection at its 300s body timeout.

---

## 4. Phase-by-phase

### Phase 1 — Metadata · `phases/metadata.py`

- **Calls:** `youtube.extract_video_data(youtube_id)` (yt-dlp), then `description_analyzer.analyze_description()`.
- **Prompt:** `prompts/description_analysis.txt` (fast model — Haiku).
- **In:** `youtube_id`.
- **Out:** `ctx.video_data` (title, channel, duration, chapters, subtitles, tags, description, thumbnail), `ctx.description_analysis` (incl. description **timestamps** reused later for chapter detection).
- **Gate:** `validate_duration()` — rejects too-long / too-short videos.
- **SSE:** `metadata`, `description_analysis`.

### Phase 2a — Transcript · `phases/transcript.py`

- **Calls:** `transcript_fetcher.fetch_transcript()` — a **fallback chain** (`transcript_fetcher.py:52`):
  ```
  S3 cache → yt-dlp subtitles → youtube-transcript-api → Whisper (audio) → Gemini (audio) → metadata-only (music)
  ```
  Whisper is tried *before* Gemini because it natively reports `response.language`.
- Then `clean_transcript()` → `clean_transcript_advanced()` (spaCy + TF-IDF in a process pool, 30s cap) → **SponsorBlock** filtering (`sponsorblock.get_sponsor_segments` + `filter_transcript_segments`).
- **Language detection:** from the source's own metadata, or `detect_language_from_text` / `detect_language_by_script`. Sets `ctx.source_language_code` (non-English only; **dropped** for sound-only/instrumental music whose "transcript" is hallucinated). **This single field decides whether Phase 8 runs.**
- **Out:** `ctx.transcript_data` (segments + raw_text + source), `ctx.clean_text`, `ctx.source_language_code`.
- **Provenance:** the fetcher fills a `TranscriptTrail` (layers that ran and failed — the youtube-transcript-api attempt is labelled `api` or `proxy` by whether `YOUTUBE_PROXY_URL` is set — the caption-429 skip flag, the S3 blob's origin) and attaches it to the yielded `TranscriptData`; the phase times the fetch and stamps `fetch_wall_ms` / `error_code` in a `finally`, so `ctx.transcript_trail` exists even when the chain raises.
- **SSE:** `transcript_ready` (+ `phase` events for each fallback hop).

### Phase 2b — Frames · `phases/frames.py`

- **Calls:** `scene_extractor.extract_scene_keyframes()` — manifest-v2 S3 cache check first (`scenes-v3/manifest.json`; `hiresCount == 0` counts as a miss so 403-era low-res runs self-heal); on miss: yt-dlp worst-quality download + FFmpeg scene detection + local frame scoring (pass 1), adaptive visual tier (`visual_tier.py`), then a 720p hi-res re-extraction of the selected frames (`hires_refiner.py`: stream-URL seeks with a `local_video.py` download fallback; with `YOUTUBE_PROXY_URL` set, `hires_prefetch.py` instead starts the 720p download right after pass 1 and the refiner seeks that local file) — then **in parallel**:
  - `scene_frames.process_scene_frames()` — OCR + S3 presigned URLs,
  - `_run_vision_analysis()` → `frame_analyzer.analyze_frames_with_vision()` (descriptions persisted back into the manifest).
- **Prompt:** vision prompt inside `frame_analyzer` (vision model — Haiku-4.5), gated by `FRAME_VISION_ENABLED`.
- **Out:** `ctx.scene_frames_all`, `ctx.scene_frames_gallery`, `ctx.scene_frames_for_assembly`, `ctx.frame_descriptions` (`[VISUAL at M:SS]` captions).
- **Non-critical:** any failure logs a warning and the pipeline continues without frames.

### Phase 2.5 — Visual Injection · `pipeline_runner.py:173`

- **Calls:** `scene_frames.inject_visual_context()`.
- **Feeds:** frame descriptions + OCR text **into** `ctx.clean_text` as `[VISUAL at M:SS]` / `[ON-SCREEN TEXT at M:SS]` annotations, positioned by segment timestamp. This is the bridge by which the frames phase *feeds* the text pipeline — extraction later "sees" what was on screen.

### Phase 3 — Plan · `phases/triage.py` → `pipeline/plan.py` + `pipeline/classifier.py`

The **brain**. Replaces the old manifest+triage 2-call flow with a single Sonnet
call, preceded by a fast classifier.

- **3a. Classifier:** `classify_domain_format()`
  - **Prompt:** `prompts/classify.txt` (fast model, ~$0.001, 10s timeout).
  - Returns domain (14 valid), format (17 valid), confidence, and 8 boolean **`ContentTraits`** (`has_steps`, `has_code`, `has_comparison`, `has_drills`, `has_narrative`, `has_visual_demo`, `is_opinionated`, `is_list`). If confidence > 0.6 it overrides the rule-based category hint.
- **3b. Plan:** `run_plan()`
  - **Prompt:** `prompts/plan.txt` + injected `prompts/component_toolkit.txt` (Sonnet, 60s timeout, JSON mode, 2 retries).
  - `{valid_components}` and `{density_gates}` are generated from `domains.json` via `domain_config` renderers — edit `domains.json` to change what components the LLM may pick.
  - The prompt is split at the `<video>` marker for **Anthropic prompt caching**: everything before it (role + toolkit + density gates + schema + examples + rules) is the cached static prefix; only the small video + transcript-preview blocks are dynamic per request.
- **In:** title, channel, description, duration, category_hint, content_format, traits summary, first 3K chars of `clean_text`.
- **Out:** `ctx.plan_result` (`PlanResult`): `contentTags`, `modifiers`, `primaryTag`, `userGoal`, and the **`tabs[]`** — each tab is `{id, label, emoji, component, dataSource, goal, outboundLinks}`. Tabs are validated against the real component registry (`_validate_tabs`; invalid components fall back to `infer_component`). Also builds `ctx.video_dna_compact` (~300 chars) injected into every downstream LLM stage, and populates `ctx.triage` / `ctx.triage_dict` for backward-compat.
- **Fallback:** on LLM failure or confidence < 0.6 → `_build_fallback_plan()` (rule-based tabs).
- **SSE:** `triage_complete`, `meta` (tab labels for progressive rendering).

### Phase 4 — Extraction · `phases/extraction.py` → `pipeline/extractor.py`

The most expensive stage. **Builds the prompt** via `prompt_builder.build_extraction_template()`:

- **Base:** `prompts/base_extraction.txt`
- **+ per-domain schemas:** `prompts/schemas/{tag}.txt` for each content tag + modifier (16 files: tech, food, learning, science, gaming, sport, news, podcast, travel, fitness, music, review, project, language, narrative, finance)
- **+ quality rules:** `prompts/quality_rules.txt`
- **+ a domain example:** `prompts/examples/{primary_tag}.txt` (falls back to `learning.txt`)
- **+ frame captions:** `format_gallery_frames_for_extraction()` folds up to 12 captioned frames so the LLM can ground visual claims and warrant a filmstrip/diagram.

**Strategy selection** (`_resolve_strategy`) by transcript length & duration:

| Strategy | When | How |
|----------|------|-----|
| **single** | < 5,333 words (~7K tokens) | one LLM call |
| **overflow** | medium/long, no usable chapters | one call, dynamic timeout `300 + words/100` capped 600s |
| **chunked** | > 30 min with chapters | `split_transcript_into_chapters()` (3-tier: YouTube chapters → description timestamps → whole-transcript AI detect) → `batch_chapters()` (capped by tokens **and** `MAX_MINUTES_PER_BATCH`) → **parallel** batches under a semaphore with rate-limit sequential fallback → `merge_batch_extractions()` |

Each call uses `call_llm_with_retry(..., json_mode=True, cache_static=...)` (Sonnet
primary; `<transcript>` marker splits cacheable static prefix from dynamic body).
Output is validated by `validate_domain_output()` against `models/domain_types.py`
Pydantic models.

- **Out:** `ctx.extraction_data` — the structured, per-domain facts (the raw material for everything downstream).
- **Then:**
  - `compute_extraction_coverage()` → `ctx.extraction_coverage` (how far the timestamped output reaches vs. duration; flags critically-low coverage = truncated transcript).
  - `check_extraction_quality()` + `validate_extraction_counts()` → if quality is low or a *planned* field came back empty → **synthesis-fed retry** (`_attempt_synthesis_fed_retry`): runs synthesis early, feeds its evidence into a re-extraction forced onto the primary model; keeps the better result.
- **Side effect:** once extraction has data, `_launch_faithfulness_check()` fires a **fire-and-forget judge** that scores extraction faithfulness vs transcript and logs to Langfuse (re-tagged `summarize:faithfulness` for correct cost attribution).
- **SSE:** `extraction_progress` (per batch), `extraction_complete`.

### Phase 5 — Synthesis · `phases/synthesis.py` → `pipeline/synthesis.py`

- **Prompt:** `prompts/synthesis.txt` (fast model, JSON mode, 30s).
- **In:** truncated `ctx.extraction_data` (or a hierarchical chapter-summary input for >5 chapters) + `video_dna_compact` + `primaryTag`.
- **Out:** `ctx.synthesis_dict` — `tldr`, `keyTakeaways`, `masterSummary`, `seoDescription`. (Skipped if the extraction retry already populated it.)
- **SSE:** `synthesis_complete`.

### Phase 6 — Enrichment · `phases/enrichment.py` → `pipeline/enrichment.py`

- **Prompt:** `prompts/enrich/{file}.txt`, chosen by `ENRICHMENT_MAP` (from `domains.json`) keyed on `primaryTag` (falls back to other content tags). Only eligible domains run it. Fast model, 90s.
- **In:** `ctx.extraction_data` (or `synthesis_dict` as fallback if extraction was empty) + tab goals + `video_dna_compact`.
- **Out:** `ctx.enrichment_data` — `quiz`, `flashcards`, `scenarios` (hard-capped at 12 / 15 / 6 regardless of prompt drift).
- **SSE:** `enrichment_complete`.

### Phase 7 — Assembly · `phases/assembly.py` → `pipeline/assembly/core.py:assemble_response`

**Pure code, no LLM.** Where all the streams *converge* into the final shape.

- **In:** `triage_dict` (the tab plan) + `extraction_data` + `enrichment_data` + `synthesis_dict` + `video_meta` + frames + description analysis.
- **Per tab** in the plan:
  - `resolve_data_source()` maps the tab's `dataSource` (e.g. `"tech.codeSnippets"`) to the actual extracted data, with **cascading fallbacks**: cross-domain → in-domain sibling (populated field backing the same component) → YouTube chapters (for an empty `moment_track`).
  - The matching assembler from `ASSEMBLER_REGISTRY` (30+ assemblers: `concept_canvas`, `connect_canvas`, `step_flow_canvas`, `comparison_radar`, `code_playground`, `quiz_arena`, `packing_mission`, `workout_room`, `lyrics_karaoke`, `video_filmstrip`, `claims_tracker`, `tier_list`, `formation_diagram`, …) turns the data into component props.
- **Post-processing per tab:** `promote_component()` (upgrade to a richer component now that item counts / connection graphs are known), `enforce_density()` (caps), `_validate_assembled_props()` (drops tabs with empty required lists). Then `resolve_cross_tab_links()`, frame-thumbnail injection, and a guaranteed **min-3-tab** fallback.
- **Out:** `ctx.assembled_tabs`, `ctx.assembled_meta` (`meta` is a superset of synthesis + the coverage metric).
- **Persists:** `repository.save_structured_result()` writes `tabs` + `meta` + the full `pipeline` debug record to Mongo. Status = `completed` for English, **`processing`** for non-English (translation owns the final transition).
- **Caches / indexes (all background, best-effort):**
  - Redis `response_cache.set_response()` — **English only** (non-English waits for translation so the cached payload includes the toggle).
  - Qdrant `store_transcript_chunks()` + `store_default_output_chunks()` — transcript translated to English for embeddings; output tabs are already English.
  - S3 raw transcript via `transcript_store.store()` — **skipped when the transcript itself came from S3** (`source == "s3"`), so the blob keeps recording which layer originally produced it. The `rawTranscriptRef` write-back that follows is currently a no-op (its filter passes the string id where Mongo stores an `ObjectId`; known side finding, not fixed here).
- **SSE:** `tab_ready` (one per tab, progressive), `complete`, and for English: `done` + `[DONE]`.

### Transcript provenance write · `routes/pipeline_runner.py`

- Wraps the parallel transcript+frames phase in `try/finally`; the `finally` calls `_record_transcript_outcome()`, which builds the block with `transcription/transcript_meta.build_transcript_meta()` and persists it through `repository.set_transcript_meta()` — a dedicated `$set`-only writer (`save_structured_result` would `$unset` the `forceRefresh` bypass marker mid-run).
- Runs for successful **and** failed fetches (a `TranscriptError` still propagates afterwards) and lives inside the Langfuse `pipeline_trace`, mirroring `transcriptSource` / `transcriptType` / `transcriptAttempted` / `transcriptOutcome` onto the trace.
- `clear_transcript_meta()` runs at the `processing` transition and on the Redis fast path, so a row re-run on the same `_id` never keeps a stale block. Field shape: [DATA-MODELS.md](./DATA-MODELS.md).

### Phase 8 — Translation · `phases/translation.py` → `pipeline/translation.py` *(non-English only)*

- **Prompt:** `prompts/translate_flat.txt` (batched Haiku calls).
- **Calls:** `translate_to_source()` — walks the **English** assembled output, translates every translatable prose string into `source_language_code`, returns the unchanged English dict with the translated artifact nested under `sourceLanguage`. Plus `_translate_title()` (the one field that travels source→English; original kept under the source block).
- **On failure / mirror / length mismatch:** the result has no `sourceLanguage` key → phase no-ops, finalizes the doc `completed` English-only (FE renders no toggle).
- **Out:** `ctx.source_language`; re-saves the Mongo doc with `sourceLanguage` + status `completed`; writes the final Redis payload (English primary + toggle).
- **SSE:** `phase:translation`; then the **runner** (not the phase) emits the deferred `done` + `[DONE]`.

---

## 5. The data-flow narrative — "who feeds who"

```
youtube_id
  └─ metadata ──────────────► video_data (title, duration, chapters, description)
                                  │
        ┌─────────────────────────┴──────────────────────────┐
        ▼                                                      ▼
   transcript                                              frames
   (fallback chain →                                  (FFmpeg + OCR + vision)
    clean_text, segments,                                  │
    source_language_code)                          frame_descriptions
        │                                                  │
        └──────────────► visual injection ◄───────────────┘
                         (clean_text now carries [VISUAL at M:SS])
                                  │
                  classifier ──► domain / format / traits
                                  │
                              PLAN (Sonnet) ──► plan_result.tabs   ← THE BLUEPRINT
                                  │             (each tab names a component + dataSource)
                                  ▼
                          EXTRACTION (Sonnet, schema-driven)
                          clean_text + frames + tabs ──► extraction_data   ← THE FACTS
                                  │
            ┌──────────┬──────────┼─────────────┐
            ▼          ▼          ▼             ▼
        SYNTHESIS  ENRICHMENT  faithfulness   coverage metric
        (tldr,     (quiz,      judge          (how much covered)
         summary)   flashcards) (background)
            │          │
            └────┬─────┘
                 ▼
            ASSEMBLY (pure code)
            tabs(plan) × extraction × enrichment × synthesis × frames
                 ▼
            assembled_tabs + assembled_meta   ← THE PRODUCT
                 │
        ┌────────┼─────────┬──────────┐
        ▼        ▼         ▼          ▼
      Mongo    Redis    Qdrant       S3
     (doc)    (cache)  (RAG chunks) (raw transcript)
                 │
         (non-English?) ──► TRANSLATION ──► sourceLanguage block ──► re-save + cache
                 ▼
            SSE: done → frontend refetches the completed Mongo doc
```

**The one flag that branches the whole tail:** `ctx.source_language_code`.
English → assembly finalizes, caches, emits `done`. Non-English → assembly leaves the
doc `processing` and translation owns completion (so an interrupted translation stays
retriable instead of fake-completed English-only).

---

## 6. Prompt files → stage map

All prompts load through `prompt_builder.load_prompt_text()`, which is
**registry-first**: it tries the Langfuse-registered version (`summarizer:<name>`)
and falls back to the on-disk `.txt` (cached via `_read_file_cached`), recording the
prompt version on the active trace.

| Prompt file | Stage | Model |
|-------------|-------|-------|
| `description_analysis.txt` | metadata | fast |
| `classify.txt` | plan / classifier | fast |
| `plan.txt` + `component_toolkit.txt` | plan | Sonnet (cached) |
| `base_extraction.txt` + `schemas/*.txt` + `quality_rules.txt` + `examples/*.txt` | extraction | Sonnet (cached) |
| `chapter_detect.txt` | extraction (long-video chapter split) | Sonnet |
| `synthesis.txt` | synthesis | fast |
| `enrich/*.txt` | enrichment | fast |
| `detection/*.txt` | faithfulness judge (background) | fast |
| `translate_flat.txt` | translation | Haiku (batched) |

---

## 7. The three pivot artifacts

Everything else is plumbing around these three:

1. **`plan_result.tabs`** — *the blueprint.* The plan decides **what components exist
   and what data each needs**, before any data is extracted. (`dataSource` is a
   promise the assembler later tries to fulfil.)
2. **`extraction_data`** — *the facts.* Schema-driven structured JSON keyed by domain
   (`tech.codeSnippets`, `food.ingredients`, `learning.concepts`, …).
3. **`assembled_tabs`** — *the product.* Pure-code marriage of blueprint + facts +
   synthesis + enrichment + frames into renderable component props, persisted to Mongo
   and cached in Redis.

---

## 8. End-to-end scenario walkthroughs

Concrete traces showing exactly which branch fires for common inputs.

### Scenario A — Short English coding tutorial (cache miss, single extraction)

> *"React useEffect in 8 minutes"* — 8-min English video with yt-dlp captions, code on screen.

| Step | What happens |
|------|--------------|
| Entry | API queues job → worker wins Redis lock → `stream_summarization` |
| Cache | Redis miss → status `PROCESSING`, build `ctx` |
| **Metadata** | yt-dlp → `video_data` (8 min, has captions). `analyze_description` finds no timestamps. Passes `validate_duration`. |
| **Transcript ‖ Frames** | Transcript: **Priority 1 yt-dlp subtitles** hit immediately (no Whisper/Gemini). Language = `en` → `source_language_code = None`. Frames: FFmpeg pulls keyframes, vision captions the code editor → `[VISUAL at 2:13] terminal showing npm install`. |
| **Visual inject** | Code-screen captions woven into `clean_text`. |
| **Plan** | Classifier → `domain=tech, format=tutorial, traits=[has_code, has_steps]` (confidence 0.9). Plan (Sonnet) → tabs like `overview`, `code` (`component=code_playground`, `dataSource=tech.codeSnippets`), `steps` (`step_flow_canvas`). |
| **Extraction** | ~1,100 words < 5,333 → **single** strategy, one Sonnet call. `extraction_data = {tech: {codeSnippets:[…], commands:[…]}}`. Coverage ≈ 1.0. Quality good → **no retry**. Faithfulness judge fires in background. |
| **Synthesis** | `tldr`, 5 `keyTakeaways`, `masterSummary`. |
| **Enrichment** | `tech` is enrichment-eligible → `enrich/tech.txt` → quiz + flashcards. |
| **Assembly** | `code` tab resolves `tech.codeSnippets` → `code_playground` props; frame thumbnails injected; cross-tab links added. Status `completed`. Saved to Mongo, cached in Redis, chunks to Qdrant, raw transcript to S3. |
| **Translation** | Skipped (`source_language_code is None`). |
| End | `done` + `[DONE]`. Total ~4–7 LLM calls. |

### Scenario B — The same video, second viewer (cache HIT)

> Another user submits the **same** YouTube ID minutes later.

| Step | What happens |
|------|--------------|
| Entry | `stream_summarization` opens the Langfuse trace (so the hit is counted). |
| **Cache** | `response_cache.get_response(youtube_id)` returns the completed payload, integrity-checked (`status=completed`, non-empty `tabs`, non-empty `meta`). |
| Trace | `update_trace_metadata({cacheHit: True})`. |
| DB | If this user's doc isn't `completed`, a **fire-and-forget** `save_structured_result` copies it (doesn't block). |
| Stream | `_stream_cached_structured` replays the stored tabs as SSE. **Zero LLM calls, $0.00.** No metadata/transcript/plan/extraction runs at all. |

### Scenario C — 90-minute Hebrew lecture (no captions → chunked → translation)

> Hebrew university lecture, no YouTube captions, native chapters in the description.

| Step | What happens |
|------|--------------|
| **Metadata** | `video_data` (90 min, **no** subtitles). `analyze_description` extracts description **timestamps** (saved for chapter detection). |
| **Transcript** | yt-dlp subtitles absent → youtube-transcript-api fails → **Whisper** audio fallback (budget scales with duration, returns partial-but-real on deadline). Whisper reports `language=he`. → `source_language_code = "he"`. |
| **Frames** | Slides captioned by vision → injected. |
| **Plan** | Classifier → `domain=learning, format=lecture`. Plan → `overview`, `concepts` (`concept_canvas`), `timeline` (`moment_track`), `quiz`. |
| **Extraction** | 90 min > 30 min **and** chapters available → **chunked**. `split_transcript_into_chapters` (Tier 2 description timestamps) → `batch_chapters` (token + `MAX_MINUTES_PER_BATCH` caps) → e.g. 5 parallel Sonnet batches under a semaphore. Each batch gets a `batch_partial_context` block ("return empty arrays for what's not in your slice — don't pad"). `merge_batch_extractions` stitches them. Coverage checked across the full duration. |
| **Synthesis** | >5 chapters → **hierarchical** input (chapter summaries + truncated extraction). |
| **Enrichment** | `learning` eligible → quiz + flashcards (in English). |
| **Assembly** | Tabs assembled in **English**. Status saved as **`processing`** (not completed!). Redis caching **skipped**. Qdrant output indexed in English. Emits `complete` but **not** `done`. |
| **Translation** | `source_language_code="he"` → runs. `translate_to_source` walks the English tabs → Hebrew strings nested under `sourceLanguage` (RTL). Title translated both ways. Re-saves Mongo with `sourceLanguage` + status **`completed`**, writes final Redis payload (English primary + toggle). |
| End | The **runner** emits the deferred `done` + `[DONE]`. FE refetches → completed doc with a Hebrew/English toggle. |

> If translation had crashed mid-way, the doc stays `processing` → retriable, never a fake-completed English-only result.

### Scenario D — Instrumental music video (sound-only → metadata fallback)

> A lo-fi beats video: `category=music`, no real speech.

| Step | What happens |
|------|--------------|
| **Transcript** | `is_music=True`. yt-dlp subtitles absent → api fails → Whisper/Gemini return hallucinated foreign fragments. `is_sound_only_video()` (low words-per-second) → **`source_language_code` dropped to None** (no bogus translation, no toggle). All audio sources exhausted → **metadata fallback**: `build_metadata_text(video_data)` becomes `raw_text`, segments empty. |
| **Frames** | Album art / visualizer captioned. |
| **Plan** | `domain=music`. Tabs like `lyrics` (`lyrics_karaoke`), `overview`. |
| **Extraction** | Tiny `raw_text` → **single** call over metadata only (title, channel, tags, description). |
| **Synthesis / Enrichment / Assembly** | Run normally on the thin data; min-3-tab guarantee ensures a usable layout. |
| **Translation** | Skipped (`source_language_code is None`). |

### Scenario E — Long English video, extraction comes back thin (retry)

> A rambling 50-min English talk; first extraction under-fills a planned field.

| Step | What happens |
|------|--------------|
| **Extraction** | overflow/chunked runs → `extraction_data` produced, but `validate_extraction_counts` finds the plan promised "6 key points" and extraction returned 0 (a *hard miss*), or `check_extraction_quality` score is low. |
| **Retry decision** | `decide_extraction_retry` → `should_retry=True`. |
| **Synthesis-fed retry** | `_attempt_synthesis_fed_retry`: runs **synthesis early** to get evidence, builds a targeted retry prompt naming the empty fields, re-extracts **forced onto the primary model** (`force_primary_model=True`, ignoring `EXTRACTION_USE_FAST_FIRST`). Keeps the retry result only if its quality score improves. |
| **Synthesis (phase 5)** | Sees `ctx.synthesis_dict` already populated by the retry → **skips** the redundant call. |
| Rest | Continues normally. |

---

## 9. SSE event timeline

The order of events a frontend client observes for a typical English cache-miss run:

```
metadata                → title/channel/thumbnail/duration  (instant, ~1s)
description_analysis    → (if description has content)
phase: transcript…      → fallback-hop markers
transcript_ready
[heartbeat]*            → during any long silent phase
triage_complete         → domains + tab plan
meta                    → tab labels (progressive shell renders now)
extraction_progress*    → per batch / percent
extraction_complete
synthesis_complete
enrichment_complete
tab_ready*              → one per assembled tab (progressive fill)
complete                → tabCount + processingTimeMs
done + [DONE]           → English: from assembly │ non-English: from runner after translation
```

`error` events (with an `ErrorCode`) replace the tail on `TranscriptError`,
`RateLimitError`, `Timeout`, `APIError`, or any unexpected exception; the Mongo doc
is set `FAILED` with the code.

---

### Source map (quick reference)

| Concern | File |
|---------|------|
| Entry / cache / trace | `routes/pipeline_runner.py` |
| HTTP route + broker dispatch | `routes/stream.py`, `routes/pipeline_broker.py` |
| Worker (queue) | `worker/runner.py`, `worker/pipeline.py` |
| Shared state | `services/pipeline/context.py` |
| Phase generators | `services/pipeline/phases/*.py` |
| Parallel runner + SSE helpers | `services/pipeline/pipeline_helpers.py` |
| Transcript fallback chain | `services/transcription/transcript_fetcher.py` |
| Plan + classifier | `services/pipeline/plan.py`, `classifier.py` |
| Prompt assembly + registry loader | `services/pipeline/prompt_builder.py` |
| Extraction strategies | `services/pipeline/extractor.py` |
| Assembly orchestrator + assemblers | `services/pipeline/assembly/core.py`, `assemblers.py` |
| Prompts | `prompts/*.txt`, `prompts/schemas/`, `prompts/enrich/`, `prompts/examples/` |
