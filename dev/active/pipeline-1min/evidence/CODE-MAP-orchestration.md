# CODE MAP — orchestration + flags (pipeline-1min)

Read 2026-10-07 on `docs/readme-rebuild` @ 015cf26 (summarizer code identical to `origin/main`). Prefixes: `S/` = `services/summarizer/src/`, `P/` = `S/services/pipeline/`, `M/` = `S/services/media/`, `A/` = `P/assembly/`. Every line number below was opened, not grepped.

---

## 1. Current call order (what actually runs today)

Entry: worker `drive_pipeline` (`S/worker/pipeline.py:33-89`) → `acquire_lock` (`:70`) → `produce_to_broker` (`S/routes/pipeline_broker.py:75-159`) → `stream_summarization` (`S/routes/pipeline_runner.py:352-593`) → `_run_pipeline_phases` (`:186-344`). SSE-direct path: `S/routes/stream.py:63-150` (same `produce_to_broker` after `acquire_lock` at `:136`; dev override bypasses broker, `pipeline_broker.py:169-196`).

Pre-phase (`pipeline_runner.py`): Langfuse trace opened `:419`; Redis response-cache check `:426-466`; `update_status(PROCESSING)` `:470`; `clear_transcript_meta` `:478`; `send_video_status("processing")` `:485`; `PipelineContext` built `:488`.

| # | Phase | Starts / awaits | Serial wait? |
|---|---|---|---|
| 1 | **Metadata** — `run_phase_metadata` `P/phases/metadata.py:20-49` | `extract_video_data` (`:25`, one yt-dlp `extract_info` in a thread, `S/services/video/youtube.py:974-1005` → `_extract_video_data_sync:723-823` → `_build_yt_dlp_opts:505` + `_extract_with_retry:717` + caption fetch `_fetch_picked_track:826`) → `validate_duration` `:32` → `analyze_description` `:37-43` (**awaited inline**, raw `litellm.acompletion` `S/services/video/description_analyzer.py:151`, no provider/span → NOT in the Langfuse trace, no `llm_feature_var` beyond `summarize:metadata`). | **YES**: description LLM (~4.9 s) blocks transcript+frames start. Caption fetch inside the same sync call (429 retry = 2 s fixed) blocks too. |
| 2 | **Transcript ∥ Frames** — `run_parallel_phases([run_phase_transcript, run_phase_frames], ctx)` `pipeline_runner.py:218` (`run_parallel_phases` `P/pipeline_helpers.py:259-315`, heartbeat every `SSE_HEARTBEAT_SECONDS` `:299-302`). | Runner waits for **both** to finish (`:218-222`) before anything else. |
| 2a | Transcript `P/phases/transcript.py:71-174`: `fetch_transcript` chain `:82`; `transcript_ready` SSE `:136`; `clean_transcript` `:137`; spaCy/TF-IDF `clean_transcript_advanced` under `TRANSCRIPT_CLEANING_ENABLED` `:140-152` (filler removal lives INSIDE the advanced pass, `S/services/transcript/cleaner.py:129-178`, `remove_fillers:59`); SponsorBlock `:155-170`. | — |
| 2b | Frames `P/phases/frames.py:121-214`: tier `derive_tier(category, title, tags)` `:142-146` (`M/visual_tier.py:25-49`, metadata-only); HIGH-tier reselect hook `:151-154` → `extract_scene_keyframes` `:156` (`M/scene_extractor.py:383-435` → `_do_extraction:438-744`): manifest cache `:421`; **pass-1 download** `worstvideo[ext=mp4]/worst` `:470-484` (timeout 120 s); `start_local_hires` `:515` (`M/hires_prefetch.py:134-145`: proxied runs start the **720p download task** concurrently); ffmpeg scene detect `:519-545`; **zero-frame early return** `:574-576` (`return empty_result` → `finally:734` `hires_source.close()` **cancels the 720p prefetch**, `hires_prefetch.py:109-131`); static-camera supplement only when `0 < frames < 12` `:602-614` (`_MIN_DETECTED_FRAMES=12`, so zero frames never reaches it); score `:620`; `select_frames` `:626`; `_sample_by_time` fallback `:637`; HIGH reselect hook (vision on candidates) `:645-666`; `refine_selected_frames` `:671` (`M/hires_refiner.py:44-74`: prefetched file → else proxied `_refine_from_local_download:137` = **second 720p download**, else stream-URL seeks `:77-113`); dedup `:677`; S3 upload `:685`; manifest `:694`; `finally` deletes pass-1 file and closes the prefetched 720p file `:734-741` (**720p file is NOT kept** for moment fill). Back in `frames.py`: OCR+presign `process_scene_frames` ∥ `_run_vision_analysis` `:181-189` (STANDARD = one Sonnet call on top-8, `M/frame_analyzer.py:88-206`: single `complete_with_messages` `:168`, `max_tokens=max(2000,250·n)` `:170`, timeout `max(timeout, 4·n)` `:162`; HIGH = one call on ≤40 candidates via the reselect hook `frames.py:89-116`); `persist_vision_descriptions` `:195`. | Vision is on the critical path because the runner awaits the whole frames phase. |
| 2.5 | **Visual injection** `pipeline_runner.py:226-243` → `inject_visual_context` (`P/scene_frames.py:191-226`): `_insert_with_segments:229` reads `seg.get("startMs", seg.get("start_ms", 0))` `:244` but `ctx.transcript_data.segments` are raw `{text,start,duration}` dicts (`youtube.py:412-418`, `transcript_fetcher.py:130`) → every annotation maps to 0 ms → never interleaves (A17/A20 confirmed). Mutates `ctx.clean_text` (so plan preview `ctx.clean_text[:3000]`, Qdrant and S3 see annotation text). | Sequential, ms. |
| 3 | **Plan** `run_phase_plan` `P/phases/triage.py:27-144`: override `:34`; **classifier awaited first** `classify_domain_format` `:44-51` (`P/classifier.py:135-222`, fast model, `max_tokens=250`, 10 s, `transcript_preview[:2000]`, returns `traits` + `reasoning`); then `run_plan` `:84-94` (`P/plan.py:191-346`: static/dynamic split at `<video>` `:250-258`, `cache_static` sent as `cache_control` system block `S/services/llm_provider.py:166-174`; `max_tokens=2048, timeout=60, max_retries=2` `:286-288`; `transcript_preview[:3000]` `:272`); `_validate_tabs` `:133-188` copies `dataSource` unvalidated `:181`; `_enforce_domain_policy` `:105-130`; `PlanResult.model_validate` `:334`; confidence < 0.6 → `_build_fallback_plan` `:55-69`. `ctx.video_dna_text = to_video_context_full()` `triage.py:98` (logging only), `video_dna_compact` `:99`; `triage_complete` + `meta` SSE `:120-133`. | **YES**: plan waits for frames-done (phase 2) although it reads no frame data; classifier → plan are sequential. |
| 4 | **Extraction** `run_phase_extraction` `P/phases/extraction.py:168-359`: chapters only when `duration > CHUNKED_EXTRACTION_THRESHOLD` (900 s) `:184` → `split_transcript_into_chapters` `:216` (`S/services/transcription/transcript_chunker.py:144-232`: youtube chapters → description timestamps → **`_detect_chapters_with_ai` LLM** `:201-213` → time split → single; description passed from `video_info` which has NO `description` key `extraction.py:174-179` → A9 "empty description" confirmed); `format_gallery_frames_for_extraction` `:235` (≤12 lines, `P/prompt_builder.py:419-444`); `extract` `:242-256` (`P/extractor.py:129-226`: `build_extraction_template` `:171-183`; strategy `_resolve_strategy:77-126` — `SINGLE_THRESHOLD=5333` words `:33`, chunked only if `batch_chapters` > 1 batch `:100-102`; `_single_extraction:326-359` `max_tokens=16384, timeout=240, retries=2`; `_overflow_extraction:365-403` `max_tokens=32768`; `_chunked_extraction:530-691` semaphore `EXTRACTION_PARALLEL_BATCHES` `:574`, `_run_batch_extraction:489-527`, rate-limited batches → sequential fallback `:634-664`, `merge_batch_extractions` `:680`; cache split at `<transcript>` `_split_prompt_for_caching:245-273`; `batch_partial_context` block `_build_batch_context:276-309`); `validate_domain_output` (`S/models/domain_types.py:1195-1284`; single-tag path `:1206-1218` validates the whole response against the one domain model — the A25 wrapped-response bug); faithfulness spawned `pipeline_runner.py:257-260`; coverage `:297`; **quality gate + synthesis-fed retry** `:303-353` (`check_extraction_quality`, `validate_extraction_counts` (reads `plan_result.item_counts`, `P/post_processor.py:203-262`), `decide_extraction_retry` `P/extraction_quality.py:137-229`, `_attempt_synthesis_fed_retry` `extraction.py:97-166` = early `synthesize` + second full `extract(force_primary_model=True)`). | Fast-first: `use_fast_model = settings.EXTRACTION_USE_FAST_FIRST and not force_primary_model` `extractor.py:201`. |
| 5 | **Synthesis ∥ Enrichment** `pipeline_runner.py:267-276` (parallel only when extraction `_has_meaningful_data`). Synthesis `P/phases/synthesis.py:45-99` → `synthesize` (`P/synthesis.py:28-67`, fast model, `extraction_summary[:4000]` `:48`, `max_tokens=8192, timeout=30`). Enrichment `P/phases/enrichment.py:19-62` → `enrich` (`P/enrichment.py:97-211`: `ENRICHMENT_MAP = get_enrichment_map()` `:56` from `domains.json["enrichment"]`; `truncate_json_safely(extraction, 8000)` `:140`; `max_tokens=16384, timeout=90, retries=2` `:168-178`; caps quiz 12 / flashcards 15 / scenarios 6 `:32-34`; strips quiz when `quiz_arena` forbidden `:194-201`). Enrichment runs for EVERY mapped domain regardless of whether a quiz tab was planned. | Both wait for extraction. |
| 6 | **Assembly** `P/phases/assembly.py:33-329`: `assemble_response` `:54` (`A/core.py:1291-…`: meta from synthesis `:1348-1351`; per-tab `resolve_data_source` `:1379` → `_cross_domain_fallback` `:1389` → `_in_domain_sibling_fallback` `:1396`; assembler `:1459`; `promote_component` `:1477`; `enforce_density` `:1484`; `demote_component` `:1503` (ladder `A/promotion.py:174-198`, `quiz_arena: ()`); `inject_frame_thumbnails` `:1522`; cross-tab links `:1614-1638` read plan `outboundLinks`); `tab_ready` for every non-moment tab `:94-97` (all at once, `position`); `fill_moment_frames` `:105-108` (`A/moment_frame_fill.py:194-226` → `_run_passes:153-191` → proxied = **third 720p download** via `_fallback_pass:129-146`); moment `tab_ready` `:109-110`; `complete` `:130-137`; Mongo save `:179` (`save_structured_result`, `$set` of allow-listed keys incl. `pipeline`, `S/repositories/mongodb_repository.py:108-170`); Redis `:200-207`; Qdrant tasks `:249-273`; S3 transcript `:313`; `done` `:321-329`. | moment fill is awaited before the moment tabs emit. |
| 7 | Translation (non-English) `pipeline_runner.py:287-315`. DONE log `:321-338` — `tabs=` = `len(ctx.triage.tabs)` = **planned** count `:337` (A23 confirmed). Faithfulness drained `:344`. | — |

**Serial waits to remove (brief 1a/1b):** (1) description LLM inside metadata before phase 2 (`metadata.py:37`); (2) plan behind frames-done (`pipeline_runner.py:218→246`); (3) classifier before plan (`triage.py:44→84`); (4) extraction behind vision for every run (vision inside phase 2); (5) moment fill awaited before moment tabs (`assembly.py:105-110`). **Downloads today:** pass-1 (`scene_extractor.py:470`), proxied 720p prefetch (`hires_prefetch.py:145`, deleted at `scene_extractor.py:734`), refiner fallback 720p when no prefetch (`hires_refiner.py:141`), moment-fill 720p (`moment_frame_fill.py:137`). Stream-URL pass: `hires_refiner.py:77-113` + `moment_frame_fill.py:120-126`, both skipped when `ytdlp_proxy_url()` (`hires_refiner.py:71`, `moment_frame_fill.py:160`).

---

## 2. Brief item → code anchor

Legend: ✅ name exists as the brief says · ⚠️ exists, differs · ❌ does not exist.

### 1a — inputs and media
| Brief item | Anchor | Name check |
|---|---|---|
| Remove 2nd/3rd proxied downloads; keep one 720p file | `M/scene_extractor.py:515` (prefetch start), `:734-741` (close/delete), `M/hires_refiner.py:137-151`, `A/moment_frame_fill.py:129-146`; `M/local_video.py:41-77` (`download_video_720p`, caller owns `temp_dir`). Needs a per-run handle on `ctx` (`P/context.py`) that outlives the frames phase and is closed after moment fill. | ✅ |
| Remove 720p cancellation on zero-frame early return | `scene_extractor.py:574-576` returns before the static fallback; `finally:734` closes the prefetch. | ✅ |
| Stream-URL pass removal | `M/stream_url.py:85-170`, `hires_refiner.py:77-113`, `moment_frame_fill.py:120-126`, `M/frame_extractor.py:61` (`extract_frame(source, ts)` works on local files too). | ✅ |
| t=0: metadata ∥ low-res ∥ 720p ∥ description | Runner `pipeline_runner.py:204-222`; metadata `metadata.py:25-49`; `validate_duration` `P/pipeline_helpers.py:187-192`. Frames need `video_data.duration` (`frames.py:158`) and tier → the download can start at t=0 only if `extract_scene_keyframes` is split (download first, then detect). | ✅ |
| One shared `extract_info` | Already one call for metadata+subtitles (`youtube.py:745-799`); format URLs come from separate `yt-dlp` subprocesses (`scene_extractor.py:470`, `local_video.py:90`, `stream_url.py:129`). Sharing format URLs means passing `info["formats"]` out of `_extract_video_data_sync` — possible but downloads go through `yt-dlp` CLI with its own client args (`M/download_utils.py` `ytdlp_client_cli_args`). | ⚠️ partial |
| Zero-candidate ladder 0.3 → 0.15 → uniform; ffmpeg rc checked | Scene detect `scene_extractor.py:519-576` (rc is NOT checked after `communicate` — only `frame_paths` emptiness `:573-576`); `_sample_interval_frames:261-323` is the uniform sampler; `SCENE_THRESHOLD` `config.py:267`. | ✅ |
| Moment fill seeks the kept 720p; hi-res from kept file | `moment_frame_fill.py:102-146` (`_extract_batch(source, …)` takes any ffmpeg input), `hires_refiner.py:125-134` (`_refine_from_prefetched`). | ✅ |
| `render_transcript(segments, every=20s)` markers | ❌ does not exist. Segments: `ctx.transcript_data.segments` raw `{text,start,duration}`; `normalize_segments` `pipeline_helpers.py:159-179`; `clean_text` built at `transcript.py:137,169` from raw text (not from segments) — markers must be rendered from segments, so cleaned text and marked text diverge unless cleaning becomes per-segment. Chunked slices: `transcript_chunker.py` `ChapterChunk.text` (`:29`), `_build_batch_transcript` `extractor.py:468-475` adds `=== CHAPTER …` headers with absolute times. | ❌ new |
| Filler removal into basic cleaning; spaCy behind flag | `remove_fillers` `S/services/transcript/cleaner.py:59-71`; basic `clean_transcript` `S/services/transcription/transcript.py` (imported `transcript.py:21`); flag `TRANSCRIPT_CLEANING_ENABLED` `config.py:253`, read `transcript.py:140`. | ✅ |

### 1b — tier probe, plan, memory
| Brief item | Anchor | Name check |
|---|---|---|
| `classify.txt` → `tier_probe.txt`; drop traits/reasoning | `P/classifier.py:26` (PROMPT_PATH), `ContentTraits:72-116`, `ClassificationResult:119-127`, prompt `S/prompts/classify.txt` (`{title}{channel}{duration_minutes}{tags}{transcript_preview}`, `<traits>` `:68-78`). Langfuse name derived from file stem (`prompt_builder.py:32-47` → `summarizer:classify`) — rename = new registry entry. Readers of traits: `triage.py:57-58,76-80`, `extraction.py:316` (`decide_extraction_retry`), `extraction_quality.py:188`. | ✅ |
| `derive_tier(probe, title)` replaces `derive_tier(category, title, tags)` | `M/visual_tier.py:25-49` has exactly `(category, title, tags)` ✅; caller `frames.py:142-146`; config `visualCriticality` in `domains.json` (`highDomains`, `lowDomains`, `highTitleKeywords`, `tiers`). | ✅ |
| Frames branch awaits the probe before Step 6b with 3 s cap | No such await exists; tier is computed at `frames.py:141-147` before `extract_scene_keyframes`, and the reselect hook (HIGH) is decided there too (`:151-154`). The hook fires inside `_do_extraction` at `:645` — the probe await must land before that point (pass a coroutine/future into `extract_scene_keyframes`). | ⚠️ |
| Delete `FRAME_TIER_EARLY_CLASSIFIER`, `category_confidence` | `config.py:330` (zero readers ✅ dead); `category_confidence` is a `VideoContext` dataclass field `youtube.py:150,157,393` (zero readers). | ✅ |
| `plan.txt`: full transcript, drop `reasoning/outboundLinks/identity.audience/itemCounts`, add `brief/evidence/terms` | `plan.py:191-275` (`transcript_preview[:3000]` `:272`, `{content_traits}` `:274`, `_render_playbook:72-102`); `PlanResult` `S/models/pipeline_types.py:257-381` (`reasoning:266`, `item_counts:273`, `PlanIdentity.audience:246`); `_validate_tabs` `plan.py:176-186` (`outboundLinks:169-184`). Readers to retire: `item_counts` → `post_processor.validate_extraction_counts:203-262`, `to_video_context_full:368-375`; `outboundLinks` → `A/cross_tab.py:95-176`, `A/core.py:1614-1638`; `reasoning`/`audience` → `to_video_context_full:343-353` only. `<extraction_caps>` `plan.txt:385-399`, `<format_routing>:350-367`, `<outbound_links_instructions>:79-87`, examples `:89-242` (6), scenarios claim `:303`. `{density_gates}`/`{valid_components}` injected `plan.py:238-240`. | ✅ |
| Timeout 45 s, 1 retry, no `cache_control` | `plan.py:286-291` (`timeout=60.0, max_retries=2, cache_static=cache_static`). | ✅ |
| `_build_fallback_plan` unchanged | `plan.py:55-69` → `build_fallback_tabs` `S/shared_config/domain_config.py:213-220`. | ✅ |
| `memory.txt` (new, Haiku, parallel with plan) | ❌ new file + new stage. Model knob: `LLM_EXTRACTION_MODEL` `config.py:77` (default `None` → primary, NOT Haiku; the brief says "Haiku (`LLM_EXTRACTION_MODEL`)" — in prod `.env.production.example` leaves it unset, so a new `LLM_MEMORY_MODEL` or an explicit default is needed). `get_stage_model` map `config.py:399-411`. | ⚠️ |
| `video_memory` into the `{video_context}` slot | Slots: extraction `base_extraction.txt:6-10` (`{video_context}` inside `<video_context>` with `{title}`/`{duration_minutes}`), filled `prompt_builder.py:302`; synthesis `synthesis.txt:13` / `synthesis.py:49`; enrichment `enrich/*.txt:7` / `enrichment.py:163`. Today's value = `ctx.video_dna_compact` (`to_video_context_compact` `pipeline_types.py:315-337`). | ✅ |
| `synthesis_complete` early at memory-done | `SynthesisCompleteEvent` `S/models/sse_events.py:79-84` (`tldr`, `keyTakeaways`; `extra="allow"` `:36`); today emitted `phases/synthesis.py:79-93`. | ✅ |
| `chapter_detect` only when > 1 batch and no outline; description no longer empty | `extraction.py:184-230` (chapters built BEFORE the batch decision; `_resolve_strategy` `extractor.py:97-113` decides batches after); AI call `transcript_chunker.py:201-213` → `_detect_chapters_with_ai:451-…` (fast model, 25 s). | ✅ |

### 1c — extraction prompt (still one call)
| Brief item | Anchor | Name check |
|---|---|---|
| `base_extraction.txt` edits | `<role>:1-4` ("Fill all fields" `:3`), `<video_context>:6-10`, `<key_frames>:12-17`, `<tabs_to_serve>:37-39` (`{tab_goals}` from `build_tab_goals` `prompt_builder.py:146-163`), `<visual_context_guide>:55-61` (unconditional), `<completeness>:66-71` (`{duration_minutes}`), `<critical_rule>:73-86`, `<extraction_example>:92-96` density line, `<transcript>:107-109` (`{batch_context}{transcript}`). Builder `_build_base_template` `prompt_builder.py:235-306`. | ✅ |
| Annotations as `<visual_annotations>` block, not injected | Remove `pipeline_runner.py:224-243` + `P/scene_frames.py:145-312`; renderer ❌ new (inputs: `ctx.frame_descriptions` items `{timestamp_sec, content, text_visible, scene_type, original_index}` `frame_analyzer.py:252-264`; OCR `ocr_text` on `ctx.scene_frames_all` from `P/scene_frames.py:106-113`). `format_visual_annotation` `frame_analyzer.py:269-286` is the existing per-item formatter. | ✅ |
| Cache layout: system = rules, user = transcript+memory ← breakpoint, then schema tail | Today: `cache_static` = everything before `<transcript>` (schemas, rules, title, duration, tab goals) `extractor.py:245-273`; provider puts `cache_static` as the **system** block with `cache_control` and the dynamic part as the user message `llm_provider.py:166-174` — only one breakpoint, only on the system message. The brief's layout (breakpoint INSIDE the user message after transcript+memory) needs `complete()`/`complete_with_messages()` to accept a content-block list with `cache_control` on a user block (`:162-185`, `:259-339`). `{duration_minutes}` is in `base_extraction.txt:7,67` and in 15 of 16 schemas (all but `finance`). | ⚠️ provider change |
| Schemas strip SCALING/floors | `S/prompts/schemas/*.txt` (16); `_load_schema` `prompt_builder.py:166-183`; SCALING/Minimum hits: learning 3, travel 2, others 1, finance 0. | ✅ |
| Examples: no learning fallback | `_load_domain_example` `prompt_builder.py:186-204` (falls back to `learning.txt` `:199-201`); examples exist for fitness, food, learning, music, project, review, tech, travel. | ✅ |
| Remove synthesis-fed retry + count-validation gate | `extraction.py:97-166, 299-359`; `P/extraction_quality.py:137-229` (`decide_extraction_retry`), `:232-260` (`merge_retry_fields`), `:263-308` (`build_synthesis_fed_retry_prompt`); `post_processor.validate_extraction_counts:203-262`; `phases/synthesis.py:51-60` ("already populated" branch). Keep `check_extraction_quality:79-…` and `compute_extraction_coverage:323`. | ✅ |
| Remove `EXTRACTION_USE_FAST_FIRST` and fast-first path | `config.py:130`; `extractor.py:201-203, 210, 217, 224` + `use_fast_model` kwargs `:344,389,517`; `extraction.py:135-137` comment; `call_llm_with_retry(use_fast_model=…)` `S/utils/llm_retry.py:88,138-146` is shared by classifier/synthesis/enrichment/chapter_detect — keep the kwarg, drop only the extraction use. Brief says "fast-first path in `llm_retry.py`" — the path is in `extractor.py`, `llm_retry.py` only routes `use_fast_model`. | ⚠️ location |

### 1d — tail
| Brief item | Anchor | Name check |
|---|---|---|
| Enrichment → quiz only, demand-driven | `P/enrichment.py:97-211`; `EnrichmentData` `pipeline_types.py:72-78` (quiz/flashcards/cheatSheet/scenarios); phase `phases/enrichment.py:19-62`; runner `pipeline_runner.py:262-276`; prompts `S/prompts/enrich/*.txt` (11 files); map `domains.json["enrichment"]` (`default/learning/tech/fitness/food/music/travel/review/project/language/science/podcast/gaming`); `get_enrichment_map` `domain_config.py:205-210`. Readers of flashcards/scenarios: `A/core.py` `resolve_data_source:39` (`enrichment.*` paths), `component_toolkit.txt:170,262`, `plan.txt:303`. | ✅ |
| `quick_quiz` host exclusion; quiz tab last | `A/attachments.py:52` (`_NO_QUICK_QUIZ_COMPONENTS = {quiz_arena, quick_quiz}`), `attach_secondaries:144-199`; tab order = plan order (`A/core.py:1363-…` loop), `_ensure_overview_first:1115`; no quiz-last rule exists. | ✅ |
| `synthesis.txt` → masterSummary + seo only; tldr/takeaways from memory | `P/synthesis.py:28-67`, `SynthesisResult` `pipeline_types.py:23-30`, meta fill `A/core.py:1348-1351`, event `phases/synthesis.py:79-84`. | ✅ |
| Vision: 8-frame batches, ≤5 parallel, Sonnet, index→frame mapping, no 1024 upscale | `M/frame_analyzer.py:88-206` (one call; `Frame {i} (at m:ss)` labels `:122`; `parse_vision_response:209-266` keyed by `frame_index`); callers `frames.py:30-59` (STANDARD) and `:89-116` (HIGH hook, ≤40); `LLM_VISION_MODEL` default `config.py:67` = Haiku (prod `.env.production.example:79` sets Sonnet explicitly); "1024 upscale" = `SCENE_DETECT_SCALE_WIDTH=1024` `config.py:271` applied in the ffmpeg scale filter `scene_extractor.py:525,281` (frames are extracted at 1024 wide from the ≤360p pass-1 file). `FRAME_VISION_PARALLEL` ❌ new. | ✅ |
| Heartbeats around plan and extraction | `run_task_with_heartbeat` `pipeline_helpers.py:245-256` (used only by moment fill `assembly.py:105-107`); `run_parallel_phases` heartbeat `:299-302`. Plan + extraction run via plain `async for` `pipeline_runner.py:246-252` → no keepalive today. | ✅ |
| Embeddings preloaded at worker start | API lifespan preloads spaCy + SentenceTransformer `S/main.py:71-113`; worker `S/worker/__main__.py:209-262` preloads nothing. | ✅ |
| `drive_pipeline` checks status (A4) | `S/worker/pipeline.py:59-77`: fetches entry, acquires lock, no status check; `produce_to_broker` re-fetches `pipeline_broker.py:108`. | ✅ |
| Honour `retry-after`, one same-provider retry before cross-provider fallback, fallback model tagged | Retry loop `llm_retry.py:129-206` (linear 1 s·attempt, no `retry-after`); LiteLLM `fallbacks=self._fallback_models` passed on every primary call `llm_provider.py:308-309` (fallback happens INSIDE one attempt, before our retry); `record_generation(model=effective_model)` `:329-331` tags the requested model, not the one LiteLLM actually used (A27). `_LITELLM_NUM_RETRIES` `:28`. | ✅ |
| `EXTRACTION_PARALLEL_BATCHES` default → 6 | `config.py:122` (=2), read `extractor.py:574`. | ✅ |

### 2 — progressive output (backend)
| Brief item | Anchor |
|---|---|
| `tab_ready` per tab as final; `final: true` + positions re-emit | `phases/assembly.py:92-110`; `TabReadyEvent` `sse_events.py:99-106` (`extra="allow"` → `final`/`provisional` need no model change but should be declared). |
| Provisional Moments tab from memory outline | `_chapters_to_moments` `A/core.py:1433` already builds moments from chapters (youtube chapters path). |
| Redis stream reset per run + TTL; leaked streams | `S/services/cache/pipeline_event_stream.py`: `publish:187` (XADD maxlen), `mark_done:197-213` (TTL set only on DONE → a crashed producer leaks a no-TTL stream), `purge:127`, `stream_key:109`. `PIPELINE_STREAM_TTL_SECONDS=120` `config.py:245`. |
| `meta` must not wipe tabs (FE) | out of this map (apps/web). Backend `meta` event `triage.py:123-133`. |

### 3 — the split
| Brief item | Anchor |
|---|---|
| Field-aware renderer | `_build_base_template` `prompt_builder.py:235-306` builds whole-domain schemas from `_load_schema`; `> UI` lines stripped where? — not in `prompt_builder.py` (no stripping seen; the brief's "already stripped for the LLM" needs verifying in `_load_text`/schema files). |
| Reconcile at plan-done | Insert after `run_plan` returns `triage.py:94-97` and before `ctx.triage` is built `:102`; write `pipeline.reconcile` next to `pipeline.triage` in `assembly.py:158-169`. |
| Groups + firing under `EXTRACTION_PARALLEL_BATCHES` | Model on `_chunked_extraction` `extractor.py:530-691` (semaphore, progress queue, rate-limit fallback); merge `P/extraction_merger.py:22-59` (`_merge_dicts` concatenates lists, longest string wins `:62-79` — NOT "scalars first-wins"; the brief's rule needs a new merge mode). |
| Validate per group without materializing defaults | `validate_domain_output` `domain_types.py:1195-1284` does `model_validate(...).model_dump(by_alias=True)` → materializes every default field. |
| Per-group assembly, `resolve_cross_tab_links` at end | `A/core.py:1291-…` is one monolithic pass (per-tab loop `:1363-1530`, then global post-process, cross-tab `:1614-1638`, overview). |
| Plan failure → fallback dataSources as requested set | `_build_fallback_plan` `plan.py:55-69`; `defaultTabs` per domain in `domains.json` (`tech.setup` ≠ any schema field — A6 fix). |

---

## 3. Settings and the touch points

**How a fully-wired setting is wired (model: `YOUTUBE_PROXY_URL`):**
1. `S/config.py` field (`:150`)
2. `docker-compose.yml` `x-summarizer-env` anchor (`:13-80`; `:41`) — shared by `vie-summarizer` (`:378`) and `vie-summarizer-worker` (`:429`)
3. `docker-compose.prod.yml` `x-summarizer-env` anchor (`:34-110`; `:67`) — `vie-summarizer` `:363`, worker `:399`
4. `.env.example` (`:261`)
5. `.env.production.example` (`:102`)
6. `docs/INFRASTRUCTURE.md` tuning table (`:308-334`)
7. `docs/SERVICE-SUMMARIZER.md` env block (`:203`)
8. the reader(s) in code (`M/download_utils.py:48-49`) — plus `docs/summarizer-workflow.md` when the flow changes.
`docker-compose.override.yml` carries no env (only `--reload` commands, `:14-33`). Tests: `S/__tests__/config.test.py`, `tests/test_config.py`.

**Matrix** (✓ = present, · = absent). Columns: config.py / compose dev anchor / compose prod anchor / .env.example / .env.production.example / INFRASTRUCTURE.md.

| Setting (default) | cfg | dev | prod | .env.ex | .env.prod.ex | INFRA |
|---|---|---|---|---|---|---|
| `FRAME_VISION_ENABLED` (true) `:318` | ✓ | · | · | · | · | · |
| `FRAME_VISION_MAX_FRAMES` (8) `:319` | ✓ | · | · | · | · | · |
| `FRAME_VISION_TIMEOUT` (90) `:320` | ✓ | · | · | · | · | · |
| `FRAME_TIER_ENABLED` (true) `:326` | ✓ | · | · | · | · | ✓ (doc only) |
| `FRAME_TIER_EARLY_CLASSIFIER` (false) `:330` **dead** | ✓ | · | · | · | · | · |
| `FRAME_OVERSELECT_COUNT` (40) `:327`, `FRAME_RESELECT_FLOOR` (20) `:333` | ✓ | · | · | · | · | · |
| `EXTRACTION_PARALLEL_BATCHES` (2) `:122` | ✓ | · | · | · | · | · |
| `EXTRACTION_FORCE_SPLIT_CHUNKS` (4) `:125` | ✓ | · | · | · | · | · |
| `EXTRACTION_USE_FAST_FIRST` (false) `:130` | ✓ | ✓ | ✓ | ✓ | · | · |
| `CHUNKED_EXTRACTION_THRESHOLD` (900 s) `:110` | ✓ | · | · | · | · | · |
| `MAX_TOKENS_PER_BATCH` (50000) `:111`, `MAX_MINUTES_PER_BATCH` (40) `:117`, `CHUNKED_EXTRACTION_TIMEOUT` (300) `:118`, `CHAPTER_BATCH_SIZE` (3) `:104` | ✓ | · | · | · | · | · |
| `LLM_VISION_MODEL` (haiku-4.5) `:67` | ✓ | ✓ | ✓ | ✓ | ✓ (sonnet) | · |
| `LLM_EXTRACTION_MODEL` (None) `:77` | ✓ | ✓ | ✓ | ✓ | ✓ | · |
| `LLM_ENRICHMENT_MODEL` (haiku-4.5) `:65` | ✓ | ✓ | ✓ | ✓ | ✓ | · |
| `LLM_SYNTHESIS_MODEL`, `LLM_DESCRIPTION_MODEL`, `LLM_TRANSLATION_MODEL` | ✓ | ✓ | ✓ | ✓ | · | · |
| `LLM_CLASSIFIER_MODEL` `:61`, `LLM_CHAPTER_DETECT_MODEL` `:62` | ✓ | · | · | ✓ | · | · |
| `TRANSCRIPT_CLEANING_ENABLED` (true) `:253` | ✓ | ✓ | ✓ | · | · | · |
| `TRANSCRIPT_CLEANING_TIMEOUT` (30) `:257` | ✓ | ✓ | ✓ | · | · | ✓ |
| `SCENE_EXTRACTION_ENABLED` `:266` | ✓ | ✓ | ✓ | · | · | · |
| `SCENE_THRESHOLD` (0.3) `:267`, `SCENE_DETECT_SCALE_WIDTH` (1024) `:271`, `SCENE_HIRES_CONCURRENCY` (4) `:279` | ✓ | · | · | · | · | · |
| `SCENE_HIRES_ENABLED` `:278` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `SCENE_HIRES_TIMEOUT` `:280`, `SCENE_HIRES_FALLBACK_TIMEOUT` `:284` | ✓ | ✓ | ✓ | · | · | ✓ |
| `SCENE_S3_PREFIX` `:313` | ✓ | ✓ | ✓ | ✓ | · | ✓ |
| `YTDLP_PLAYER_CLIENTS` `:300`, `YTDLP_HIRES_PLAYER_CLIENTS` `:309` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `YOUTUBE_PROXY_URL` `:150`, `YOUTUBE_PROXY_EXIT_COUNT` `:155` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `WORKER_CONCURRENCY` (2) `:360` | ✓ | ✓ (worker svc) | ✓ | ✓ | ✓ (=1) | · |
| `SSE_HEARTBEAT_SECONDS` (12) `:250` | ✓ | · | · | ✓ | · | · |
| `PIPELINE_STREAM_TTL_SECONDS` (120) `:245` | ✓ | · | · | · | · | · |
| `LLM_FALLBACK_PROVIDER` `:49` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `FRAME_EXTRACTION_ENABLED` (false in cfg, `true` in compose anchors!) `:338` | ✓ | ✓ | ✓ | ✓ | · | · |
| `LANGFUSE_*` `:180-192` | ✓ | ✓ | ✓ | ✓ | ✓ | · |
| Prompt disk-vs-registry switch | ❌ none (`prompt_builder.py:100-143`, `langfuse_client.py:368-383`: registry wins whenever Langfuse keys are set) |

Settings the brief depends on that are **not passed through compose** (so prod cannot change them without a code/compose edit): `FRAME_VISION_ENABLED`, `FRAME_TIER_ENABLED`, `EXTRACTION_PARALLEL_BATCHES`, all chunking knobs, `SCENE_THRESHOLD`, `FRAME_VISION_MAX_FRAMES/TIMEOUT`. `EXTRACTION_USE_FAST_FIRST` is the only extraction flag fully wired (and is slated for deletion).

---

## 4. Prompt loading, registry, cache placement

- Loader: `load_prompt_text` `P/prompt_builder.py:66-89` → `load_prompt_with_fallback:100-118` → `_try_fetch_from_registry:121-143` → `fetch_prompt_with_obj` `S/services/observability/langfuse_client.py:368-383` (`client.get_prompt(name)`, default label = `production`, SDK-side cache). Registry name = `summarizer:<stem>` or `summarizer:<schema|enrich|example>:<stem>` (`_langfuse_name_for:32-47`). Disk fallback `_read_file_cached:50-53` (lru). **No switch to force disk** — a `PROMPT_SOURCE=disk` (dev-only) setting must be added in `config.py` and honoured in `_try_fetch_from_registry`.
- Registration: `scripts/register_prompts.py` (`--dry-run` default, `--commit`, `--filter`; labels `production` `:200-224`; `_label_for:112`). Per Langfuse project = per key pair → run once with dev keys, once with prod keys.
- Prompts read by stage: plan `plan.py:42-52` (`plan.txt` + `component_toolkit.txt`, `{density_gates}` + `{valid_components}` from `domain_config.py:90-94,161-169`); classifier `classifier.py:26,130`; extraction `extractor.py:160` (`quality_rules.txt`) + `prompt_builder.py:266-306` (`base_extraction.txt` + `schemas/<tag>.txt` + `examples/<primary>.txt`); synthesis `synthesis.py:20`; enrichment `enrichment.py:131`; chapter_detect `transcript_chunker.py:464`; description `description_analyzer.py:28-32`; vision prompt is an inline constant `frame_analyzer.py:26-57` (not in the registry).
- Per-video placeholders in the **static/cached** block today: extraction `{title}` `{duration_minutes}` `{user_goal}` `{tab_goals}` `{detail_level}` `{content_emphasis}` `{video_context}` `{frame_context}` `{primary_tag}` (`prompt_builder.py:292-306`, all before `<transcript>` → the cache block differs per video, A26); schemas `{duration_minutes}` in 15 files. Plan: `{title}{channel}{duration_minutes}{category_hint}{content_format}{domain_playbook}{description}{transcript_preview}{content_traits}` all after `<video>` (dynamic) `plan.py:260-275`; static = everything before `<video>` `:250-258` ✓ already per-video-free.
- Cache breakpoints: plan → `cache_static` system block `plan.py:291`; extraction → `cache_static` system block `extractor.py:338,382,509`; both via `llm_provider.complete:166-174` (single `cache_control` on the system message, Anthropic only `_is_anthropic_model`). No user-message breakpoint support.
- Examples: `_load_domain_example` `prompt_builder.py:186-204`; header line is the first line of each `examples/*.txt` ("Match this density…").

---

## 5. Tests

- Layout: `services/summarizer/tests/` (129 entries; `conftest.py`, `fixtures/{llm_responses, faithfulness_labeled_claims.json, retrieval_golden.json}`, `eval/test_retrieval_eval.py`, `utils/`). Run: `cd services/summarizer && .venv/bin/python -m pytest` (`pytest.ini`: asyncio auto, `testpaths = tests`). Worker tests live beside the code: `S/worker/__tests__/*.test.py`, `S/__tests__/config.test.py`.
- `conftest.py`: autouse `_disable_stage_model_overrides` `:25-36` (nulls `LLM_*_MODEL`), `mock_llm_provider` `:133-156`, `mock_llm_service` `:158`, `mock_repository` `:222`, sample segments/transcript `:315-365`.
- Tests that fake `acompletion` today: `tests/test_llm_provider.py`, `test_llm_provider_langfuse.py`, `test_description_analyzer.py`, `test_prompt_render_placeholders.py` (patch `litellm.acompletion` / `src.services.llm_provider.acompletion`). For the replay harness the seam is `llm_provider.acompletion` (`llm_provider.py:13` import; calls at `:227,315,380,471`) keyed by `llm_feature_var` (set per phase: `summarize:metadata/transcript/frames/classifier/plan/extraction/synthesis/enrichment/chapter_detect`) — note `description_analyzer.py:16,151` imports `acompletion` separately and must be patched too.
- Runner tests patch phase functions on the module: `tests/test_pipeline_runner.py:53-67` (`run_phase_*`, `run_parallel_phases`), `test_pipeline_runner_failure_report.py`, `test_pipeline_integration.py` (per-domain extract/synthesis/enrichment with `mock_llm`).
- Existing tests touching the files this task edits: `test_extractor.py`, `test_chunked_extraction.py`, `test_extraction_merger.py`, `test_extraction_quality.py`, `test_extraction_coverage.py`, `test_post_processor.py`, `test_plan.py`, `test_triage.py`, `test_classifier.py`, `test_classifier_traits.py`, `test_enrichment.py`, `test_synthesis.py`, `test_prompt_builder.py`, `test_prompt_registry.py`, `test_prompt_render_placeholders.py`, `test_inject_visual_context.py`, `test_phase_frames.py`, `test_scene_extractor.py`, `test_hires_prefetch.py`, `test_hires_refiner.py`, `test_local_video.py`, `test_frame_analyzer.py`, `test_moment_frame_fill.py`, `test_phase_assembly_streaming.py`, `test_phase_assembly_cache.py`, `test_attachments.py`, `test_cross_tab.py`, `test_domain_config.py`, `test_domain_types.py`, `test_llm_retry.py`, `test_llm_provider.py`, `test_pipeline_event_stream.py`, `test_sse_event_models.py`, `test_stream_routes.py`, `test_contract_parity.py`, `test_ts_pydantic_parity.py`.

---

## 6. Mongo doc shape (`videoSummaryCache`)

Written once at `P/phases/assembly.py:143-181` via `save_structured_result` (`S/repositories/mongodb_repository.py:154-170`, `$set` of allow-listed keys `_ALLOWED_RESULT_KEYS:108-…` incl. `meta, tabs, triage, enrichment, pipeline, pipelineVersion, status, …`). `pipeline` = `{triage: ctx.triage_dict, extraction, enrichment, synthesis, assembly: {tabsDesigned, tabsAssembled, tabsDropped, droppedTabs}}` (`:158-169`); `transcriptMeta` written separately `set_transcript_meta:172` from `pipeline_runner.py:154`; `degraded`, `processingTimeMs`, `rawTranscriptRef` (`:298-301`). `ctx.phase_times` (`P/context.py:120`) is only logged (`pipeline_runner.py:321-338`), never persisted → `pipeline.timing` and `pipeline.reconcile` slot in beside `pipeline.assembly` in the same `$set` (already allow-listed under `pipeline`).

---

## 7. Shared config

`domains.json` lives at `packages/shared/src/config/domains.json` (mounted read-only at `/app/shared/domains.json` for summarizer + worker, `docker-compose.yml:375,426`, prod `:360,396`); loader `S/shared_config/domain_config.py:18-37` (`lru_cache`). `packages/shared/dist/domains.json` is a flat copy dated 2026-07-13 while src changed 2026-08-25 (87571f6) → stale. Top-level keys today: `components, componentTiers, densityGates, assemblerItemCaps, domainRequirements, playbooks, visualCriticality, domains, modifiers, enrichment, categoryMap`. **Conflict with Appendix A:** `"enrichment"` already exists as the tag→prompt-file map (`get_enrichment_map` `:205-210`, `enrichment.py:56`); the brief's new `"enrichment": {quizDomains, flavor}` would overwrite it — use a different key (e.g. `quizEnrichment`) or migrate the old map in the same change. `defaultTabs` confirmed: `tech.setup` (no schema field; schema has `setup.commands`), `language.vocabulary` → `concept_canvas` (brief: `flash_deck`).

---

## Summary (for the parent)

**Serial waits found:** description LLM awaited inside metadata (`metadata.py:37`); plan+classifier wait for frames-done (`pipeline_runner.py:218→246`); classifier awaited before plan (`triage.py:44→84`); vision inside the awaited frames phase; moment fill awaited before moment `tab_ready` (`assembly.py:105-110`). 720p is downloaded up to three times (prefetch deleted at `scene_extractor.py:734`; refiner fallback `hires_refiner.py:141`; moment fill `moment_frame_fill.py:137`); the zero-frame return at `scene_extractor.py:574` skips the static fallback AND cancels the prefetch.

**Brief-name mismatches:** `render_transcript`, `memory.txt`, `tier_probe.txt`, `FRAME_VISION_PARALLEL`, `EXTRACTION_PARALLEL`, reconcile, registry maps = new (expected). `derive_tier(category, title, tags)` ✅ exists as stated. "Fast-first path in `llm_retry.py`" — it is in `extractor.py:201-224`; `llm_retry.py` only routes `use_fast_model` (keep). "Haiku (`LLM_EXTRACTION_MODEL`)" — that setting defaults to `None` = primary; memory needs its own model knob/default. The brief's user-message cache breakpoint needs `llm_provider.complete()` to support `cache_control` on a user content block (today: system block only, `llm_provider.py:166-174`). `extraction_merger` keeps the LONGEST string, not first-wins (`:72-75`). `validate_domain_output` materializes defaults (`domain_types.py:1211-1212`). Appendix A's `"enrichment"` key collides with the existing enrichment prompt map. No disk-vs-registry prompt switch exists. `FRAME_TIER_EARLY_CLASSIFIER` and `category_confidence` are confirmed dead. `FRAME_EXTRACTION_ENABLED` defaults `false` in config but `true` in both compose anchors.

**Touch points for a setting:** `config.py` → `docker-compose.yml` `x-summarizer-env` → `docker-compose.prod.yml` `x-summarizer-env` → `.env.example` → `.env.production.example` → `docs/INFRASTRUCTURE.md` table → `docs/SERVICE-SUMMARIZER.md` env block → reader + tests. Not wired today: `FRAME_VISION_*`, `FRAME_TIER_ENABLED`, `EXTRACTION_PARALLEL_BATCHES`, chunking knobs, `SCENE_THRESHOLD`.
