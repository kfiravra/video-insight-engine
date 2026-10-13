# Summarizer Pipeline — Complete Workflow

> How a YouTube URL becomes an interactive knowledge app. This traces **who calls
> what, in what order, which prompt file each stage loads, and how data flows from
> step to step** until it becomes the final stored result — the v9 pipeline
> (`PIPELINE_VERSION` v9, pipeline-1min phases 1–3).
>
> Service: `vie-summarizer` (Python + FastAPI + LiteLLM), port 8000.
> All paths below are relative to `services/summarizer/src/`. Settings, timeouts and
> the per-step detail live in [SERVICE-SUMMARIZER.md](./SERVICE-SUMMARIZER.md); the
> SSE contract in [API-REFERENCE.md](./API-REFERENCE.md#tab_ready); the stored
> `pipeline.*` record in [DATA-MODELS.md](./DATA-MODELS.md).

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
| **Queue (production)** | API gateway publishes a `VideoJobPayload` to RabbitMQ → `vie-summarizer-worker` consumes it | `worker/runner.py:WorkerRunner.process_message` → `worker/pipeline.py:drive_pipeline` (skips a row already completed by the current `PIPELINE_VERSION`, before and after taking the lock) |
| **SSE direct (dev / provider override)** | Browser hits `GET /summarize/stream/{video_summary_id}` | `routes/stream.py:stream_summary` |

Both funnel through a **Redis broker lock** (`pipeline_event_stream.acquire_lock`) so that
*no matter how many SSE clients or worker messages arrive for the same
`video_summary_id`, the pipeline runs exactly once*. The winner becomes the
**producer** (`routes/pipeline_broker.py:produce_to_broker`); everyone else
**consumes** the shared Redis event stream (`consume_from_broker`). Winning the lock
also deletes the previous run's stream, so a re-run never replays old events. This is
why a React StrictMode double-mount, three open tabs, and a worker job all collapse to
a single pipeline run.

The producer calls **`stream_summarization()`** (`routes/pipeline_runner.py`), which:

1. Reads `youtubeId` + `userId` + `requestId` from the Mongo `entry` and structlog contextvars; `evalRun` (eval-user row, D25) and `coldMedia` (admin/eval benchmark: skip the S3 transcript + scene-frame caches) from the row.
2. Sets LLM cost-tracking contextvars (`llm_video_id_var`, `llm_user_id_var`, `llm_request_id_var`, `llm_video_summary_id_var`) — so every `llm_usage` row this run writes is attributable to user/video/run/request.
3. Opens the **Langfuse parent trace** (`pipeline_trace`) — opened *before* the cache lookup so even cache hits are observable.
4. **Redis cache check**: if `response_cache.get_response(youtube_id)` returns a completed payload → stream it instantly (`$0.00`, the "same video = instant serve") and return. (Skipped when `force_refresh=True` — set by the dev provider-override flow, by `bypassCache=true` submissions via the worker payload, or by the `forceRefresh` flag the api stamps on the version row.)
5. On miss → sets Mongo status `PROCESSING`, clears any stale `transcriptMeta`, mirrors the status to the API, builds the **`PipelineContext`** (with the run's two download handles, `LocalLowresSource` / `LocalHiresSource`) and hands it to **`run_pipeline_phases()`** (`routes/pipeline_orchestration.py`).
6. Any exception → `pipeline_failures.classify_run_failure` → status `FAILED` + error code, a user-safe `error` event.

---

## 2. The orchestrator + the shared context

`PipelineContext` (`services/pipeline/context.py`) is the spine of "who feeds who".
There is **no return-value chaining** between phases — the context *is* the data bus.
Each phase **reads fields off `ctx` and writes its outputs back onto `ctx`**; three
`asyncio.Event`s let the members of the parallel group wait for each other:

```python
@dataclass
class PipelineContext:
    # ── Immutable inputs (set once at construction) ──
    video_summary_id, youtube_id, entry, repository, llm_service, timer

    # ── t=0 group (started by metadata) ──
    video_data                                    # ← metadata
    caption_task, description_task                # background tasks; readers await them
    lowres_video, hires_video                     # the run's two downloads

    # ── Text branch ──
    transcript_data, transcript_trail, clean_text, prompt_segments, source_language_code
    transcript_ready: Event                       # releases the tier probe
    tier_probe_task, probe                        # ← tier probe
    prompt_transcript                             # [m:ss]-marked, rendered once
    plan_result, triage, triage_dict, content_format   # ← plan (reconciled at plan-done)
    memory                                        # ← memory (None = failed / not run)
    video_memory                                  # <video_memory> block, every writer's {video_context}
    plan_done: Event                              # the text groups fire from here

    # ── Frames ──
    scene_frames_all, scene_frames_gallery, scene_frames_for_assembly, frame_descriptions
    visual_annotations                            # <visual_annotations>, set at frames-done
    frames_done: Event                            # the visual group is cut from here

    # ── Extraction / late phases / assembly ──
    extraction_data, extraction_coverage, extraction_groups   # _groups = one row per group call
    quiz_task, enrichment_data, synthesis_dict
    assembled_tabs, assembled_meta
    tab_ledger                                    # every tab_ready goes through it
    source_language, rag_transcript_task          # ← translation / RAG English
    phase_times
```

`run_pipeline_phases` binds a `pipeline.timing` recorder for the run (every outgoing SSE
chunk stamps its milestones), runs `_run_phases_in_order`, and in its `finally` stops
whatever the t=0 group left running (`_release_run_media`: the caption / description /
quiz tasks, the tier probe, both downloads), waits — bounded — for a non-English RAG
transcript task once `done` is out, then persists `pipeline.timing`
(`routes/run_timing.py:persist_run_timing`) on success **and** failure.
`_run_phases_in_order` drives the phases below inside the Langfuse trace and drains the
fire-and-forget faithfulness tasks before the trace flushes. Each phase is an **async
generator** that mutates `ctx` and `yield`s SSE event strings up to the broker.

---

## 3. Phase order at a glance

```
Phase 1  metadata                one extract_info + validate_duration (sequential; ends ~5 s)
         └─ starts the t=0 group in the background: caption fetch · description analysis
            · low-res + one ≤720p download (none on a frame-manifest hit)

Phase 2  one parallel group (run_parallel_phases; the tier-probe task starts alongside)
         ├─ text branch   transcript → tier probe → plan ∥ memory → plan-done reconcile
         │                → <video_memory> → plan_done → provisional Moments tab
         ├─ frames        scenes → tier (probe) → 720p refine → OCR ∥ vision
         │                → frames-done: <visual_annotations> + deferred code tabs → frames_done
         └─ split extraction (EXTRACTION_PARALLEL=true, the default)
                          waits plan_done → text groups (text1 warms the prefix) ;
                          visual group at frames_done ; per-group tab previews ;
                          ONE extraction_complete
         ↳ the group's `finally` writes `transcriptMeta` (ok AND failed runs)

         EXTRACTION_PARALLEL=false: no third member — after the whole group:
         frames-done step, then ONE extraction call (chunked batches one at a time)

         faithfulness judge — fire-and-forget once extraction has data

Phase 3  assembly                description analysis → synthesis FIRST only if memory left
                                 the hero empty → assemble → previews / tabs
                                 → late group: moment fill ∥ synthesis ∥ quiz
                                 → closing re-emit (every tab final, overview last)
                                 → complete → save → Redis / Qdrant / S3 → done (English)

Phase 4  translation             ONLY if ctx.source_language_code is set (non-English) → done
```

The parallel group uses `run_parallel_phases()` (`pipeline_helpers.py`), an
`asyncio.Queue` that streams every member's events in arrival order and emits a
`heartbeat` event every `SSE_HEARTBEAT_SECONDS` of silence — otherwise a multi-minute
Whisper run sends zero bytes and the API gateway's undici proxy aborts the idle SSE
connection at its 300 s body timeout. A member that raises cancels the others and fails
the run (frames never raises — it is non-critical).

---

## 4. Phase-by-phase

### Metadata + the t=0 group · `phases/metadata.py`

- **Calls:** `youtube.extract_video_data(youtube_id)` (one yt-dlp `extract_info`), overlapped with the frame-manifest lookup (`scene_extractor.frames_cached`, ≤ 2 s).
- **Gate:** `validate_duration()` — rejects too-long / too-short videos. Nothing starts before it passes, so a rejected video costs no download and no LLM call.
- **Then, in the background (the t=0 group):** `youtube.fetch_video_captions` (`ctx.caption_task` — the transcript phase awaits it), `_analyze_description` (`ctx.description_task`, prompt `description_analysis.txt`; readers await it through `await_description_analysis`), and the low-res + ≤720p downloads (`ctx.lowres_video.start()`, `ctx.hires_video.start()`), unless the frames are cached.
- **Caption pick** (`video/caption_picker.py`, inside `extract_info`, no extra call): the language first — the one original ASR track's language (`<lang>-orig`; several = an auto-dub, no ASR signal), which overrides a disagreeing uploader tag unless the video is dubbed, else yt-dlp's `language` field (`en-US` → en, legacy `iw` → he) — then a manual track in it, else its untranslated auto track. No track in the spoken language → the older waterfall + English-preference pick. Detail: SERVICE-SUMMARIZER.md §Transcript Fallback Chain.
- **Out:** `ctx.video_data` (title, channel, duration, chapters, subtitles, tags, description, thumbnail, the picked caption track, `language`, `spoken_language`).
- **SSE:** `metadata`.

### The phase-2 group · `routes/pipeline_orchestration.py:_run_phase_two`

`start_tier_probe(ctx)` (`phases/probe.py`) creates the probe task, which waits for
`ctx.transcript_ready`; then `run_parallel_phases` runs the members from
`_phase_two_members(split)`: the text branch (wrapped by
`moments.then_provisional_moments`), frames (wrapped by `frames_done.then_frames_done`
on the split path) and, when `EXTRACTION_PARALLEL` is true,
`run_phase_split_extraction`. The description analysis is **not** a member: no reader in
the group needs it on the critical path.

#### 2a. Text branch · `phases/text.py:run_phase_text`

In order (each step's module in brackets):

1. **Transcript** [`phases/transcript.py`], once the caption fetch landed (`after_captions`). `transcript_fetcher.fetch_transcript()` is a **fallback chain**:
   ```
   S3 cache → yt-dlp subtitles → youtube-transcript-api → Whisper (audio) → Gemini (audio) → metadata-only (music)
   ```
   Whisper is tried *before* Gemini because it natively reports the language. The S3 blob (`cached_transcript.py`) is skipped as **stale** when this run fetched captions and the blob holds another caption track (`s3-stale`). Then basic cleaning (`clean_transcript`, English fillers included; the spaCy + TF-IDF pass only with `TRANSCRIPT_CLEANING_ENABLED`) and **SponsorBlock**, which cuts sponsor reads from `clean_text` **and** `prompt_segments`. Language detection sets `ctx.source_language_code` (non-English only; dropped for sound-only music) — **this single field decides whether translation runs**. The fetcher's `TranscriptTrail` records every layer that ran and failed. **SSE:** `phase` per fallback hop, `transcript_ready`. Then `ctx.transcript_ready` is set.
2. **Tier probe** [`pipeline/tier_probe.py`, prompt `tier_probe.txt`]: one call on `LLM_CLASSIFIER_MODEL`, temperature 0, 80 tokens, 4 s, no retry, over metadata + three short windows of the NATIVE `clean_text` (nothing translates before the plan) → domain, format, `has_visual_demo`, `transcript_usable`. The text branch waits for it (`probe_for_plan`, ≤ 5 s); frames read it separately at their Step 6b. Failure → `None`: the metadata rule decides.
3. **Unusable captions** [`phases/transcript_fallback.py:replace_unusable_captions`]: `transcript_usable: false` on a caption transcript (not music, an audio layer allowed) → the transcript phase re-runs on the audio layers only (`run_audio_transcript_pass`: Whisper → Gemini), a second probe runs on the new text; once per run. A run that goes on with an unusable answer plans without a probe.
4. **The marked transcript**, rendered ONCE: `ctx.prompt_transcript = render_prompt_transcript(ctx)` — `[m:ss]` markers over `prompt_segments` (`transcript/render.py`), `clean_text` for a segment-less (metadata) transcript. Plan and memory read the same string; neither ever sees frame annotations.
5. **Plan ∥ memory** (`run_parallel_phases([run_phase_plan, run_phase_memory])`):
   - **Plan** [`phases/triage.py:run_phase_plan` → `pipeline/plan.py:run_plan`, prompt `plan.txt` + `component_toolkit.txt` via `plan_prompt.render_plan_parts`]: primary model, the FULL marked transcript, 3,500 max tokens, 60 s, 1 retry, JSON mode. The probe sets the playbook (`<domain>:<format>`, its domain only above 0.6 confidence) and the one `Hint:` line. Everything above `<video>` is one cached user block (`cache_control` ttl 1h). Out: `PlanResult` — `contentTags`, `modifiers`, `primaryTag`, `userGoal`, tabs `{id, label, emoji, component, dataSource, goal, brief {what, where, expect}}`, `evidence` (has_* booleans), `terms`. Unregistered dataSources → same-component sibling or dropped (`invalid_datasource`). Failure / confidence < 0.6 → `_build_fallback_plan()` (the domain's default tabs).
   - **Memory** [`phases/memory.py` → `pipeline/memory.py`, prompt `memory.txt`]: `LLM_EXTRACTION_MODEL`, 25 s, one attempt, temperature 0 → outline (4–12 sections), evidence, tldr, takeaways. No timed segments → no call. Its tldr + takeaways go out at once as the first `synthesis_complete` — **the hero**.
6. **Plan-done** [`phases/triage.py:plan_done_events` → `pipeline/reconcile.py:reconcile_plan`, `reconcile_pass.py`]: the memory's second opinion. One evidence view (true if either reader said true, false only when BOTH answered false); a tab whose `requiresEvidence` key is doubly false is re-pointed, kept (moment_track), demoted onto a free eligible source, or dropped; `has_code` tabs are **deferred to frames-done**; the domain's required components are backfilled; quiz policy; dedupe; 3–6 tabs. Every decision → `pipeline.reconcile`. **SSE:** `triage_complete` + `meta` on the reconciled plan.
7. **`<video_memory>`** [`pipeline/video_memory.py:render_video_memory`]: rendered once from the reconciled plan + memory — identity, terms, outline, evidence with its `[m:ss]` ranges. It fills every writer's `{video_context}` slot (extraction, synthesis, quiz). Then `ctx.plan_done` is set (in a `finally`: whatever happens, nothing waiting on it hangs).
8. **Provisional Moments tab** [`phases/moments.py`]: built from the moment fallback chain (memory outline → YouTube chapters → description timestamps, the latter only if the analysis already landed) with the id, label and position estimate of the tab that will replace it (the plan's first `moment_track` tab, else the always-Moments `key_moments`). **SSE:** `tab_ready {provisional: true, final: false}`. No source with an entry → nothing.

#### 2b. Frames · `phases/frames.py` (+ `phases/frames_done.py`)

- **Calls:** `scene_extractor.extract_scene_keyframes()` — the frame manifest at `videos/{id}/{SCENE_S3_PREFIX}/manifest.json` first (skipped for `coldMedia`; `hiresCount == 0` counts as a miss); on a miss: FFmpeg scene detection on the run's low-res file (`ctx.lowres_video`), local scoring, time-slot selection, the **visual tier** decided once at Step 6b (`_TierGate` → `media/visual_tier.resolve_tier`, waits ≤ 3 s for the probe; HIGH over-selects and vision-describes the candidates **before** the hi-res pass, dropping presenter shots; LOW skips vision), then 720p refinement of the selected frames by seeking the run's one local ≤720p file (`ctx.hires_video`), S3 upload — then **in parallel**:
  - `scene_frames.process_scene_frames()` — OCR + presigned URLs,
  - `_run_vision_analysis()` → `frame_analyzer.analyze_frames_with_vision()` (prompt `vision.txt`; `LLM_VISION_MODEL`, blank → primary; ≤ 8 frames per call, ≤ 5 calls in flight). Fresh descriptions are persisted into the manifest.
- **Out:** `ctx.scene_frames_all`, `ctx.scene_frames_gallery`, `ctx.scene_frames_for_assembly`, `ctx.frame_descriptions`. **SSE:** `frames`.
- **Non-critical:** any failure logs a warning and the run continues without frames.
- **Frames-done** (`frames_done.frames_done_step`; split path: `then_frames_done`, which first waits for `plan_done`): renders `ctx.visual_annotations` — the `<visual_annotations>` block, chronological `[m:ss] caption | on-screen text` (`pipeline/visual_annotations.py`), **never** written into `clean_text` — then `triage.frames_done_events` decides the code tabs reconcile deferred: code on screen (vision `scene_type: code`, or code-shaped OCR) keeps them and backfills tech's `code_playground`; no code → the step-1 chain. A changed tab list sends a fresh **`meta`**. Then `ctx.frames_done` is set. Timing step `visual_inject` (name kept from the old Phase 2.5).

#### 2c. Split extraction · `phases/split_extraction.py:run_phase_split_extraction`

The most expensive stage, and the one v9 changed. It waits for `ctx.plan_done`, then:

1. **Chunks** [`pipeline/extraction_chunks.py:plan_chunks`]: ONE chunk (the whole marked transcript) unless one extraction batch can't cover the video (`needs_chapter_batching`: duration > `MAX_MINUTES_PER_BATCH` (40 min) or estimated tokens > `MAX_TOKENS_PER_BATCH` (50k)). A longer video is cut like the chunked path below: the chapter chain (creator chapters → description timestamps, awaited ≤ 5 s → the memory outline → `chapter_detect` only without a usable outline) merged by `batch_chapters`, else a sentence force-split. `CHUNKED_EXTRACTION_THRESHOLD` plays no part here.
2. **Requested fields** [`pipeline/field_requests.py:requested_fields`]: the union over the reconciled tabs of each planned dataSource, its registry siblings (active domains only), the rendering component's side reads, the overview's tip/level reads, and every requested domain's scalars — nothing of an inactive domain, no unplanned modifier output.
3. **Groups** [`pipeline/field_groups.py:build_groups`, `domains.json` `grouping`]: text fields bucketed by domain, split by field when Σ `outputWeight × expect` exceeds `groupOutputBudget` (1,500), at most `maxTextGroups` (5) text groups ordered by the first tab they serve — **text1** holds the first planned tab; every `waitsForVisual` field forms ONE **visual** group.
4. **Prompt** [`extractor._build_prompt` cut by `pipeline/extraction_group_prompt.py:build_group_messages`]: three content blocks, no system prompt — block 1 (ttl 1h) = the rules + every requested domain's full schema + the primary's example; block 2 (5 min) = `<video>` + the chunk's transcript + `<video_memory>`; block 3 (uncached) = the group's job from `extraction_group_job.txt` ("emit ONLY these keys" + their shape, the tabs it serves; the visual group adds the chunk's annotations and key frames).
5. **Firing** [`pipeline/extraction_groups.py:fire_groups`]: per chunk, text1 is the only **streamed** call — its first chunk releases the chunk's other text groups (or `GROUP_RELEASE_FALLBACK_S` = 1.5 s), so they read blocks 1 + 2 instead of writing them. The visual group waits for `ctx.frames_done`, is re-cut from what no text group asked for (nothing left → not fired) and binds the annotations + key frames then. Every call holds a slot of the run's `EXTRACTION_PARALLEL_BATCHES` semaphore; model `LLM_EXTRACTION_MODEL`, JSON mode, 16,384 max tokens, timeout from the group's estimate (120–300 s), 1 retry. Spans `extraction_<group>` (`_c<k>` for chunk k ≥ 1), ledger feature `summarize:extraction:<group>`.
6. **Landing** [`pipeline/group_merge.py:validate_group`, `pipeline/group_failures.py`]: each answer is validated without default materialization and cut to the group's own keys as it lands. A lost answer (not JSON, no key of the group, content validation dropped, a warm-up stream that broke) gets ONE more unstreamed call; still nothing → `failed` (its fields count as landed, so no tab waits for ever). **SSE per landed call:** `extraction_progress {section: "groups", batch, of}`, then a **preview** `tab_ready` of every planned tab whose requested fields have all landed (`phases/group_emission.py:GroupLanding`): non-final, without cross-tab links and attachments, at its plan-order position — never the overview, a moment tab or the quiz. The **quiz task** starts here (`enrichment.start_quiz_task`, `ctx.quiz_task`) once the text groups holding a `quizDomains` field have landed.
7. **Merge** [`group_merge.merge_groups`]: ONE deterministic merge — chunk-major, text1..N then visual; lists concatenate with later duplicates dropped, first non-empty scalar wins. `ctx.extraction_data`, `pipeline.extraction._groups` (one row per call), coverage + quality on the requested fields (`finish_extraction`). Every failed group marks the run `degraded`; when no group landed at all → `fail_extraction` (status `FAILED`, `LLM_ERROR`, an `error` event; the orchestrator still runs assembly afterwards). **SSE:** `extraction_complete`.

### Today's single extraction (`EXTRACTION_PARALLEL=false`) · `pipeline_orchestration._frames_done_then_extraction` → `phases/extraction.py` → `pipeline/extractor.py`

The rollback lever. After the whole phase-2 group: the frames-done step, then
`run_phase_extraction` through `run_parallel_phases` (for its heartbeats). The request
bytes for a given plan are identical to the pre-split single call (replay identity
test). It reverts **extraction only**: the plan-done / frames-done reconcile,
`<video_memory>` evidence ranges, the toolkit markers and `PIPELINE_VERSION` v9 stay.

- **Prompt:** `extraction_prompt.build_extraction_prompt()` over `base_extraction.txt` + `schemas/{tag}.txt` + `quality_rules.txt` + `examples/{primary}.txt`: system = the rules; user block 1 = `[<video> + transcript + <video_memory>]` (5-min breakpoint); block 2 = the job (briefs + caps, schemas, example, `<visual_annotations>`, ≤ 12 key-frame captions).
- **Strategy** (`extractor._resolve_strategy`): a video longer than `CHUNKED_EXTRACTION_THRESHOLD` (900 s) with a transcript gets chapters (`phases/extraction.py:_split_chapters`, the same chain as above); with ≥ 2 chapters that `batch_chapters` turns into > 1 batch → **chunked** (each batch its own transcript slice + `<batch_partial_context>`, **one at a time** under this flag, a rate-limited batch retried in a sequential pass); otherwise ≤ 5,333 words → **single** (16,384 tokens, 240 s); a single-batch input over that is force-split into `EXTRACTION_FORCE_SPLIT_CHUNKS` batches. Without chapters: < 5,333 words → single; > 20,000 → force-split; in between → **overflow** (one call, 32,768 tokens, `300 + words/100` s, ≤ 600). Batches merge in `extraction_merger.py`.
- **SSE:** `extraction_progress` (`section: "all"`, or `"chunked"` / `"chunked-sequential"` per batch), `extraction_complete`.

### Faithfulness judge · `routes/pipeline_faithfulness.py` → `pipeline/faithfulness.py`

Once `ctx.extraction_data` exists (after the phase-2 group on the split path), the
orchestrator spawns a **fire-and-forget judge**: sampled claims
(`LANGFUSE_FAITHFULNESS_SAMPLE_RATE`, ≤ 6 per video) checked against the transcript +
the rendered visual annotations, fast tier, logged as a Langfuse score
(`summarize:faithfulness`). Informational — never blocks or gates the run. Drained
(≤ 25 s) before the trace flushes.

### Assembly + late group · `phases/assembly.py:run_phase_assembly` → `pipeline/assembly/core.py:assemble_response`

Where all the streams *converge* into the final shape. Order:

1. **Description analysis** (≤ 5 s wait; past it `meta` goes without it). **SSE:** `description_analysis`.
2. **Synthesis FIRST** only when memory left the tldr or takeaways empty (`synthesis.needs_hero_fallback`): the overview and the minimum-3 Key Info fallback need them. Otherwise memory's hero seeds `synthesis_dict`.
3. **Assemble** (pure code, no LLM): per planned tab, `resolve_data_source()` maps its `dataSource` to the extracted data with cascading fallbacks; the matching assembler from `ASSEMBLER_REGISTRY` builds the props; promotion, density caps, the degrade-never-drop demote ladder, domain forbidden / max policy, timestamp hygiene, the always-Moments tab, frame thumbnails, cross-tab links, the minimum-3 fallback. A tab a failed extraction group cost is recorded as `extraction_group_failed`.
4. **First tab emissions**: on the split path a **preview** of each tab the client still lacks (the overview, tabs assembly added, tabs the last groups or frames changed — `group_emission.assembled_tab_events`); on today's path every tab but the moment tabs, `final` where no late phase can change it. Every `tab_ready` goes through the run's `TabLedger` (`pipeline/tab_ledger.py`, the emission contract).
5. **Late group**, in parallel with heartbeats: **moment fill** (`assembly/moment_frame_fill.py`: a frame at each frameless moment's timestamp from the run's ≤720p file → the moment tabs stream framed, replacing the provisional tab by id or with `replaces`), **synthesis** unless it ran first (`synthesis.txt`, fast tier: masterSummary + seoDescription; patches meta + the overview; **SSE:** `synthesis_complete` superset), the **quiz** when `needs_quiz` (`enrich_quiz.txt`, fast tier; the split path awaits the task it already started; **SSE:** `enrichment_complete`). A landed quiz re-assembles the response (`phases/late_quiz.py`): the quiz tab last, a sparse host's quick_quiz strip, links and the overview's count follow.
6. **Closing re-emit** (`TabLedger.closing_events`): every tab not yet received final goes out `final: true` at its final position, in order — the overview last after per-group streaming. Then the 720p file is deleted.
7. **`complete`** (`tabCount`, `tabIds` = the final order — a streamed tab missing there was withdrawn, `processingTimeMs`, `degraded`), then **save** (`repository.save_structured_result`: `tabs`, `meta`, the `pipeline` record — triage, reconcile, extraction, enrichment, synthesis, assembly accounting — `pipelineVersion`, status `completed` for English, **`processing`** for non-English).
8. **Shared artifacts**: Redis `response_cache.set_response()` — **English only**, awaited before `done`, best-effort; Qdrant (`phases/qdrant_index.py:index_in_qdrant`, background tasks): transcript, default-output and visual points — a non-English transcript first gets its English (Whisper translate when this run transcribed the audio, else the fast model block by block, `rag_translation`). An eval-user run writes neither Redis nor Qdrant. The S3 raw transcript (`transcript_store.store()`, background) is written either way, unless the transcript came from S3. Bug: its `rawTranscriptRef` write-back filters on the string id where Mongo stores an `ObjectId`, so it matches no row and the field stays unset.
9. **SSE:** `done` + `[DONE]` (English only).

### Transcript provenance write · `routes/pipeline_orchestration.py:_record_transcript_outcome`

- Called from the `finally` around the phase-2 group: builds the block with `transcription/transcript_meta.build_transcript_meta()` and persists it through `repository.set_transcript_meta()` — a dedicated `$set`-only writer (`save_structured_result` would `$unset` the `forceRefresh` bypass marker mid-run).
- Runs for successful **and** failed fetches (a `TranscriptError` still propagates afterwards) and lives inside the Langfuse `pipeline_trace`, mirroring `transcriptSource` / `transcriptType` / `transcriptAttempted` / `transcriptOutcome` onto the trace.
- `clear_transcript_meta()` runs at the `processing` transition and on the Redis fast path, so a row re-run on the same `_id` never keeps a stale block. Field shape: [DATA-MODELS.md](./DATA-MODELS.md).

### Translation · `phases/translation.py` → `pipeline/translation.py` *(non-English only)*

- **Prompt:** `prompts/translate_flat.txt` — `LLM_TRANSLATION_MODEL` (blank → fast tier), ≤ 2,500-char batches (CJK weighted) of ≤ 100 strings, up to 8 in flight, each checked for an echo; a failed batch retried once as two halves, a half that still fails kept in English.
- **Calls:** `translate_to_source()` — walks the **English** assembled output, translates every translatable prose string into `source_language_code`, returns the unchanged English dict with the translated artifact nested under `sourceLanguage`. Plus the title (the one field that travels source → English; original kept under the source block).
- **On failure / mirror / length mismatch:** no `sourceLanguage` key → the phase finalizes the doc `completed` English-only (the FE renders no toggle).
- **Out:** `ctx.source_language`; re-saves the Mongo doc with `sourceLanguage` + status `completed`; writes the final Redis payload (English primary + toggle).
- **SSE:** `phase: translation`; then the **orchestrator** (not the phase) emits the deferred `done` + `[DONE]`. A translation that raises leaves the doc `processing` (retriable) and emits no terminal event.

### Run timing · `routes/run_timing.py` + `services/pipeline/pipeline_timing.py`

Every phase and sub-step (`text`, `transcript`, `plan`, `memory`, `frames`, `frames.*`,
`visual_inject`, `split_extraction`, `extraction`, `assembly`, `moment_fill`,
`synthesis`, `enrichment`, `quiz_call`, `translation`, …), every LLM call (tokens, cache
reads / 1h / 5m writes, cost, retries, fallbacks, `ttftMs` for the streamed warm-up) and
every download lands in `pipeline.timing` (schema version 3, DATA-MODELS.md) and a
one-line `DONE` log (`log_run_summary`). On the split path the `transcript_frames` wall
spans the extraction too; `extraction` = plan-done → last group landed.

---

## 5. The data-flow narrative — "who feeds who"

```
youtube_id
  └─ metadata ─────────► video_data ──► t=0 group: captions · description analysis · downloads
                                  │
      ┌───────────────────────────┴─────────────────────────────┐
      ▼                                                          ▼
 TEXT BRANCH                                                  FRAMES
 transcript (chain → clean_text, prompt_segments,             scenes + scoring
   source_language_code)                                        │  tier ◄── probe (≤ 3 s)
      │ transcript_ready                                         │  720p refine, S3
 tier probe ──────────────────────────────────────────────────►─┘  OCR ∥ vision
      │ (playbook + hint)                                        │
 PLAN ∥ MEMORY  (both read the [m:ss] transcript)                │
      │            │                                             │
      │            └─ hero ──► synthesis_complete                │
      ▼                                                          │
 reconcile ──► tabs + evidence     ← THE BLUEPRINT               │
      │                                                          │
 <video_memory> ── plan_done ──► provisional Moments tab         │
      │                                                          ▼
      │                                          frames-done: <visual_annotations>
      │                                          + deferred code tabs ── frames_done
      ▼                                                          │
 SPLIT EXTRACTION  requested fields → groups → chunks            │
   text groups (text1 warms the prefix) ─┐        visual group ◄─┘
                                         ▼        ▼
                       per-group validate → preview tab_ready
                                         │  (quiz task starts when its groups land)
                                         ▼
                        merged extraction_data          ← THE FACTS
                                         │
            ┌────────────────────────────┼─────────────────┐
            ▼                            ▼                 ▼
     faithfulness judge             ASSEMBLY (code)   coverage / quality
     (background)          tabs(plan) × extraction × frames × memory hero
                                         │
                       late group: moment fill ∥ synthesis ∥ quiz
                                         │
                     closing re-emit ──► assembled_tabs + meta   ← THE PRODUCT
                                         │
                ┌──────────┬─────────────┼──────────┐
                ▼          ▼             ▼          ▼
              Mongo      Redis        Qdrant       S3
              (doc)  (English only) (transcript,  (raw transcript)
                                     output, visual)
                                         │
             (non-English?) ──► TRANSLATION ──► sourceLanguage ──► re-save + cache
                                         ▼
                    SSE: done → frontend refetches the completed Mongo doc
```

**The one flag that branches the whole tail:** `ctx.source_language_code`.
English → assembly finalizes, caches, emits `done`. Non-English → assembly leaves the
doc `processing` and translation owns completion (so an interrupted translation stays
retriable instead of fake-completed English-only).

**The one setting that branches extraction:** `EXTRACTION_PARALLEL` (default `true` =
the field groups inside the phase-2 group; `false` = one call after the whole group).

---

## 6. Prompt files → stage map

All prompts load through `prompt_builder.load_prompt_text()`, which is
**registry-first**: it tries the Langfuse-registered version (`summarizer:<name>`)
and falls back to the on-disk `.txt`, recording the prompt version on the active trace
(`PROMPT_SOURCE=disk` reads the `.txt` on dev; OBSERVABILITY.md). Model routing: the
**primary** model is `LLM_MODEL`, else the `LLM_PROVIDER` default; the **fast tier** is
`LLM_FAST_MODEL`, else the fast model of `LLM_FAST_PROVIDER` (or `LLM_PROVIDER`); a blank
stage setting falls back as shown.

| Prompt file | Stage | Loaded by | Model |
|-------------|-------|-----------|-------|
| `description_analysis.txt` | metadata (t=0, background) | `video/description_analyzer.py` | `LLM_DESCRIPTION_MODEL` → fast tier |
| `tier_probe.txt` | tier probe | `pipeline/tier_probe.py` | `LLM_CLASSIFIER_MODEL` |
| `plan.txt` + `component_toolkit.txt` | plan | `pipeline/plan_prompt.py` | primary (static half cached 1h) |
| `memory.txt` | memory (∥ plan) | `pipeline/memory.py` | `LLM_EXTRACTION_MODEL` → primary |
| `vision.txt` | frames (vision) | `media/frame_analyzer.py` | `LLM_VISION_MODEL` → primary |
| `chapter_detect.txt` | long-video chunking — > 1 batch and no usable outline | `transcription/transcript_chunker.py` | `LLM_CHAPTER_DETECT_MODEL` → fast tier |
| `base_extraction.txt` + `schemas/*.txt` + `quality_rules.txt` + `examples/*.txt` | extraction (both paths) | `pipeline/extraction_prompt.py`, `extractor._build_prompt` | `LLM_EXTRACTION_MODEL` → primary |
| `extraction_group_job.txt` | split extraction: each group's job block | `pipeline/extraction_group_prompt.py` | (as extraction) |
| `synthesis.txt` | synthesis | `pipeline/synthesis.py` | `LLM_SYNTHESIS_MODEL` → fast tier |
| `enrich_quiz.txt` | the quiz | `pipeline/enrichment.py` | `LLM_ENRICHMENT_MODEL` → fast tier |
| `translate_flat.txt` | output translation + the RAG transcript's English | `pipeline/translation_flat.py` | `LLM_TRANSLATION_MODEL` → fast tier |
| (inline `_JUDGE_PROMPT`) | faithfulness judge (background) | `pipeline/faithfulness.py` | fast tier |

`prompts/detection/category_rules.json` is not an LLM prompt: the metadata category
rules (`video/youtube.py`) behind the visual-tier fallback and the plan's category hint.
`LLM_FALLBACK_PROVIDER` (when set) gives primary-model calls a cross-provider fallback
after one same-provider retry (`utils/llm_retry.call_llm_with_retry`).

---

## 7. The three pivot artifacts

Everything else is plumbing around these three:

1. **`plan_result.tabs`** — *the blueprint.* The plan decides **what components exist
   and what data each needs**, before any data is extracted; reconcile at plan-done
   (and frames-done for code tabs) corrects it against the memory's evidence and the
   screen. The split extraction requests exactly the fields these tabs read.
2. **`extraction_data`** — *the facts.* Schema-driven structured JSON keyed by domain
   (`tech.snippets`, `food.ingredients`, `learning.concepts`, …), merged from the field
   groups.
3. **`assembled_tabs`** — *the product.* Pure-code marriage of blueprint + facts +
   memory / synthesis + quiz + frames into renderable component props, persisted to
   Mongo and cached in Redis.

`<video_memory>` (plan + memory, rendered once at plan-done) is the shared context every
writer reads, so extraction, synthesis and the quiz see the same outline and evidence.

---

## 8. End-to-end scenario walkthroughs

Concrete traces showing which branch fires for common inputs (default settings).

### Scenario A — Short English coding tutorial (cache miss, one chunk)

> *"React useEffect in 8 minutes"* — 8-min English video with yt-dlp captions, code on screen.

| Step | What happens |
|------|--------------|
| Entry | API queues job → worker wins Redis lock → `stream_summarization` |
| Cache | Redis miss → status `PROCESSING`, build `ctx` |
| **Metadata** | yt-dlp → `video_data` (8 min, has captions). Passes `validate_duration`. Caption fetch, description analysis and both downloads start in the background. |
| **Text branch** | Transcript: yt-dlp subtitles (no Whisper/Gemini); language `en` → `source_language_code = None`. Tier probe → `tech` / `tutorial`, `has_visual_demo`. Plan ∥ memory: memory's hero goes out as `synthesis_complete`; the plan designs e.g. `code` (`code_playground`, `tech.snippets`), `steps`, a quiz. Reconcile: `has_code` false in both readers? → the code tab is deferred to frames-done; otherwise kept. `triage_complete` + `meta`, then the provisional Moments tab. |
| **Frames** | Low-res scenes → tier from the probe → 720p refine → OCR ∥ vision captions the editor. Frames-done: `<visual_annotations>`; a deferred code tab is kept because the screen shows code. |
| **Split extraction** | 8 min, well under 40 min / 50k tokens → **one chunk**. Groups: e.g. text1 (the first planned tab's fields), text2, and a visual group (fields that wait for the screen, such as code). text1 streams and releases text2 on its first token; the visual group fires at frames-done. Each landed group → `extraction_progress` + preview `tab_ready`s; the quiz call starts when its groups landed. One `extraction_complete`. |
| **Assembly** | Memory had the hero → no early synthesis. The overview preview; late group: moment fill frames the Moments tab (replaces the provisional), synthesis patches the overview, the quiz joins last. Closing re-emit: every tab `final: true`, overview last. `complete` → saved `completed`, cached in Redis, Qdrant points, raw transcript to S3. |
| **Translation** | Skipped (`source_language_code is None`). |
| End | `done` + `[DONE]`. |

### Scenario B — The same video, second viewer (cache HIT)

> Another user submits the **same** YouTube ID minutes later.

| Step | What happens |
|------|--------------|
| Entry | `stream_summarization` opens the Langfuse trace (so the hit is counted). |
| **Cache** | `response_cache.get_response(youtube_id)` returns the completed payload, integrity-checked (`status=completed`, matching `youtubeId`, non-empty `tabs`, non-empty `meta`). |
| Trace | `update_trace_metadata({cacheHit: True})`. |
| DB | If this user's doc isn't `completed`, a **fire-and-forget** save copies it (clearing any stale `transcriptMeta` first). |
| Stream | `routes/cached_response.py:stream_cached_structured` replays the stored result: `cached`, `metadata`, `triage_complete`, `meta`, every tab once `final: true` at its index, `synthesis_complete`, `done`. **Zero LLM calls, $0.00.** |

### Scenario C — 90-minute Hebrew lecture (no captions → chunks × groups → translation)

> Hebrew university lecture, no YouTube captions, chapters listed in the description.

| Step | What happens |
|------|--------------|
| **Metadata** | `video_data` (90 min, **no** subtitles). The description analysis (background) extracts the description **timestamps**. |
| **Transcript** | yt-dlp subtitles absent → youtube-transcript-api fails → **Whisper** audio (budget scales with duration). Whisper reports `he` → `source_language_code = "he"`. The probe, plan and memory read the native Hebrew. |
| **Plan ∥ memory** | `learning` / `lecture` → e.g. `concepts` (`concept_canvas`), a `moment_track` tab, a quiz. Memory's outline covers the 90 minutes and feeds the provisional Moments tab. |
| **Split extraction** | 90 min > 40 min → **chunks**: the chapter chain takes the description timestamps (awaited ≤ 5 s), `batch_chapters` merges them into ≤ 40-min / ≤ 50k-token batches — at least three chunks. Calls = chunks × groups under the run's semaphore; each chunk's text1 warms that chunk's head block; each chunk carries its `<batch_partial_context>`, and its visual call only the annotations and key frames of its own window. A field lands once every chunk's call of its group did; the merge is chunk-major. |
| **Assembly** | Tabs assembled in **English**. Saved as **`processing`** (not completed). Redis caching **skipped**. Qdrant: the transcript's English comes from Whisper translate of the cached audio (this run transcribed it). Emits `complete` but **not** `done`. |
| **Translation** | `source_language_code="he"` → `translate_to_source` → Hebrew strings under `sourceLanguage` (RTL), the title both ways. Re-saves with status **`completed`**, writes the Redis payload (English primary + toggle). |
| End | The orchestrator emits the deferred `done` + `[DONE]`. The FE refetches → completed doc with a Hebrew/English toggle. |

> If translation had crashed mid-way, the doc stays `processing` → retriable, never a fake-completed English-only result.

### Scenario D — Instrumental music video (sound-only → metadata fallback)

> A lo-fi beats video: `category=music`, no real speech.

| Step | What happens |
|------|--------------|
| **Transcript** | `is_music=True`. Captions absent → api fails → Whisper/Gemini return hallucinated foreign fragments. `is_sound_only_video()` → **`source_language_code` dropped to None** (no bogus translation, no toggle). All audio sources exhausted → **metadata fallback**: `build_metadata_text(video_data)` becomes the text, segments empty. |
| **Text branch** | No timed segments → **no memory call** (it would invent an outline), so no early hero; the plan reads `clean_text` (title, channel, tags, description). |
| **Frames** | Album art / visualizer captioned. |
| **Split extraction** | The groups over the metadata text — one chunk when the video is ≤ 40 min (a longer one is still force-split by duration, into small chunks). |
| **Assembly** | Memory left the hero empty → **synthesis runs first**. The minimum-3-tab guarantee keeps a usable layout. |
| **Translation** | Skipped (`source_language_code is None`). |

### Scenario E — One extraction group fails

> A 20-min English talk; the second text group's answer is not usable.

| Step | What happens |
|------|--------------|
| **Group call** | text2's answer is not a JSON object (or holds none of its keys, or validation kept nothing) → **one more call**, unstreamed (`retried: "parse"` / `"empty"`); the warm-up text1 gets the same when its stream breaks (`"stream"`). |
| **Still nothing** | text2 is `failed`: its fields count as landed, so no tab waits; the other groups land normally and their tabs stream as previews. |
| **Merge** | `pipeline.extraction._groups` records the row (`status: "failed"`); the run is marked `degraded`. |
| **Assembly** | text2's tabs fall back through assembly's usual chain (sibling source, demotion) or drop — `droppedTabs` reason `extraction_group_failed`. The result ships with `meta.degraded` (the FE retry affordance). |
| **All groups failed** | `fail_extraction`: status `FAILED`, `LLM_ERROR`, an `error` event "Extraction failed to produce data". |

---

## 9. SSE event timeline

The order of events a frontend client observes for a typical English cache-miss run
(`EXTRACTION_PARALLEL=true`). Text branch and frames run in parallel, so `frames` can
arrive anywhere after `metadata`; the frames-done `meta` comes after plan-done:

```
metadata                → title/channel/thumbnail/duration  (one extract_info, ~5 s)
phase: transcript…      → fallback-hop markers
transcript_ready
synthesis_complete      → memory's hero: tldr + keyTakeaways (∥ the plan)
triage_complete         → reconciled tab plan + evidence      ┐ plan-done
meta                    → tab labels (progressive shell)      │
tab_ready (provisional) → the Moments placeholder             ┘
frames                  → scene frames (URLs, OCR)
[meta]                  → frames-done changed the tab list (deferred code tabs)
extraction_progress     → section "groups": one per landed group call …
tab_ready* (preview)    → … then every tab whose fields all landed (non-final)
extraction_complete     → the merged extraction
[heartbeat]*            → during any silent stretch
description_analysis
tab_ready* (preview)    → the overview + tabs assembly added or the last groups changed
tab_ready (moments)     → framed; replaces the provisional
synthesis_complete      → superset (masterSummary, seoDescription)
enrichment_complete     → the quiz (when planned)
tab_ready* (final)      → closing re-emit: every tab final, overview last
complete                → tabCount, tabIds (final order), processingTimeMs, degraded
done + [DONE]           → English: from assembly │ non-English: after translation
```

With `EXTRACTION_PARALLEL=false` the extraction events come after the whole group
(`extraction_progress` with `section: "all"` / `"chunked"`), and assembly then streams
every tab but the moment tabs (`final` where no late phase can change them) before the
late group. Full rules: [API-REFERENCE.md — emission sequence](./API-REFERENCE.md#tab_ready).

`error` events (with an `ErrorCode`) replace the tail on `TranscriptError`,
`RateLimitError`, `Timeout`, `APIError`, or any unexpected exception; the Mongo doc
is set `FAILED` with the code.

---

### Source map (quick reference)

| Concern | File |
|---------|------|
| Entry / cache / trace | `routes/pipeline_runner.py` |
| Phase order, transcript provenance, run media + timing | `routes/pipeline_orchestration.py`, `routes/run_timing.py` |
| HTTP route + broker dispatch | `routes/stream.py`, `routes/pipeline_broker.py` |
| Worker (queue) | `worker/runner.py`, `worker/pipeline.py` |
| Shared state | `services/pipeline/context.py` |
| Phase generators | `services/pipeline/phases/*.py` (`metadata`, `text`, `transcript`, `transcript_fallback`, `probe`, `triage` = plan, `memory`, `moments`, `frames`, `frames_done`, `split_extraction`, `group_emission`, `extraction`, `assembly`, `synthesis`, `enrichment`, `late_quiz`, `qdrant_index`, `translation`) |
| Parallel runner + SSE helpers | `services/pipeline/pipeline_helpers.py` |
| Transcript fallback chain | `services/transcription/transcript_fetcher.py` |
| Tier probe, plan, memory | `services/pipeline/tier_probe.py`, `plan.py`, `plan_prompt.py`, `memory.py` |
| Reconcile | `services/pipeline/plan_reconcile.py` (plan-parse guard), `reconcile.py`, `reconcile_pass.py` |
| `<video_memory>`, `<visual_annotations>` | `services/pipeline/video_memory.py`, `visual_annotations.py` |
| Split extraction | `services/pipeline/field_requests.py`, `field_groups.py`, `extraction_chunks.py`, `extraction_group_prompt.py`, `extraction_groups.py`, `group_merge.py`, `group_failures.py`, `schema_fields.py` |
| Single-call extraction | `services/pipeline/extraction_prompt.py`, `extractor.py`, `extraction_merger.py` |
| Tab emission contract | `services/pipeline/tab_ledger.py` |
| Prompt loading + registry | `services/pipeline/prompt_builder.py`, `prompt_registry.py` |
| Assembly orchestrator + assemblers | `services/pipeline/assembly/core.py`, `assembly/registry.py`, `assembly/assemblers_*.py` |
| Run timing recorder | `services/pipeline/pipeline_timing.py` |
| Prompts | `prompts/*.txt`, `prompts/schemas/`, `prompts/examples/` |
