# Summarizer Pipeline — Complete Workflow

> How a YouTube URL becomes an interactive knowledge app. This traces **who calls
> what, in what order, which prompt file each stage loads, and how data flows from
> step to step** until it becomes the final stored result — the v10 pipeline
> (`PIPELINE_VERSION` v10: pipeline-1min phases 1–3 + the 4.0 model refresh; output-quality
> phases 0b and 1 ship on v10 too — [Assembly notes](#assembly-notes-output-quality),
> [what each prompt asks for](#what-each-prompt-asks-for-output-quality)).
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

- **Calls:** `youtube.extract_video_data(youtube_id)` (one yt-dlp `extract_info`), overlapped with the frame-manifest lookup (`frame_manifest.frames_cached`, ≤ 2 s).
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
2. **Tier probe** [`pipeline/tier_probe.py`, prompt `tier_probe.txt`]: one call on `LLM_CLASSIFIER_MODEL` (Haiku 4.5), temperature 0, 80 tokens, 4 s, no retry, over metadata + three short windows of the NATIVE `clean_text` (nothing translates before the plan) → domain, format (19, `probe_types.VALID_FORMATS`; the spoken-word ones by what the speaker does — `talk` makes a case, `lecture` teaches, `interview` is a host questioning a guest, `podcast` the regular hosts among themselves), `has_visual_demo`, `transcript_usable`. The text branch waits for it (`probe_for_plan`, ≤ 5 s); frames read it separately at their Step 6b. Failure → `None`: the metadata rule decides.
3. **Unusable captions** [`phases/transcript_fallback.py:replace_unusable_captions`]: `transcript_usable: false` on a caption transcript (not music, an audio layer allowed) → the transcript phase re-runs on the audio layers only (`run_audio_transcript_pass`: Whisper → Gemini), a second probe runs on the new text; once per run. A run that goes on with an unusable answer plans without a probe.
4. **The marked transcript**, rendered ONCE: `ctx.prompt_transcript = render_prompt_transcript(ctx)` — `[m:ss]` markers over `prompt_segments` (`transcript/render.py`), `clean_text` for a segment-less (metadata) transcript. Plan and memory read the same string; neither ever sees frame annotations.
5. **Plan ∥ memory** (`run_parallel_phases([run_phase_plan, run_phase_memory])`):
   - **Plan** [`phases/triage.py:run_phase_plan` → `pipeline/plan.py:run_plan`, prompt `plan.txt` + `component_toolkit.txt` via `plan_prompt.render_plan_parts`]: primary model (Sonnet 5.5) at effort medium (`plan.PLAN_EFFORT`), the FULL marked transcript, 3,500 max tokens, 60 s, 1 retry, JSON mode. The probe sets the playbook (`<domain>:<format>`, its domain only above 0.6 confidence) and the one `Hint:` line. Everything above `<video>` is one cached user block (`cache_control` ttl 1h). Out: `PlanResult` — `contentTags`, `modifiers`, `primaryTag`, `userGoal`, tabs `{id, label, emoji, component, dataSource, goal, brief {what, where, expect}}`, `evidence` (has_* booleans), `terms`. Unregistered dataSources → same-component sibling or dropped (`invalid_datasource`). A goal that breaks the viewer-outcome rule is logged at validation (`plan_prompt.goal_rule_violation`: empty, names a component, describes the surface, names the topic, or no "you") — the tab ships. A tab on `learning.keyIdeas` switches the learning domain on instead of being re-pointed (`plan_reconcile.activate_argument_domains`: ≤ 3 content tags, a tag no tab reads gives way from the end, never the primary). Failure / confidence < 0.6 → `_build_fallback_plan()` (the domain's default tabs).
   - **Memory** [`phases/memory.py` → `pipeline/memory.py`, prompt `memory.txt`]: `LLM_MEMORY_MODEL` (Haiku 5.5; blank = `LLM_EXTRACTION_MODEL` in config.py only — under compose a blank line keeps Haiku 5.5), 25 s, one attempt, temperature 0 (dropped for Haiku 5.5), 1,600 max tokens → outline (4–12 sections, each with an `establishes` line ≤ 120 chars), evidence, tldr (the video's thesis), takeaways (`takeaway_count`: 3 / 5 / 7 for ≤ 15 / ≤ 45 / longer minutes, 5 for an unknown length; each closing with its `[m:ss]`, a stamp past the end cut). No timed segments → no call. Its tldr + takeaways go out at once as the first `synthesis_complete` — **the hero** (the ≤ 16 s preview: with `HERO_REWRITE_ENABLED` the hero rewrite replaces it in the late group, [below](#hero-rewrite--quiz-v2-output-quality-21--21b)).
6. **Plan-done** [`phases/triage.py:plan_done_events` → `pipeline/reconcile.py:reconcile_plan`, `reconcile_pass.py`]: the memory's second opinion. One evidence view (true if either reader said true, false only when BOTH answered false); a tab whose `requiresEvidence` key is doubly false is re-pointed, kept (moment_track), demoted onto a free eligible source, or dropped; `has_code` tabs are **deferred to frames-done**; a Moments tab on `learning.timestamps` reads the primary's own moment source — its first `moment_track` defaultTab source, e.g. `podcast.segments` (`reconcile_pass.moment_source_step`, `repointed` / `primary_moment_source`); the domain's required components are backfilled; quiz policy; dedupe; a planned `info_grid` joins the planned tab whose question it answers as one of its `folds` (`fold_step`, R6: `folded` / `info_grid_alone`; the host = the tab whose component ranks first in `infoGridHosts`, the grid's own domain first among equals; never below 3 tabs); 3–6 tabs. Every decision → `pipeline.reconcile`. **SSE:** `triage_complete` + `meta` on the reconciled plan.
7. **`<video_memory>`** [`pipeline/video_memory.py:render_video_memory`]: rendered once from the reconciled plan + memory — identity, terms, outline, evidence with its `[m:ss]` ranges. It fills every writer's `{video_context}` slot (extraction, synthesis, quiz). Then `ctx.plan_done` is set (in a `finally`: whatever happens, nothing waiting on it hangs).
8. **Provisional Moments tab** [`phases/moments.py`]: built from the moment fallback chain (memory outline → YouTube chapters → description timestamps, the latter only if the analysis already landed) with the id, label and position estimate of the tab that will replace it (the plan's first `moment_track` tab, else the always-Moments `key_moments`); an outline moment carries its section's `establishes` line as its description. **SSE:** `tab_ready {provisional: true, final: false}` — the web strip labels it "Preview" until the replacement lands. No source with an entry → nothing; nor for a food, fitness or project plan whose steps are the timeline (a `step_player`, `step_flow_canvas` or `workout_room` tab, `moment_track` not required — D6), whose response ships no Moments tab ([Assembly notes](#assembly-notes-output-quality)).

#### 2b. Frames · `phases/frames.py` (+ `phases/frames_done.py`)

- **Calls:** `scene_extractor.extract_scene_keyframes()` — the frame manifest (`media/frame_manifest.py`) at `videos/{id}/{SCENE_S3_PREFIX}/manifest.json` first (skipped for `coldMedia`; `hiresCount == 0` counts as a miss); on a miss: FFmpeg scene detection on the run's 360p file (`ctx.lowres_video`; the 720p download's file instead when it lands first as the same ≤360p rendition — `hires_prefetch.first_detection_file`), local scoring, time-slot selection, the **visual tier** decided once at Step 6b (`_TierGate` → `media/visual_tier.resolve_tier`, waits ≤ 3 s for the probe; HIGH over-selects and vision-describes the candidates **before** the hi-res pass, dropping presenter shots; LOW skips vision), then 720p refinement of the selected frames by seeking the run's one local ≤720p file (`ctx.hires_video`) when that file is already on disk — while it still downloads, the 360p frames are uploaded and a deferred pass (`media/hires_upgrade.py`, started after vision, awaited ≤ 60 s after `done`) overwrites the same S3 objects once it lands, so frames-done never waits for it unless pass 1 failed; vision + OCR then read 360p frames — S3 upload — then **in parallel**:
  - `scene_frames.process_scene_frames()` — OCR + presigned URLs,
  - `_run_vision_analysis()` → `frame_analyzer.analyze_frames_with_vision()` (prompt `vision.txt`; `LLM_VISION_MODEL`, Sonnet 5.5; ≤ 8 frames per call, ≤ 5 calls in flight). Fresh descriptions are persisted into the manifest.
- **Out:** `ctx.scene_frames_all`, `ctx.scene_frames_gallery`, `ctx.scene_frames_for_assembly`, `ctx.frame_descriptions`. **SSE:** `frames`.
- **Non-critical:** any failure logs a warning and the run continues without frames.
- **Frames-done** (`frames_done.frames_done_step`; split path: `then_frames_done`, which first waits for `plan_done`): renders `ctx.visual_annotations` — the `<visual_annotations>` block, chronological `[m:ss] caption | on-screen text` (`pipeline/visual_annotations.py`), **never** written into `clean_text` — then `triage.frames_done_events` decides the code tabs reconcile deferred: code on screen (vision `scene_type: code`, or code-shaped OCR) keeps them and backfills tech's `code_playground`; no code → the step-1 chain. A changed tab list sends a fresh **`meta`**. Then `ctx.frames_done` is set. Timing step `visual_inject` (name kept from the old Phase 2.5).

#### 2c. Split extraction · `phases/split_extraction.py:run_phase_split_extraction`

The most expensive stage, and the one v9 changed. It waits for `ctx.plan_done`, then:

1. **Chunks** [`pipeline/extraction_chunks.py:plan_chunks`]: ONE chunk (the whole marked transcript) unless one extraction batch can't cover the video (`needs_chapter_batching`: duration > `MAX_MINUTES_PER_BATCH` (40 min) or estimated tokens > `MAX_TOKENS_PER_BATCH` (50k)). A longer video is cut like the chunked path below: the chapter chain (creator chapters → description timestamps, awaited ≤ 5 s → the memory outline → `chapter_detect` only without a usable outline) merged by `batch_chapters`, else a sentence force-split. `CHUNKED_EXTRACTION_THRESHOLD` plays no part here. Each chunk's jobs expect its **share** of every planned count (output-quality 3.3): `ExtractionChunk.share` = its window over the video's clock (`(end − start) / video end`; S01's two chunks 0.62 / 0.38, S02's three 0.45 / 0.41 / 0.14), the job line reads `expect ~round(min(expect, cap) × share)` (at least 1; `extraction_prompt._amount`), the cap stays per call, and the chunk's `<batch_partial_context>` says so (`_build_batch_context(counts_scaled=True)`) — the chunks' counts add up to the plan's ± 1 instead of each aiming at the whole video's. One chunk has share 1.0: today's bytes; `EXTRACTION_PARALLEL=false` keeps the whole-video sentence. Every `<batch_partial_context>` — on both chunked paths — also states the clock (3.4, L21): "Clock: markers read [m:ss] before 1:00:00 and [h:mm:ss] after; every timestamp is seconds on the whole video's clock — this part's first marker [1:11:11] is 4271." (`extraction_prompt.batch_clock_line` over `extractor._batch_start_seconds`); it rides in block 3, so blocks 1 / 2 stay cached, and the window check below (`window_stamps`) stays the enforcement. The vision call's frame labels read the same clock (`frame_analyzer._frame_label` → `memory.format_clock`: `Frame 3 (at 1:11:22):`, was `71:22`).
2. **Requested fields** [`pipeline/field_requests.py:requested_fields`]: the union over the reconciled tabs of each planned dataSource (with the fields of the info_grid tabs folded into it — the host waits for them), its registry siblings (only inside a domain some tab plans on; a field of an inactive domain may take a sibling in any active domain), the rendering component's side reads (none of `learning.timestamps` for a Moments tab that plans a registered field of its own), the overview's tip/level reads, and every requested domain's scalars — nothing of an inactive domain, no unplanned modifier output.
3. **Groups** [`pipeline/field_groups.py:build_groups`, `domains.json` `grouping`]: text fields bucketed by domain, split by field when Σ `outputWeight × expect` exceeds `groupOutputBudget` (1,500), a slice estimated under `minGroupOutput` (400) joining the domain's previous slice (`_absorb_small`; with the measured key-idea / moment / ask weights a talk's plan comes to ≤ 3 text groups per chunk — S01's in `test_p1_7_grouping.py`), at most `maxTextGroups` (5) text groups ordered by the first tab they serve — **text1** holds the first planned tab; every `waitsForVisual` field forms ONE **visual** group.
4. **Prompt** [`extractor._build_prompt` cut by `pipeline/extraction_group_prompt.py:build_group_messages`]: three content blocks, no system prompt — block 1 (ttl 1h) = the rules (ending with `extraction_group_rules.txt`'s completeness, argument and never-invent-a-number rules — `completeness_rule`) + every requested domain's full schema + the primary's example; block 2 (5 min) = `<video>` + the chunk's transcript + `<video_memory>`; block 3 (uncached) = the group's job from `extraction_group_job.txt` ("emit ONLY these keys" + their shape, the tabs it serves; the visual group adds the chunk's annotations and key frames; the group requesting `learning.keyIdeas` ends with the rules file's `<argument_example>` for the run's primary — the S01 talk idea for `learning`, the S02 position for `podcast`, none otherwise — `_taught_examples`).
5. **Firing** [`pipeline/extraction_groups.py:fire_groups`]: per chunk, text1 is the only **streamed** call — its first chunk releases the chunk's other text groups (or `GROUP_RELEASE_FALLBACK_S` = 5 s), so they read blocks 1 + 2 instead of writing them (a write anyway is counted: `pipeline.timing` `counts.cacheRewrites`). The visual group waits for `ctx.frames_done`, is re-cut from what no text group asked for (nothing left → not fired) and binds the annotations + key frames then. Every call holds a slot of the run's `EXTRACTION_PARALLEL_BATCHES` semaphore; model `LLM_EXTRACTION_MODEL` (Sonnet 5.5, effort low), JSON mode, 16,384 max tokens, timeout from the group's estimate (120–300 s), 1 retry. Spans `extraction_<group>` (`_c<k>` for chunk k ≥ 1), ledger feature `summarize:extraction:<group>`.
6. **Landing** [`pipeline/group_merge.py:validate_group`, `pipeline/group_failures.py`]: each answer is validated without default materialization and cut to the group's own keys as it lands; on a chunked run its stamps are then held to the chunk's window (`extraction_chunks.window_stamps`, 0b.10 — [Assembly notes](#assembly-notes-output-quality)). A lost answer (not JSON, no key of the group, content validation dropped, a warm-up stream that broke) gets ONE more unstreamed call; a refused warm-up does not (`propagate_refusal` — asking again would reach the model that refused): it releases the waiting groups and fails; still nothing → `failed` (its fields count as landed, so no tab waits for ever). **SSE per landed call:** `extraction_progress {section: "groups", batch, of}`, then a **preview** `tab_ready` of every planned tab whose requested fields have all landed (`phases/group_emission.py:GroupLanding`): non-final, without cross-tab links and attachments, at its plan-order position — never the overview, a moment tab or the quiz. The **quiz task** starts here (`enrichment.start_quiz_task`, `ctx.quiz_task`) once the text groups holding a `quizDomains` field have landed; the **hero task** starts here too (`GroupLanding._hero_run` → `phases/synthesis.start_hero_task`, `ctx.hero_task`, timing step `hero_call`) once every text group has landed on every chunk (text-groups-done; `HERO_REWRITE_ENABLED`), over the extraction merged so far. A run that fails or is cancelled stops both (`split_extraction`, `phases/metadata.release_background_work`).
7. **Merge** [`group_merge.merge_groups`]: ONE deterministic merge — chunk-major, text1..N then visual; lists concatenate with later duplicates dropped, first non-empty scalar wins. `ctx.extraction_data`, `pipeline.extraction._groups` (one row per call), coverage + quality on the requested fields (`finish_extraction`). Every failed group marks the run `degraded`; when no group landed at all → `fail_extraction` (status `FAILED`, `LLM_ERROR`, an `error` event; the orchestrator still runs assembly afterwards). **SSE:** `extraction_complete`.

### Today's single extraction (`EXTRACTION_PARALLEL=false`) · `pipeline_orchestration._frames_done_then_extraction` → `phases/extraction.py` → `pipeline/extractor.py`

The rollback lever. After the whole phase-2 group: the frames-done step, then
`run_phase_extraction` through `run_parallel_phases` (for its heartbeats). The request
bytes for a given plan are identical to the pre-split single call (replay identity
test). It reverts **extraction only**: the plan-done / frames-done reconcile,
`<video_memory>` evidence ranges, the toolkit markers and `PIPELINE_VERSION` stay.

- **Prompt:** `extraction_prompt.build_extraction_prompt()` over `base_extraction.txt` + `schemas/{tag}.txt` + `quality_rules.txt` + `examples/{primary}.txt`: system = the rules; user block 1 = `[<video> + transcript + <video_memory>]` (5-min breakpoint); block 2 = the job (briefs + caps, schemas, example, `<visual_annotations>`, ≤ 12 key-frame captions).
- **Strategy** (`extractor._resolve_strategy`): a video longer than `CHUNKED_EXTRACTION_THRESHOLD` (900 s) with a transcript gets chapters (`phases/extraction.py:_split_chapters`, the same chain as above); with ≥ 2 chapters that `batch_chapters` turns into > 1 batch → **chunked** (each batch its own transcript slice + `<batch_partial_context>`, **one at a time** under this flag, a rate-limited batch retried in a sequential pass); otherwise ≤ 5,333 words → **single** (16,384 tokens, 240 s); a single-batch input over that is force-split into `EXTRACTION_FORCE_SPLIT_CHUNKS` batches. Without chapters: < 5,333 words → single; > 20,000 → force-split; in between → **overflow** (one call, 32,768 tokens, `300 + words/100` s, ≤ 600). Batches merge in `extraction_merger.py`.
- **SSE:** `extraction_progress` (`section: "all"`, or `"chunked"` / `"chunked-sequential"` per batch), `extraction_complete`.

### Faithfulness judge · `routes/pipeline_faithfulness.py` → `pipeline/faithfulness.py`

Once `ctx.extraction_data` exists (after the phase-2 group on the split path), the
orchestrator spawns a **fire-and-forget judge**: sampled claims
(`LANGFUSE_FAITHFULNESS_SAMPLE_RATE`, ≤ 6 per video) checked against the transcript +
the rendered visual annotations, fast tier (gpt-4o-mini with `LLM_FAST_PROVIDER=openai`), logged as a Langfuse score
(`summarize:faithfulness`). Informational — never blocks or gates the run. Drained
(≤ 25 s) before the trace flushes.

A second launch follows the first (output-quality 2.4): `pipeline_orchestration` calls
`_launch_hero_quiz_check` once `run_phase_assembly` returns, so the late group has written the
hero and the quiz. Up to two more calls on the judge model (`faithfulness_hero_quiz.py`) score every
stamped hero takeaway against the transcript around its `[m:ss]` (−30 / +90 s) as
`faithfulness_hero` — only when the hero rewrite wrote the hero (`wrote_hero`) — and 2 stamped quiz
questions of the shipped `quiz_arena` tab against their answer stamp as `faithfulness_quiz` — only
when `QUIZ_V2_ENABLED`; a run with both flags off makes no extra judge call. `faithfulness` keeps
the extraction sample. Both launches go through `pipeline_orchestration._launch_judges` (stage
`extraction` / `assembly`). Details and cost
bound: [OBSERVABILITY.md](./OBSERVABILITY.md#hero-and-quiz-sample-output-quality-24). The
claim-localized transcript windows moved to `faithfulness_context.py`.

### Assembly + late group · `phases/assembly.py:run_phase_assembly` → `pipeline/assembly/core.py:assemble_response`

Where all the streams *converge* into the final shape. Order:

1. **Description analysis** (≤ 5 s wait; past it `meta` goes without it). **SSE:** `description_analysis`.
2. **Synthesis FIRST** only when memory left the tldr or takeaways empty (`synthesis.needs_hero_fallback`): the overview and the minimum-3 Key Info fallback (a checklist of the non-blank takeaways) need them. Otherwise memory's hero seeds `synthesis_dict`. "Synthesis" is the synthesis slot, `phases/synthesis.run_phase_synthesis`: the hero rewrite when `HERO_REWRITE_ENABLED` (this phase awaits the hero task), else the synthesis call (step 5).
3. **Assemble** (pure code, no LLM): per planned tab, `resolve_data_source()` maps its `dataSource` to the extracted data with cascading fallbacks; the matching assembler from `ASSEMBLER_REGISTRY` builds the props; a label-only Moments source yields to a described one, promotion, density caps, the degrade-never-drop demote ladder, the folded info_grids join their host, domain forbidden / max policy, timestamp hygiene, no Moments tab when the steps are the timeline (D6, undone when the dedupe drops the step tab), the post-promotion dedupe, every info_grid row with a value (R5), the always-Moments tab, frame thumbnails, rank-sampled caps and true-count labels, cross-tab links, the attachments (answer tab first), the minimum-3 fallback — order and modules in [Assembly notes](#assembly-notes-output-quality). A tab a failed extraction group cost is recorded as `extraction_group_failed`. Output-quality 2.0 adds, inside `assemble_response`: the **quote pre-pass** first (`quote_verify.verify_extraction_quotes` over a copy of the extraction and the quiz, against the run's `QuoteIndex` — `run_quote_index(ctx)`, built once per run), the quiz's stamps read in seconds (`quiz_stamps.with_reconciled_quiz`), and after the caps and before the links the shadowed filmstrip and any empty-props tab leave (`tab_references.drop_shadowed_filmstrips`, `density.drop_empty_tabs`: `droppedTabs` reasons `filmstrip_shadowed_by_moments`, `empty_props`), then `tab_references.scrub_tab_references` once the tab list is final — [Assembly notes](#assembly-notes-output-quality).
4. **First tab emissions**: on the split path a **preview** of each tab the client still lacks (the overview, tabs assembly added, tabs the last groups or frames changed — `group_emission.assembled_tab_events`); on today's path every tab but the moment tabs, `final` where no late phase can change it. Every `tab_ready` goes through the run's `TabLedger` (`pipeline/tab_ledger.py`, the emission contract).
5. **Late group**, in parallel with heartbeats: **moment fill** (`assembly/moment_frame_fill.py`: a frame at each frameless moment's timestamp from the run's ≤720p file → the moment tabs stream framed, replacing the provisional tab by id or with `replaces`), the **synthesis slot** unless it ran first (`phases/synthesis.run_phase_synthesis`): with `HERO_REWRITE_ENABLED` (default) the **hero rewrite** (`hero_rewrite.txt`, `LLM_EXTRACTION_MODEL`; the split path awaits `ctx.hero_task`, `EXTRACTION_PARALLEL=false` makes the call here over the whole extraction) writes the hero — [Hero rewrite](#hero-rewrite--quiz-v2-output-quality-21--21b) — and the synthesis call (`synthesis.txt`, fast tier: masterSummary + seoDescription) runs only when the flag is off or the hero call gave none; either way it patches meta + the overview and **SSE:** `synthesis_complete` (superset), the **quiz** when `needs_quiz` (`QUIZ_V2_ENABLED`, default: `quiz_v2.txt` on `LLM_MEMORY_MODEL`, every question's quote checked against the transcript — [Quiz v2](#hero-rewrite--quiz-v2-output-quality-21--21b); `false`: `enrich_quiz.txt`, `LLM_ENRICHMENT_MODEL` = gpt-4o-mini; the split path awaits the task it already started; 3 / 5 / 8 questions by length from `quizPolicy.questionsByDuration` — `enrichment.quiz_question_count`, the first band for an unknown length, never above the `enrichment.quiz` registry cap; every question carries the `timestamp` of the sentence that answers it, and one stamped past the end + 10 s is dropped; **SSE:** `enrichment_complete`). Once the hero and the quiz have both landed, `phases/enrichment.settle_late_quiz` settles the quiz against the shipped hero ([Quiz v2](#hero-rewrite--quiz-v2-output-quality-21--21b)): quiz v2's spares leave the record, a question whose keyed option restates a shipped takeaway is dropped and refilled from them, and fewer than 3 left means no quiz tab. A landed quiz then re-assembles the response (`phases/late_quiz.py`): the quiz tab last, a sparse host's quick_quiz strip, links and the overview's count follow.
5b. **Settle** (`phases/synthesis.settle_tab_references`, after the quiz re-assembly): the Moments are framed and the hero is written, so the filmstrip they shadow leaves now, every hero / goal sentence naming a tab that does not ship goes (`tab_references.settle_shipped_tabs`; a planned tab dropped here joins `assembled["dropped"]`; a hero field or goal is never emptied — its naming clauses are cut instead, and a sentence with no clean cut stays — while a takeaway whose claim names one goes whole, closing `[m:ss]` with it), and a hero page gets its `readingMinutes` (`hero_rewrite.reading_minutes`, over the final hero + tabs). **SSE:** a corrected `synthesis_complete` superset when the hero no longer reads as the last one sent — always on a hero page (it carries `readingMinutes`), on the synthesis path only when the scrub changed it — so a run sends it up to three times. It carries `final: true`: it REPLACES the client's and the relay's copy (a field the scrub emptied clears, a hero key it lacks goes), where the earlier emissions merge ([API-REFERENCE.md](./API-REFERENCE.md#synthesis_complete)).
6. **Closing re-emit** (`TabLedger.closing_events`): every tab not yet received final goes out `final: true` at its final position, in order — the overview last after per-group streaming. Then the 720p file is deleted.
7. **`complete`** (`tabCount`, `tabIds` = the final order — a streamed tab missing there was withdrawn, `processingTimeMs`, `degraded`), then **save** (`repository.save_structured_result`: `tabs`, `meta`, the `pipeline` record — triage, reconcile, extraction, enrichment, synthesis, assembly accounting — `pipelineVersion`, status `completed` for English, **`processing`** for non-English).
8. **Shared artifacts**: Redis `response_cache.set_response()` — **English only**, awaited before `done`, best-effort; Qdrant (`phases/qdrant_index.py:index_in_qdrant`, background tasks): transcript, default-output and visual points — a non-English transcript first gets its English (Whisper translate when this run transcribed the audio, else `LLM_TRANSLATION_MODEL` (gpt-6-luna) block by block, `rag_translation`). An eval-user run writes neither Redis nor Qdrant. The S3 raw transcript (`transcript_store.store()`, background) is written either way, unless the transcript came from S3. Bug: its `rawTranscriptRef` write-back filters on the string id where Mongo stores an `ObjectId`, so it matches no row and the field stays unset.
9. **SSE:** `done` + `[DONE]` (English only).

### Assembly notes (output-quality)

Output-quality phases 0b, 1 and 2.0 (`dev/active/output-quality/`) reworked what assembly ships — pure code, under `PIPELINE_VERSION` v10 (the task's single bump is its close-out, [IDEMPOTENCY.md](./IDEMPOTENCY.md#when-to-bump-pipeline_version)); the prop shapes are in [DATA-MODELS.md](./DATA-MODELS.md#output-quality-props-2026-10). The sets, bands and thresholds assembly keys on come from `domains.json` (`assemblyPolicy`, `momentCapsByDuration`, `measureUnits`, `injectedTabs`, `infoGridHosts` — `shared_config/assembly_policy.py`, `domain_config.py`), read once per process. `assemble_response` order: per planned tab, resolve (a label-only Moments source → its described sibling) → assemble → promote → density → the moment gate → validate → demote → frames → the info_grids folded into it (a host that does not ship gives them back as tabs); then timestamp hygiene → overview first → domain requirements → the D6 moment drop → the dedupe → D6 undone when no step tab survived → every info_grid row valued (R5) → the always-Moments tab → the filmstrip → caps + labels (`_post_process_tabs`, the key-ideas cap) → cross-tab links → attachments → minimum-3 fallbacks → quiz last. The tabs assembly adds itself (overview, Key Info, Visual Moments, Key Moments) take their label, emoji and viewer-outcome goal from `injectedTabs`.

| Module (`pipeline/assembly/`) | Owns |
|--------|------|
| `component_lists.py` | `COMPONENT_LIST_KEYS`, the one component → item-list table (`item_list_keys`; a comparison = `comparisons` + `pros` + `cons`): validation's required list, the caps, the label count and the dedupe derive from it (`attachments._PRIMARY_LIST_KEY` stays its own, mirroring the FE's `COUNT_PROP_BY_COMPONENT`). `SEQUENTIAL_COMPONENTS` (`assemblyPolicy.sequentialComponents`: step_player, step_flow_canvas, workout_room): a procedure ships whole — `core._cap_tab_items` never caps or samples it (so `assemblerItemCaps.step_player` goes unused by assembly) and the dedupe never trims it |
| `moment_normalizers.py` | the MomentTrack item contract — `normalize_moment_items` (prose-bearing, timed, one per `seconds`), `_moment_start` (a stated start > 0, else the `time` clock, else a stated 0; the `time` chip re-derived from the `seconds` that ship), `_moment_text` (label + description), `_chapters_to_moments` (time-ordered spans); re-exported by `normalizers.py`. A `podcast.qa` item is a moment: its `question` is the row's label (whole up to 140 chars, else its first sentence when that fits, else a word cut) and rides whole as `question`, its `answer` the description; `quote` passes through |
| `info_grid_items.py` | the InfoGridItem contract — `_normalize_info_grid_item` (credits / `{key, value}` / keyed / keyless-tip shapes, every row carrying its emoji, evidence and anchor; `type` → `category`), `_split_string_info_item`; re-exported by `assemblers_primary.py`. R5 over what ships (`require_grid_values`, after the dedupe, tabs and folded attachments alike): a key-only row takes its `evidence` as the value, else — a one-sentence tip — its text under its category as the key (`valued_row`), else it goes; a grid of nothing but short names (≤ 40 chars, ≤ 5 words, no value) ships as `mode: "tag_cloud"`; a grid tab left with no row goes (`validation_empty_list`, detail "no row with a value (R5)") |
| `labels.py` | `shipped_count` + `true_count_label` — the FE's `hasLeadingCount` / `inferItemCount` rule, run in `core._post_process_tabs` after the caps; a number before a measure unit is no count claim (`_MEASURE_UNITS` = `domains.json` `measureUnits`, the one list `domain-meta.ts` `MEASURE_UNITS` and the output-quality tools read too) |
| `dedupe.py` | `_dedupe_tabs`, `tab_fingerprints` — the post-promotion dedupe below |
| `sampling.py` | capped lists keep items by score — never a head-slice — in source order. `rank_sample`: score tiers taken whole from the top, the first that does not fit evenly sampled (`item_richness` = evidence fields + a stamp + a ≥ 120-char text; `concept_richness` for the 30-concept canvas). `sample_moments`: the timeline cut into `cap` equal strides (by `seconds`, else by list position), each keeping its most salient moment (description 2, quote 1, number 1, ≤ 3 more for the other items and takeaways that point at it — `collect_moment_anchors`), an empty stride handing its slot to the best left. `tier_head`: the best stated tiers. Spots grouped by day or section get per-section quotas (`core._cap_spots_with_sections`); at least as many sections as the cap → one spot each from an even pick of the sections, first and last kept (`even_indices`, output-quality 3.3 — never the first `cap`, so a long video's closing section survives). A Moments track's cap is strictly content-model §9 (`domains.json` `momentCapsByDuration`, `moment_track_cap`): a label-only track gets one per 3 / 4 / 5 / 6 min for videos ≤ 15 / 15–45 / 45–90 / > 90 min, clamped to 4–6 / 8–12 / 12–18 / 18–60 (`moment_cap_for_duration`; §9 gives the last band no range, 18–60 is the registry's choice); a described track — ≥ 50 % of its moments carry a description the row shows, not the label repeated (`is_described_track`) — its band's maximum (the open-ended band: one per 6 min within 18–60); `assemblerItemCaps.moment_track` (20) only for an unknown length; either is salience-sampled when over (a moment's own `quote` counts as its quote). A key-ideas tab (`learning.keyIdeas`) is capped at `assemblyPolicy.keyIdeasCapFactor` (2) × `keyIdeasByDuration` (5 / 8 / 12 / 16 for ≤ 15 / ≤ 45 / ≤ 90 / longer minutes) = 10 / 16 / 24 / 32 (`key_idea_cap` via `core.key_idea_caps`; spot_explorer's 25 for an unknown length), never by the plan's `expect`, rank-sampled by richness in source order |
| `derived_guard.py` | nothing derived from nothing (D13): a score, tier, claim status, rest time, duration or date ships only on an item anchored by `timestamp` / `seconds` / `startSeconds` (`anchor_seconds`, `stated_scalars`, `stated_number`, `stated_rows`); unanchored it is dropped. A NaN or ±Infinity is never an anchor or a number, and `stated_rows` keeps only the rows whose field is a stated number. Callers: the verdict score and sub-scores (`normalizers._normalize_verdict`), tiers (a tier list with none stated is refused) and claim statuses, step / exercise durations and rests, spot cost / duration / rating, the info_grid, spot and stat_banner anchors, the radar axis (`is_numeric_axis`) — and the overview guard: `assemblers_learning._domain_scalars` spreads the primary block's scalars through `stated_block_scalars`, so an unanchored score or `rating` block never reaches the overview. **D13 extended (2.0 c):** a *verdict* — a score, a tier, a status (a review verdict's `badge` included), every scalar of a `rating` block (`QUOTED_FIELDS`) — also needs the creator's words, a non-empty `quote` on the item (`has_quote`), which the quote check ([below](#quote-check-and-tab-references-output-quality-20)) has verified or kept as a translation; an anchored item without one keeps its prose and stamp and loses the verdict (a claim ships with its `timestamp` and no `status`). A tier list with no tier that is both stated and quoted is refused and walks the demote ladder (`tier_list` → `spot_explorer` → `info_grid` → `display_section`) to a spot_explorer of the same items (`degradedFrom: "tier_list"`; `normalizers._normalize_to_spot` reads `item` → name, `reason` → description), its goal cut before the first clause that names tiers, ranks, ratings or a best / worst / highest / lowest, or — when its opening clause does — replaced by the registry's goal for the shipped component (`tab_references.goal_without_lost_structure`, `registry_goal`: the domain's first defaultTab rendering it, else `injectedTabs.keyInfo`) |
| `attachments.py` | `attach_response_secondaries` — the overview is never decorated; the first content tab that takes an attachment is the answer tab (an "In short" `summary_header` from the extraction `summary` whatever its size, the `stat_banner`); a sparse tab (≤ `assemblyPolicy.sparseItemsMax`, 4 items) gets one of frame_strip → quick_quiz → tip_callout, each once per response (a filmstrip tab pre-consumes the frame_strip; none for `noFilmstripDomains`); any other dense tab (≥ `denseItemsMin`, 10), or a dense answer tab when the extraction has no `summary` or a summary that only repeats the goal, a "First up" one (its first item); a header is never the goal; a folded info_grid attachment stays after them |
| `stat_banner.py` | the answer tab's numbers strip, split out of `attachments.py` (which re-exports its names): `_key_idea_tile` turns each anchored `kind: "number"` key idea with a model-stated `figure {value, label}` into a tile — no text is mined; `_stat_banner_attachment` ships ≥ 5 tiles, at most 8, else none — the rule in [DATA-MODELS.md](./DATA-MODELS.md#output-quality-props-2026-10) |
| `assemblers_learning.py` | `key_idea_props`: a `learning.keyIdeas` list (every item with `claim` + `why`, `is_key_idea_list`) ships as spot_explorer cards — claim → name, why → description, quote, timestamp, kind → `category` badge ("Thesis"), speaker; `section` → spot sections in first-appearance order (none below two); below spot_explorer's minimum of cards the assembler returns None (the usual demote / drop path) |
| `quote_spots.py` | a spot_explorer tab on `podcast.quotes` / `narrative.quotes` ships one card per quote: the speaker as its name (an unattributed quote is named by its own head, ≤ 60 chars), the line verbatim as `quote`, the context as description, the seek point |
| `moment_sources.py` | R7: a label-only Moments list yields to its first described registry sibling that no other planned tab ships from (`described_moment_source`; S01's `learning.timestamps` → `narrative.keyMoments`), before the moment fallback chain; past the assembler a track failing the moment gate (`density.moment_gate_passes` = `is_described_track`) yields to the fallback chain's track when that one passes — the memory outline with its `establishes` lines (`described_or_fallback`); with none it ships as it is |
| `steps_timeline.py` | D6 on the assembled tabs (`dedupe_under_steps_timeline`): `drop_moments_when_steps_are_the_timeline` before the dedupe, `restore_moments_without_steps` after it — when no step tab outlived the dedupe the removed Moments tabs go back at their places and their `steps_are_the_timeline` rows go, and the response is treated like any other (the always-Moments rule applies again). The primaries and step components come from `assemblyPolicy` (`stepTimelinePrimaries`, `sequentialComponents`) |
| `info_grid_fold.py` | R6, info_grid never stands alone: a planned grid reconcile folded into its host (`fold_host`, `fold_entry` at plan-done) joins the host when it ships (`apply_folds`) — a spot_explorer host takes the rows as cards (key → name, value → description, the row's timestamp, the category badge; the row's `evidence` is the card's `quote` only when it is the item's checked quote — carrying its `quoteTranslated` flag — else it ships as prose after the description, `promotion.spot_evidence`, the same rule as the info_grid → spot_explorer promotion) in a section named after the grid's label, a valueless row dropped; any other host takes the grid as a bottom attachment. A host that does not ship gives its folds back as tabs (`unfolded_tabs`); a grid reached only by demotion is no planned tab and stays one |
| `ingredients.py` | `ingredient_row`, the checklist row of a food ingredient: amount + unit + name; an amount that is no quantity ("liberally", "to taste") leads the note instead, never the label; "amount not stated" is no amount (R10) |
| `quiz_stamps.py` | quiz times in seconds (2.0 a, G1-1): a stamp that reads as the minutes of the clock its explanation cites takes that clock; one past the video's end takes the first cited clock; any other stamp stands; an explanation citing one clock more than `CLOCK_AGREEMENT_S` (10 s) from an in-video stamp cites the stamp's clock instead; a bare `(1322)` is read as a second count only when it agrees with the stamp (within `CLOCK_AGREEMENT_S`) or follows "at" / "around" / "from", never with 1–2 digits (a list number) and, off the stamp, never as a year (1000–2099: "a study (2008)" stays text); a second count renders `(m:ss)` inside the video, else goes with its "at" and comma. `with_reconciled_quiz` runs in `assemble_response`, `reconcile_quiz_time` in `enrichment.parse_quiz`; applying it twice changes nothing |
| `tab_references.py` | nothing names a tab that does not ship (2.0 b, G1-2): `drop_shadowed_filmstrips` (the web's rule, run server-side: ≥ `RICH_MOMENT_FRAMES` = 8 frame-backed moments in a `moment_track` shadow every `video_filmstrip`), `drop_records` (a planned tab removed here is a `droppedTabs` row, reason `filmstrip_shadowed_by_moments`, or `empty_props` from `density.drop_empty_tabs`), `scrub_tab_references` (every sentence of the hero — `tldr`, `masterSummary`, `seoDescription`, `brief`, `worthItFor`, `skipIf`, the takeaways, the overview's copy — and of each tab's `goal` that names a dropped tab's label is removed whole; a label of any length names the tab only in a sentence about "tab(s)" / "section(s)" (any case) or in its own case past the sentence's first word — from the sentence's first word too when a later word of the label is capitalized, which sentence case never writes; `_scrub_text` never empties a field: when every sentence names a dropped tab, only the naming clauses go and a sentence with no clean cut stays; a takeaway's closing `[m:ss]` is stripped before the split and the takeaway goes whole when its claim does), `recount_overview`, `goal_without_lost_structure` / `registry_goal` (a degraded tab's goal, above); `settle_shipped_tabs` repeats the drop and the scrub after the late group (`phases/synthesis.settle_tab_references`) |

**Post-promotion dedupe** (`dedupe._dedupe_tabs`, R5). Reconcile's plan-time `dedupe_step` sees only `(dataSource, component)` pairs (a component and its `COMPONENT_PROMOTIONS` target count as one; of two, the tab the primary requires — equivalents included — stays, else the first); promotion and the sibling fallback can still ship one list twice, so assembly compares what ships:

- after the requirement backfill and the D6 drop, on the assembled props; the overview never takes part;
- a tab's items are its component's own lists (`component_lists.item_list_keys`; every list prop for an unknown component), an item's identity its first text field (`_ITEM_TEXT_KEYS`: label, title, name, term, question, claim, text, …), whitespace-collapsed and case-folded;
- a tab whose items ship ≥ 50 % (`DUPLICATE_OVERLAP`) on a tab that stays is its twin. The earlier twin stays whole — unless only the later renders a component the domain requires (`core.required_components`, equivalents included); then the later stays whole and each earlier twin whose items ship ≥ 50 % on it is settled instead;
- a settled twin (`_settle`) goes only when every item it ships also ships on a tab that stays; otherwise it sheds those shared items and ships its own — except a procedure (`SEQUENTIAL_COMPONENTS`) and a twin the trim would leave without the list it was validated on (`COMPONENT_LIST_KEYS`: a comparison whose own items are pros, not rows), which ship whole. A trimmed itinerary's `sections[].spotIndices` are re-indexed (a section left empty goes); a trimmed canvas keeps no connection to a removed concept and no empty group. A tab is removed by identity, never an equal tab before it;
- each record is a `droppedTabs` row with the tab's planned `dataSource`: a twin that goes, reason `duplicate_tab`, `detail` "N% of its items ship on <id>"; a trimmed twin, reason `duplicate_items`, `detail` "<removed> of its <total> items ship elsewhere (<id>)" — it ships, so `core.count_dropped_tabs` leaves it out of `tabsDropped`; a twin that ships whole leaves no row;
- upstream, a planned tab's fallbacks — cross-domain (`_cross_domain_fallback`) and in-domain sibling, its required-list and generic longest-list branches included — refuse a field another planned tab ships from (`core._planned_claims`; a tab that drops releases its claim); the requirement backfill's fallbacks pass no claims.

**Per-chunk timestamp window** (`extraction_chunks.window_stamps`, 0b.10 — split extraction, before assembly). Wired in `extraction_groups._read` → `_windowed`, right after `group_merge.validate_group`, on every landed answer:

- one chunk (a video one batch covers) is open at both ends: nothing is checked and the data comes back as is, so the ≤ 40-min replay stays byte-identical; `EXTRACTION_PARALLEL=false` has no check;
- a chunk's window runs from its first marker to the next chunk's, with 30 s of grace at an inner edge (`WINDOW_GRACE_S`) and none past the video's end (`ExtractionChunk.video_end_s`); it holds the integer `seconds` / `timestamp` / `endSeconds` / `endTimestamp` of every stamped item it reaches;
- a stamp outside gets its hour back when + 1…24 h lands it inside (the model read `[h:mm:ss]` without the hour: S02 stamped 1:13:41 as 821 s) — once, first fit; a `seconds` item may take its own `time` clock instead; a stamp ≤ 0 (a 0 is the model's "no stamp", a negative a sentinel) is never hour-restored — a `seconds` item's fitting `time` clock wins over it, else a 0 stays only inside the window and a negative never (cleared or dropped as below);
- a stamp nothing fits is cleared (`None`) and the item stays — except a list item whose own `seconds` fits nowhere (`learning.timestamps`), which is dropped; a range end left before its start, or without it, is cleared;
- every move is counted per field path (`StampReport`), logged, and merged into the call's validation report (`_merge_stamps`; `merge_report` across groups) → `pipeline.extraction._dropped[path] = {dropped, total, restamped, cleared}`, gate 1's timestamp-drift reading (D17).

**No provisional Moments tab for step-timeline plans.** `moment_fallback.steps_are_the_timeline(tabs, primary_tag, required)` holds only when the primary domain is food, fitness or project (`assemblyPolicy.stepTimelinePrimaries`), its required components leave out `moment_track`, and a `step_player`, `step_flow_canvas` or `workout_room` tab is there (`sequentialComponents`; an unnamed plan tab counts by the component its id infers) — a podcast or lecture with a stray step tab keeps its Moments. `provisional_moments_tab(plan_tabs, video_meta, description_analysis, primary_tag, requirements)` — `phases/moments.py` passes the primary and its `effective_requirements` — returns None then (and when the requirements forbid `moment_track`), so plan-done streams nothing: assembly decides the same on the assembled tabs after the requirement backfill (`steps_timeline.drop_moments_when_steps_are_the_timeline`, over `required_components`), drops every `moment_track` tab of such a response (reason `steps_are_the_timeline`) and adds no always-Moments tab, so a preview would only be withdrawn at `complete` — unless the dedupe then drops the step tab as a twin: the Moments tabs come back (`restore_moments_without_steps`). Food and project steps never become a canvas: a planned `step_flow_canvas` ships as `step_player` (`promotion.STEP_LIST_DOMAINS` = `assemblyPolicy.stepListDomains`), which Cooking / Build mode needs.

### Quote check and tab references (output-quality 2.0)

`pipeline/quote_verify.py` (+ `quote_index.py`) answers "is this quote in the transcript the run read?" for every `quote` field (and a `quotes` list item's `text`) of the extraction and for the quiz, on a copy — the stored `pipeline.extraction` is never mutated. `run_quote_index(ctx)` builds the `QuoteIndex` once per run and keeps it on `ctx.quote_index` (None without a timed transcript: nothing is checked); `assemble_response(quote_index=…)` runs `verify_extraction_quotes` first.

- Match: normalized on both sides (NFKC, casefold, apostrophes dropped, punctuation as space, thousands separators joined, hesitation words out), exact first (the occurrence nearest the item's stamp), else fuzzy — word-trigram-anchored windows scored by `difflib` ratio, `FUZZY_THRESHOLD` = 0.9; a quote cut with "…" is fragments and every fragment must pass, in order and beside its neighbour (the longest is placed like a whole quote, each other one within `FRAGMENT_GAP_WORDS` = 40 words AND `FRAGMENT_GAP_SECONDS` = 60 s of it; a fragment under 3 words passes only as an exact match inside that range). The matcher (`QuoteIndex`, `normalize_words`, the status constants) lives in `pipeline/quote_index.py`; `quote_verify` re-exports it and keeps the extraction pre-pass.
- Verdict on the copy (written as `quoteStatus` on every checked item; the overview drops the key, `assemblers_learning._quote_scalars`): `verified` — kept; `unverified` (an English source, not found) — the quote is cleared to `None` (so `derived_guard.has_quote` sees no quote and the verdict beside it goes) and a quote-list item leaves the list; `translated` (any other source language, not found — the extraction runs in English, so a Hebrew talk's quotes are renderings) — kept as prose and flagged `quoteTranslated: true`, which the spot, moment, narrative-quote and quiz cards carry (`mark_translated_quote`) and the web renders without quote marks.
- `quiz_v2` runs the same `verify_quote` on its own questions ([below](#hero-rewrite--quiz-v2-output-quality-21--21b)); the pre-pass then checks the shipped quiz again and flags a translated quote.

### Hero rewrite + quiz v2 (output-quality 2.1 / 2.1b)

**Hero rewrite** (`HERO_REWRITE_ENABLED`) — `pipeline/hero_rewrite.py` (the call, the retry, the persisted meta) + `hero_job.py` (the request), `hero_validate.py` (the checks) with `hero_text.py` (the prose readers they use), `hero_compact.py`, `models/hero_types.py`, prompt `hero_rewrite.txt`. One call on `LLM_EXTRACTION_MODEL` (stage / span `hero_rewrite`, ledger feature `summarize:hero_rewrite`, JSON mode, 2 000 max tokens, 25 s) replaces the synthesis call; memory's hero stays the preview.

- **When:** split path — `GroupLanding._hero_run` starts `ctx.hero_task` at text-groups-done (`start_hero_task`, timing step `hero_call`; the later `synthesis` step is only the wait), over the extraction merged so far, so it can run beside the visual group; the late group awaits it (`phases/synthesis._hero`). `EXTRACTION_PARALLEL=false` — the late group calls `hero_rewrite_for_run` over the whole extraction (no cache blocks to read). Nothing extracted → no call.
- **Request:** the field groups' cached blocks 1 (+ 2 on a one-chunk run), then the job (`hero_job.job_text`): the compacted merged extraction (`hero_compact.compact_for_hero`, ≤ 5 000 tokens, deterministic, every stamped item under an id — K key idea, M moment, C claim … — numbered over the whole extraction before any cut; a quote the run's quote index reads as never said is left out of the item's line and of the evidence, so no number or name only an invented quote holds can carry a takeaway) + the planned tab labels; the prompt's `{stamp_rule}` is filled per request (below). Cache layout: [llm-cost-model.md](./llm-cost-model.md#where-cache_control-is-set).
- **Answer:** `tldr` (the thesis, ≤ 200 chars — the prompt asks 140; memory's preview keeps its own 150), `brief` (3–5 sentences), `keyTakeaways` (3 / 5 / 7 by length, `memory.takeaway_count`; each cites an extraction id and **code copies that item's timestamp**, moved to where the item's quote is said when the quote verifies within 240 s of it (`QUOTE_STAMP_MAX_SHIFT_S`; an answer runs on past its question's stamp), so the prompt asks each takeaway for the claim that quote makes, from one item, never from a line without an id (it has no stamp) — a model-typed `m:ss` is accepted only when no item carries the claim, and only where the call read the transcript (block 2): a chunked run or `EXTRACTION_PARALLEL=false` is told to always cite a `ref`, and its typed stamp stands only within 30 s (`TYPED_STAMP_MAX_GAP_S`) of an `<extracted>` stamp or a `<video_memory>` clock), `worthItFor` / `skipIf` (≤ 20 words each, D18; the prompt asks 16), `seoDescription` (≤ 160 chars).
- **Checks** (`hero_validate.check_hero`, against what the call read): every number in the hero is in the evidence (G1-6); sizes; each takeaway resolves to a second inside the video; a double-quoted span of 3+ words in the tldr, brief, takeaways or either line is looked up with `quote_verify.verify_quote` (an English-source quote the transcript never says fails its sentence / field / takeaway; a translated one loses its marks and ships as prose; no quote index, nothing checked); no tab is named (a planned tab's label in its own case past the sentence's first word, or in any case in a sentence about tabs / sections / pages — the problem names the label) and the page is not pointed at; D18 heuristics — a creator judgement (`hero_text.CREATOR_JUDGEMENT_RE`: a word that only judges a person, or a verdict word next to a person subject — "the host is boring", "his delivery was weak" — never a verdict on an idea) and a proper name the evidence never contains — a capitalized word past the line's start or a multi-word capitalized run; never a term with non-letters (Q&A, C++, .NET), an all-caps acronym of ≤ 4 letters or a listed format word (proxies, not proofs). One retry, only where a second answer can change the page — a timeout, a non-JSON answer, one `finalize` cannot turn into a hero, or one that would ship a D18 line empty (`drops_a_line`); what `finalize` repairs itself (a set over the band, a brief over 5 sentences, a failing brief sentence while 3 remain, a failing SEO line) costs no second call — carries the failed checks; `finalize` then drops a failing optional line (`""`) or a failing brief sentence while 3 remain (the tldr is never cut), and returns None for a failing tldr / brief / takeaway set — the caller keeps memory's hero and runs today's synthesis. Never raises.
- **Meta:** `hero_meta` — `tldr`, `brief` (also `masterSummary`, for older readers), `keyTakeaways` as strings `"<claim> [m:ss]"` (`[h:mm:ss]` past an hour: the shape memory's takeaways have, so every reader keeps `string[]`), `worthItFor`, `skipIf`, `seoDescription`; `readingMinutes` is added by `settle_tab_references` once the tabs are final (`hero_rewrite.reading_minutes`: 238 words a minute over the hero and every non-overview tab's label, goal and props; at least 1). Event and storage: [API-REFERENCE.md](./API-REFERENCE.md#synthesis_complete), [DATA-MODELS.md](./DATA-MODELS.md#output-quality-hero-and-quiz-2026-10).

**Quiz v2** (`QUIZ_V2_ENABLED`) — `pipeline/quiz_v2.py`, prompt `quiz_v2.txt`; `phases/enrichment._quiz` picks it over `enrich_quiz`, scheduling unchanged (`start_quiz_task` early on the split path, the late group awaits it, timing step `quiz_call`). Calls on `LLM_MEMORY_MODEL` (Haiku 5.5; stage / span `quiz_v2`, ledger feature `summarize:enrichment`; 18 s per attempt, 1 retry, no cache breakpoint; every call ends by the one 37 s stage deadline) read the run's marked transcript and the key ideas (`quiz_v2_ideas.KEY_IDEA_SOURCES`: `learning.keyIdeas` first everywhere, then the domain's nearest list) and write the video's count (3 / 5 / 8) plus `SPARE_QUESTIONS` = 2 candidates. With a known length and stamped key ideas (`quiz_v2_prompt.build_quiz_v2_prompts`; output-quality 3.0 — told "one per stretch", then given a count per stretch, Haiku still wrote nothing past 37:30 of S03's 57:14) it is **one call per third** of the video that holds a key idea, in parallel (`asyncio.gather`): call k asks for its quota (`quiz_v2_select.stretch_quotas`: one per third with a key idea, the rest by key ideas held, adding up to the count + 2), names its range ("inside this stretch of the video: 38:10–57:14"), and reads only the key ideas stamped in its third and the transcript lines within 30 s of it — a call that fails or runs out of time loses only its third, and the replies merge by rank (every call's first, then every call's second …; a question written twice kept once). Without quotas (an unknown length, no stamped key idea) it is one call over the whole video. Each question carries a ≤ 25-word evidence `quote` and its answer's `timestamp` (asked as written, `m:ss`, and read to seconds by code — `pipeline_types.quiz_timestamp_seconds`; the prompt no longer asks for a "Which is NOT …" question; it asks for the correct option first in ≤ 12 words and each wrong option built from it by changing the one detail that makes it right, within 2 words of its length — output-quality 3.0). Each reply is read whole or, when its envelope is broken (gate 2's S02: one `}` too many), question by question (`quiz_v2_parse.quiz_items`: `raw_decode` over the `quiz` list; the longer read wins); a reply in which no question parses is asked for **once more** — that call's only second call, inside the same deadline. `keep_checked` runs every quote through `verify_quote`: `verified` — the stamp is **set from where the quote was found**; `unverified` — dropped; `translated` — kept only with a stamp inside the video, shown as prose; no quote — dropped. Of the survivors `quiz_v2_select.select_quiz` ships the video's count: the first clean question of each third, then the rest in the model's (importance) order — a question whose key is over 1.5× the mean wrong option's length (`length_tell`, counted in the `length_tell=N` log line) ranks after every clean one and ships only when nothing clean is left; the shipped questions keep the model's order. `shuffle_quiz` then deals the shipped keys' positions evenly (no position more than ⌈k / 4⌉ of k questions; the generator is seeded with a SHA-256 of the questions' texts, so every process deals the same) and shuffles the wrong options around each key, `correctIndex` moving with its option; the spares are dealt on their own. Fewer than `MIN_QUIZ_V2_QUESTIONS` = 3 shipped → no quiz (and no quiz tab); no timed transcript → no call. The checked questions not shipped ride on as `EnrichmentData.spares` (clean ones first; never dumped; `phases/enrichment` keeps them under `QUIZ_SPARES_KEY` in the run's record, never emitted or stored). The quiz reads memory's hero while the hero rewrite writes the shipped one in parallel, so `phases/enrichment.settle_late_quiz` runs in the late group once both landed, before the quiz re-assembly: with `QUIZ_V2_ENABLED`, a question whose keyed option restates a shipped takeaway (`quiz_v2.restates_the_hero`: ≥ 60 % of the option's stemmed content words, at least 3 — or all of a shorter option's — are in the takeaway) is dropped and each drop is refilled at the end by the first clean spare stamped in the dropped question's third, else the next spare in order (`quiz_v2_select.spare_for`), up to the quiz's own length; fewer than 3 left → no quiz tab. With the rewrite off the shipped takeaways are memory's. The flag-off `enrich_quiz` quiz stays as written. `QuizQuestion.quote` is left out of a dump when unset, so the flag-off wire and stored shapes are unchanged.

### Transcript provenance write · `routes/pipeline_orchestration.py:_record_transcript_outcome`

- Called from the `finally` around the phase-2 group: builds the block with `transcription/transcript_meta.build_transcript_meta()` and persists it through `repository.set_transcript_meta()` — a dedicated `$set`-only writer (`save_structured_result` would `$unset` the `forceRefresh` bypass marker mid-run).
- Runs for successful **and** failed fetches (a `TranscriptError` still propagates afterwards) and lives inside the Langfuse `pipeline_trace`, mirroring `transcriptSource` / `transcriptType` / `transcriptAttempted` / `transcriptOutcome` onto the trace.
- `clear_transcript_meta()` runs at the `processing` transition and on the Redis fast path, so a row re-run on the same `_id` never keeps a stale block. Field shape: [DATA-MODELS.md](./DATA-MODELS.md).

### Translation · `phases/translation.py` → `pipeline/translation.py` *(non-English only)*

- **Prompt:** `prompts/translate_flat.txt` — `LLM_TRANSLATION_MODEL` (gpt-6-luna, reasoning off), ≤ 2,500-char batches (CJK weighted) of ≤ 100 strings, up to 8 in flight, each checked for an echo; a failed batch retried once as two halves, a half that still fails kept in English.
- **Calls:** `translate_to_source()` — walks the **English** assembled output, translates every translatable prose string into `source_language_code`, returns the unchanged English dict with the translated artifact nested under `sourceLanguage`. Plus the title (the one field that travels source → English; original kept under the source block).
- **On failure / mirror / length mismatch:** no `sourceLanguage` key → the phase finalizes the doc `completed` English-only (the FE renders no toggle).
- **Out:** `ctx.source_language`; re-saves the Mongo doc with `sourceLanguage` + status `completed`; writes the final Redis payload (English primary + toggle).
- **SSE:** `phase: translation`; then the **orchestrator** (not the phase) emits the deferred `done` + `[DONE]`. A translation that raises leaves the doc `processing` (retriable) and emits no terminal event.

### Run timing · `routes/run_timing.py` + `services/pipeline/pipeline_timing.py`

Every phase and sub-step (`text`, `transcript`, `plan`, `memory`, `frames`, `frames.*`,
`visual_inject`, `split_extraction`, `extraction`, `assembly`, `moment_fill`,
`synthesis`, `enrichment`, `quiz_call`, `hero_call`, `translation`, …), every LLM call (tokens, cache
reads / 1h / 5m writes, cost, retries, fallbacks, `ttftMs` for the streamed warm-up), every
failed attempt and every download lands in `pipeline.timing` (schema version 4,
DATA-MODELS.md) and a one-line `DONE` log (`log_run_summary`). On the split path the
`transcript_frames` wall spans the extraction too; `extraction` = plan-done → last group
landed.

Version 4 (4.0b) records refusals: a declined answer is an `llmFailures` row with
`error: "Refusal"`, `refusalCategory` and what it was billed (`inputTokens`,
`outputTokens`, the four cache-token fields, `costUsd`); every row of its one re-send on
the previous model — the answer in `llmCalls`, or a refusal / error / timeout in
`llmFailures` — carries `refusedModel` (the answer also `refusalCategory`); a timeout
gets it only under `call_deadline` (`LLMService` calls): a re-send cancelled by frame
vision's `asyncio.wait_for` or the description analysis' 30 s cap is a plain
`TimeoutError` row under the refused model, without `refusedModel`. `counts`
gains `refusals` (calls refused), `refusalFallbacks` (answered by the previous model) and
`refusalsUnanswered`; `counts.fallbacks` counts only the cross-provider fallback and
provider-side model swaps; the run's `costUsd` includes refused calls.

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
                                         │  (quiz task starts when its groups land;
                                         │   hero task at text-groups-done)
                                         ▼
                        merged extraction_data          ← THE FACTS
                                         │
            ┌────────────────────────────┼─────────────────┐
            ▼                            ▼                 ▼
     faithfulness judge             ASSEMBLY (code)   coverage / quality
     (background)          tabs(plan) × extraction × frames × memory hero
                                         │
                       late group: moment fill ∥ hero rewrite (else synthesis) ∥ quiz
                                         │
               settle (tab refs, readingMinutes) + closing re-emit ──► assembled_tabs + meta
                                         │                      ← THE PRODUCT
                                         │   (then the hero + quiz judge, background)
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
**primary** model is `LLM_MODEL`, else the `LLM_PROVIDER` default (`MODEL_MAP`; anthropic:
`anthropic/claude-sonnet-5-5`) — with the stage defaults below only the plan (and
`/health`) reads it; the **fast tier** is `LLM_FAST_MODEL`, else the fast model of
`LLM_FAST_PROVIDER` (or `LLM_PROVIDER`) — `openai/gpt-4o-mini` with
`LLM_FAST_PROVIDER=openai` (prod, `.env.example`), Haiku 4.5 without. A stage setting's
default is in `config.py` and in both compose anchors as the same literal, so a blank
`.env` line keeps it; an empty value outside compose falls back as shown. The defaults
come from the 4.0d model A/B (`dev/completed/pipeline-1min/gates/g4-model-ab.md` §Picks).

| Prompt file | Stage | Loaded by | Model (default) |
|-------------|-------|-----------|-------|
| `description_analysis.txt` | metadata (t=0, background) | `video/description_analyzer.py` | `LLM_DESCRIPTION_MODEL` = `openai/gpt-6-luna` → fast tier |
| `tier_probe.txt` | tier probe | `pipeline/tier_probe.py` | `LLM_CLASSIFIER_MODEL` = `anthropic/claude-haiku-4-5-20251001` → fast tier |
| `plan.txt` + `component_toolkit.txt` | plan | `pipeline/plan_prompt.py` | primary = `anthropic/claude-sonnet-5-5`, effort medium (static half cached 1h) |
| `memory.txt` | memory (∥ plan) | `pipeline/memory.py` | `LLM_MEMORY_MODEL` = `anthropic/claude-haiku-5-5` → `LLM_EXTRACTION_MODEL` → primary |
| `vision.txt` | frames (vision) | `media/frame_analyzer.py` | `LLM_VISION_MODEL` = `anthropic/claude-sonnet-5-5` → primary |
| `chapter_detect.txt` | long-video chunking — > 1 batch and no usable outline | `transcription/transcript_chunker.py` | fast tier (`LLM_CHAPTER_DETECT_MODEL` exists in `config.py`, but no compose anchor passes it) |
| `base_extraction.txt` + `schemas/*.txt` + `quality_rules.txt` + `examples/*.txt` | extraction (both paths) | `pipeline/extraction_prompt.py`, `extractor._build_prompt` | `LLM_EXTRACTION_MODEL` = `anthropic/claude-sonnet-5-5` → primary |
| `extraction_group_job.txt` | split extraction: each group's job block | `pipeline/extraction_group_prompt.py` | (as extraction) |
| `extraction_group_rules.txt` | split extraction: the completeness, argument and never-invent-a-number rules, `<quality_rules>`' last paragraph in block 1; its `<argument_example>`s close the key-ideas group's block 3 (read once per run, `with_group_rules`) | `pipeline/extraction_group_prompt.py`, `phases/split_extraction.py` | (as extraction) |
| `hero_rewrite.txt` | the hero rewrite (late group's synthesis slot; `HERO_REWRITE_ENABLED`) | `pipeline/hero_rewrite.py` | `LLM_EXTRACTION_MODEL` = `anthropic/claude-sonnet-5-5` → primary (the extraction's cached blocks precede it) |
| `synthesis.txt` | synthesis — only when the hero rewrite is off or gave no hero | `pipeline/synthesis.py` | `LLM_SYNTHESIS_MODEL` (blank) → fast tier |
| `quiz_v2.txt` | the quiz (`QUIZ_V2_ENABLED`) | `pipeline/quiz_v2.py` | `LLM_MEMORY_MODEL` = `anthropic/claude-haiku-5-5` → `LLM_EXTRACTION_MODEL` (config only) → fast tier |
| `enrich_quiz.txt` | the quiz when `QUIZ_V2_ENABLED=false` | `pipeline/enrichment.py` | `LLM_ENRICHMENT_MODEL` = `openai/gpt-4o-mini` → fast tier |
| `translate_flat.txt` | output translation + title + the RAG transcript's English | `pipeline/translation_flat.py` | `LLM_TRANSLATION_MODEL` = `openai/gpt-6-luna` → fast tier |
| (inline `_JUDGE_PROMPT`) | faithfulness judge (background) | `pipeline/faithfulness.py` | `LLM_JUDGE_MODEL` = `openai/gpt-6-luna` → fast tier |
| (inline `_HERO_PROMPT`, `_QUIZ_PROMPT`) | the judge's hero + quiz sample (up to 2 calls, after assembly: the hero's only when the hero rewrite wrote it, the quiz's only with `QUIZ_V2_ENABLED`) | `pipeline/faithfulness_hero_quiz.py` | `LLM_JUDGE_MODEL` = `openai/gpt-6-luna` → fast tier |

`prompts/detection/category_rules.json` is not an LLM prompt: the metadata category
rules (`video/youtube.py`) behind the visual-tier fallback and the plan's category hint.
`LLM_FALLBACK_PROVIDER` (when set) gives primary-model calls a cross-provider fallback
after one same-provider retry (`utils/llm_retry.call_llm_with_retry`; the provider's
`MODEL_MAP` default — `openai` → `openai/gpt-4o`). Every request is shaped for the model it goes to
(`services/llm_model_rules.request_for_model`: thinking off + effort, only the sampling
params the model accepts, no assistant prefill), and an answer a model refuses is asked
for once more on its previous model (`config.PREVIOUS_MODEL`: Sonnet 5.5 → Sonnet 4.6,
Haiku 5.5 → Haiku 4.5, gpt-6-luna → gpt-4o-mini) — ERROR-HANDLING.md
§LLM Error Handling.

### What each prompt asks for (output-quality)

A talk, lecture, podcast or interview is read as an argument; counts follow the video's
length (content-model §9). Phase 1's prompts add no call; phase 2 swaps two (`hero_rewrite.txt` for `synthesis.txt`, `quiz_v2.txt` for `enrich_quiz.txt`, each behind its flag) and adds the judge's two background calls (each only when its flag's call wrote the hero / quiz).

| Prompt | Asks for |
|--------|----------|
| `tier_probe.txt` | 19 formats; the spoken-word ones by what the speaker does — `talk` makes a case (keynote, conference, TED-style), `lecture` teaches, `interview` is a host questioning a guest (a "Name: topic" title or a guest introduced in the opening minutes), `podcast` the regular hosts among themselves; a keynote without code is learning + talk. `<domain>:<format>` picks the playbook, directly or through `playbookAliases` (`review:commentary` / `comparison` → `review:review`; `news:news` / `documentary` / `news_commentary` → `news:explainer`) |
| `plan.txt` + `component_toolkit.txt` | four spoken-word examples (a talk, a lecture, a podcast, an interview). `<spoken_word>`: the answer tab is spot_explorer on `learning.keyIdeas` (claim, why, quote, timestamp, section, kind); Moments say what was established, never chapter jumps — a podcast's are `podcast.segments`, an interview adds `podcast.qa`; asks → checklist on `learning.takeaways` only with 3+; concept_canvas never first; quotes ride on the items. `<goal_rule>`: a goal is the viewer's outcome ("After this you know / can …", ≤ 15 words), never the component, the topic, a count or the label. `<sizing>`: `expect` by length — key ideas 5–8 / 8–12 / 12–16 / 16–20, moments one per 3 / 4 / 5 / 6 min, quiz 3 / 5 / 8 / 8 for ≤ 15 / ≤ 45 / ≤ 90 / longer minutes — never above the dataSource cap, the last ten minutes represented. Labels carry no digits. claims_tracker on `has_claims` for news, learning, podcast and tech (stated / supported / contested). The toolkit: spot_explorer is the argument tab, info_grid a lookup table, never the first tab and never for arguments |
| `memory.txt` | tldr = the thesis, a position a reader could disagree with (≤ 150 chars); `{takeaway_count}` takeaways (3 / 5 / 7), most important first, each ending with its `[m:ss]`, a number only when it is the claim; one `establishes` line per outline section (≤ 12 words: the finding, decision or argument, not the topic); never a duration, quantity or number the speaker did not say |
| `extraction_group_rules.txt` | completeness (every step, item or command); arguments (every claim with its reasoning and the sentence that carries it, never two merged, never the last 10 minutes skipped); never invent a number (an unstated duration, rest time, quantity, count or score stays null); two worked `learning.keyIdeas` items — a talk's (primary `learning`) and a conversation's position with `speaker` (primary `podcast`) |
| `base_extraction.txt`, `quality_rules.txt` | the component map in registry names (which field arrays feed which component); never a duration, rest time, quantity or number the speaker did not say |
| `schemas/*.txt` | learning: `keyIdeas`, `timestamps` with a required `description` + `quote`, `concepts[].timestamp`, `claims`; podcast: segments that say what was concluded + `quote`, `qa`, `claims`; tech: `topics` as `{name, description, timestamp}`, `claims`; news: the shared claim shape; review: `rating` and `verdict` stamped; gaming: each ranking stamped; food / fitness: durations and quantities only as stated, else null; travel: spots with `timestamp` + `quote` (fields: [DATA-MODELS.md](./DATA-MODELS.md#output-quality-extraction-fields-2026-10)) |
| `hero_rewrite.txt` | the hero from the compacted extraction (ids `K12`, `M3` …) and the tab labels: `tldr` ≤ 140 chars (the thesis, never the topic); `brief` 3–5 sentences in the video's order that never names or points at the page and claims nothing about delivery, tone or how a line lands (a demo or build is named only as `<extracted>` names it); `{takeaway_count}` takeaways, each one sentence ≤ 25 words with a `ref` to the extraction item it rests on (code copies its timestamp; `{stamp_rule}` — a typed `m:ss` instead only when the call reads the transcript and no item carries the claim, else "always cite a `ref`", `hero_job._stamp_rule`); `worthItFor` / `skipIf` ≤ 16 words each (the validator allows 20), plain and specific, about the video's content and never its creator, nothing from outside the video (D18); `seoDescription` ≤ 160 chars; every number written as it appears in the transcript, `<video_memory>` or the extraction; quote marks only around words copied exactly from the transcript or an `<extracted>` quote. Placeholders `{takeaway_count}`, `{duration}`, `{tab_labels}`, `{stamp_rule}`, `{video}`, `{extracted}` |
| `quiz_v2.txt` | the count `<task>` asks for (one call per third of the video: that third's quota; else the video's count plus 2 spares), each on one `<key_ideas>` entry (order = importance; spread across `<key_ideas>`, its last entries included), none the `<hero>` alone answers; the correct option says only what the quote says; `quote` = the speaker's words, one unbroken run ≤ 25 words copied from `<transcript>` (the English rendering when the transcript is in another language); `timestamp` = where it is spoken, the transcript's `[m:ss]` marker as written without the brackets ("3:22"; code converts to seconds), inside the range `<task>` states; the explanation teaches why and carries no clock; 4 options, one correct (code deals the key's position): the correct one first in ≤ 12 words, each wrong one built from it by changing the one detail that makes it right, within 2 words of its length; no "Which is NOT …" question; no placeholders |
| `enrich_quiz.txt` | `{question_count}` questions (3 / 5 / 8) for `{video_length}`, each on a key idea, none the `<hero>` (title, tldr, top takeaways) alone answers, nothing the video does not state; `timestamp` = where the answer is spoken, copied as written (the extracted item's own stamp, else the transcript marker as "m:ss"; code converts to seconds); the explanation cites that `(m:ss)` once |

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
| **Assembly** | Memory had the hero → no early synthesis. The overview preview; late group: moment fill frames the Moments tab (replaces the provisional), the hero rewrite — started when the text groups landed — replaces memory's hero and patches the overview, the quiz (quiz v2) joins last; the settle step counts `readingMinutes` and sends the corrected `synthesis_complete`. Closing re-emit: every tab `final: true`, overview last. `complete` → saved `completed`, cached in Redis, Qdrant points, raw transcript to S3. |
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
synthesis_complete      → superset (masterSummary, seoDescription; the hero rewrite adds brief, worthItFor, skipIf)
enrichment_complete     → the quiz (when planned)
[synthesis_complete]    → corrected superset after the settle step, `final: true` (it replaces, earlier ones merge):
                          always on a hero page (adds readingMinutes), else only when the dropped-tab scrub
                          changed the hero — up to three in all
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
| Reconcile | `services/pipeline/plan_reconcile.py` (plan-parse guard, the key-ideas domain switch), `reconcile.py`, `reconcile_pass.py` |
| `<video_memory>`, `<visual_annotations>` | `services/pipeline/video_memory.py`, `visual_annotations.py` |
| Split extraction | `services/pipeline/field_requests.py`, `field_groups.py`, `extraction_chunks.py`, `extraction_group_prompt.py`, `extraction_groups.py`, `group_merge.py`, `group_failures.py`, `schema_fields.py` |
| Single-call extraction | `services/pipeline/extraction_prompt.py`, `extractor.py`, `extraction_merger.py` |
| Tab emission contract | `services/pipeline/tab_ledger.py` |
| Hero rewrite, quiz v2, quote check | `services/pipeline/hero_rewrite.py`, `hero_job.py`, `hero_validate.py`, `hero_text.py`, `hero_compact.py` (+ `models/hero_types.py`), `quiz_v2.py` (+ `quiz_v2_prompt.py`, `quiz_v2_ideas.py`, `quiz_v2_parse.py`, `quiz_v2_select.py`), `quote_verify.py`, `quote_index.py` |
| Faithfulness judge | `services/pipeline/faithfulness.py`, `faithfulness_context.py`, `faithfulness_hero_quiz.py`; spawn + drain: `routes/pipeline_faithfulness.py` |
| Prompt loading + registry | `services/pipeline/prompt_builder.py`, `prompt_registry.py` |
| Assembly orchestrator + assemblers | `services/pipeline/assembly/core.py`, `assembly/registry.py`, `assembly/assemblers_*.py`, `assembly/{component_lists, moment_normalizers, info_grid_items, labels, dedupe, sampling, derived_guard, attachments, stat_banner, moment_fallback, moment_sources, steps_timeline, info_grid_fold, quote_spots, ingredients, quiz_stamps, tab_references}.py`; the registry policy it reads: `shared_config/assembly_policy.py` |
| Run timing recorder | `services/pipeline/pipeline_timing.py` |
| Prompts | `prompts/*.txt`, `prompts/schemas/`, `prompts/examples/` |
