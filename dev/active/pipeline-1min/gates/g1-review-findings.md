# Phase-1 review findings (raw, per group; triage in g1.md)

Recipe: `review-p1.md`. One review agent per task-id group (24 groups for 56 commits; docs-only commits skipped).

## G05 p1a.3 (e18e948) — C=0 W=2
- [W] maintainability | scene_detect.py:60 | `_ScenePass.ok` set but never read; failed/timed-out pass and real zero-candidate pass both log `rung=uniform` | fix: drop `ok` or label the rung `failed+uniform` when not ok
- [W] test gap | scene_detect.py:265 | untested: `_SEEK_BUDGET` timeout in `seek_frames` (keep landed frames, cancel rest) and the CancelledError branch in `_run_ffmpeg` (kill + re-raise) | fix: two tests (budget 0.05 s with a stuck fake process; cancel during communicate → kill called, CancelledError propagates)

## G01 proxy rotation (7304540 e925e2b) — C=0 W=6
- [W] possible bug | download_utils.py:58-61 | `is_exit_blocked` misses yt-dlp's other session blocks ("current session has been rate-limited by YouTube…", "requiring a captcha challenge") | fix: add `rate-limited by YouTube|try again later|captcha challenge` + tests
- [W] performance | download_utils.py:334-354 | audio download: 3 outer attempts × full rotation = 9 extractions + sleeps when all exits blocked | fix: break the outer loop when the rotation ended blocked
- [W] maintainability | youtube.py:757 | file grew 1085→1106 (over limit) | fix: move proxied fetch helpers to video/youtube_fetch.py
- [W] maintainability | playlist.py:85-87 | `_ErrorCapture.warning` re-logs every yt-dlp warning at WARNING (bypasses no_warnings) | fix: warning() at debug
- [W] possible bug | playlist.py:99-105 | non-block failure returns None → try_proxy_exits records the exit as working → process-wide exit memory poisoned | fix: raise a private non-blocked exception on None-without-block; test memory unchanged
- [W] maintainability | tests/test_download_utils.py:21 | `_fresh_exit_memory` fixture copied into 6 files | fix: one autouse fixture in tests/conftest.py

## G02 register_prompts (965c618) — C=0 W=3
- [W] possible bug | register_prompts.py:246 | `_already_synced` swallows every lookup error → dry run reports bad keys/401/network failures as `would-upload`, exit 0 | fix: three-state lookup (unchanged / changed / lookup-failed → `unknown`), non-zero exit on unknown/error, test with a non-404 error
- [W] maintainability | docs/OBSERVABILITY.md:88 | doc says dry run makes no Langfuse calls; no --label/--dry-run/stage-then-promote recipe (activate_langfuse.sh step 5 now reads Langfuse) | fix: update OBSERVABILITY.md:84-93
- [W] test gap | register_prompts.py:297 | keys set + SDK missing branch untested (dry run → warn/None; commit → SystemExit) | fix: two tests via monkeypatch sys.modules["langfuse"]=None

## G06 p1a.4+1a.5 (c311479 6137c27) — C=0 W=5
- [W] bug | transcript_chunker.py:61 | `_chunk_text` → render_transcript without source_language → chunked (> 900 s) non-English chapters lose "um" ("eu tenho um carro" → "eu tenho carro") | fix: thread source_language through split_transcript_into_chapters → _chunk_text; pass ctx.source_language_code (phases/extraction.py:180); pt regression test
- [W] performance | config.py:255 + both composes | TRANSCRIPT_CLEANING_ENABLED defaults True (prod .env false): spaCy pass on the critical path before transcript_ready in dev/replay/eval; output reaches no prompt now | fix: default False (config + composes) or document intent
- [W] possible bug | cleaner.py:77 (:43-44) | stray-comma cleanup runs over the whole string once any filler is removed ("e.g., x" → "e.g. x", "U.S.," → "U.S.") | fix: remove the adjacent comma inside the filler substitution only; test
- [W] maintainability | phases/extraction.py:31/:214 | build_prompt_transcript duplicates text.py render_prompt_transcript; extraction re-renders instead of reading ctx.prompt_transcript | fix: use ctx.prompt_transcript or the shared helper; delete the copy
- [W] best practice | extractor.py (702) / transcript_chunker.py (780) | over-limit files grew; whole-file reflow inside a feature commit | fix: pure-move splits (phase 4 / review fixes)

## G07 p1b.1 (1e8db2f 8010731 203904e 4b273bf 44e4325 9b207bc) — C=0 W=5
- [W] best practice | docker-compose.yml:24, docker-compose.prod.yml:44, config.py:67, .env.example:178 | LLM_CLASSIFIER_MODEL (now the probe model, default Haiku) not passed through compose / not documented — .env override does nothing; a blank passthrough would silently route the probe to gpt-4o-mini | fix: `${LLM_CLASSIFIER_MODEL:-anthropic/claude-haiku-4-5-20251001}` in both anchors + docs rows (rename in 4.1)
- [W] maintainability | tests/replay/build_cassette.py:200, cassette_timing.py:61 | cassette building still hard-codes the `classifier` span (StopIteration/IndexError on any post-1b.1 trace) | fix: look up tier_probe first, fall back to classifier
- [W] maintainability | tier_probe.py:44 (:79-104, :121) | strip_visual_annotations + helpers + 5 tests dead since 1c.2; stale comment | fix: delete; keep the "never renders annotation text" guard test
- [W] maintainability | scene_extractor.py:477 | file 572→584 (over limit); Step-6b resolver inside a 173-line function | fix: extract _apply_reselect into a sibling module
- [W] docs | SERVICE-SUMMARIZER.md:362,985; ARCHITECTURE.md:182; summarizer-workflow.md:148-154,306; API-REFERENCE.md:1368,1689; OBSERVABILITY.md:33; PROJECT-BRIEFING-TLDR.md:23 | still describe the classifier | fix: 4.2 docs sweep

## G04 p1a.2 (bf618ee) — C=0 W=6
- [W] possible bug | moment_frame_fill.py:38 | cached-frames run: moment fill starts the 720p download (180 s DOWNLOAD_TIMEOUT) inside its 150 s budget; the old 120 s cap + 30 s seek reserve are gone → can hold `complete`/save 150 s and fill nothing | fix: download timeout param; moment fill passes _FILL_TIMEOUT − 30 when it starts the download
- [W] possible bug | frame_extractor.py:70 | extract_frame kills ffmpeg only on its own timeout; an outer cancel leaves ffmpeg running and stray tmp jpgs | fix: except CancelledError → proc.kill(); await proc.wait(); raise
- [W] best practice | moment_frame_fill.py:129 | fill_moment_frames 33→52 lines | fix: extract _fill_within_budget
- [W] test gap | tests/test_moment_frame_fill.py:209 | test_should_never_download_on_its_own can't fail (patches the wrong module name) | fix: real LocalHiresSource + monkeypatch hires_prefetch.download_video_720p, or delete (replay covers it)
- [W] docs | config.py:136-145, PROJECT-BRIEFING.md:142,160, DEPLOY.md:91, summarizer-workflow.md:136, tests/test_download_utils.py:3, .env.example:266 | still describe stream-URL seeks / two 720p downloads | fix: rewrite (one ≤ 720p file per run, seeks only)
- [W] performance | hires_prefetch.py:122 | close() runs shutil.rmtree on the event loop before `complete`/save | fix: await asyncio.to_thread(cleanup_local_video, …)

## G08 p1b.2 (b7bb9a5 da3a61d 505de70 da14284) — C=0 W=8
- [W] possible bug | plan.py:43-45 | PLAN_MAX_TOKENS 2048 vs measured 1,814–1,920 out; 45 s timeout vs measured 36.7–39.8 s → truncation (repair silently drops trailing tabs) or timeout + retry → fallback plan after ~90 s | fix: max_tokens ~3,500; timeout ~60 s (deviation) or smaller output; count finish_reason=length; test
- [W] possible bug | scripts/_eval_scoring.py:87 | quality coverage (35 %, gating) matches requiredComponents exactly — promotion (step_player→step_flow_canvas) reads as a regression; rescore() too | fix: accept promotion targets (reuse _eval_assertions.promotion_targets()) + test
- [W] possible bug | prompt_builder.py:131-133 / prompt_registry.py:74-81 | guard compares placeholder sets only → schemas/examples/toolkit (no placeholder change) served stale from the registry until registration; "safe in either order" claim wrong | fix: record the shipped file sha at registration and serve disk when it differs; meanwhile correct the docstring + make registration a deploy step (vie-langfuse-init already re-registers on compose up)
- [W] bug | assembly/core.py:1709-1723 + cross_tab.py:92 | cross-tab link labels resolved before _post_process_tabs → "Next: 🛒 14 Ingredients", "Untitled Chapter N" leaks | fix: resolve after post-processing (or same emoji strip/rename) + test
- [W] possible bug | plan.txt:292 vs :389-390 vs :295/:319/:533 | step_flow_canvas thresholds 11+ / ≤10 / 6+ while assembly promotes at 8 | fix: one threshold = STEP_FLOW_THRESHOLD rendered from code + prompt test
- [W] maintainability | domain_config.py:139-171 | effective_requirements(evidence=) + _ruled_out_by_evidence unused in prod; 1d.7 re-implemented the rule in core.py:554 | fix: one helper
- [W] test gap | tests/test_plan_prompt.py:80 | no test that the plan's untrusted transcript/description/title text can't break tags ("</transcript>") or expand "{probe_hint}" | fix: two sanitization tests
- [W] maintainability | pipeline_types.py:247-310 | dead to_video_context_compact / to_video_context_full (+ ctx.video_dna_text); to_triage_dict drifted from the real triage_dict | fix: delete dead helpers; give to_triage_dict the full shape and use it in _store_plan_result

## G03 p1a.1 (eabe954) — C=0 W=7
- [W] performance | pipeline_orchestration.py:178 | run_phase_description is a phase-2 member → extraction waits for the description LLM (30 s timeout + retry); its SSE has no reader | fix: take it out of the group; await (capped) only where needed (assembly / chunked path)
- [W] possible bug | local_video.py:116 | lowres (and 720p) temp dir not removed on non-cancel exceptions (EMFILE/EACCES) → disk fills | fix: cleanup on BaseException / try-finally keyed on success
- [W] test gap | youtube.py:1099 | the live caption path (extract_video_data(with_captions=False) → fetch_video_captions) untested | fix: 4 unit tests (info-only, fill + clear, 429 flag, no track)
- [W] maintainability | phases/extraction.py:163 | _description_chapters reads ctx.description_analysis directly (contract: await_description_analysis) | fix: capped await in _split_chapters
- [W] maintainability | youtube.py:855 | _extract_video_data_sync + with_captions=True branch have no prod caller | fix: remove; port tests
- [W] best practice | youtube.py | 1063→1088 lines (1106 at HEAD) | fix: move caption code to video/captions.py
- [W] maintainability | scene_extractor.py:568 + hires_prefetch.py:8 + youtube.py:648/689 + transcript_fetcher.py:78/122 + frames.py:166 + local_video.py:105 | stale comments; the low-res file stays on disk until the frames phase ends | fix: close lowres right after detect_candidate_frames; update docstrings

## G16 p1c.5 (13a63ff f7ab092 2c26c6a f1922f4) — C=0 W=2
- [W] docs | SERVICE-SUMMARIZER.md:412-418 (5b), :63, :605, :967 | still describe the synthesis-fed retry / validate_extraction_counts / RETRY_SCORE_THRESHOLD | fix: "one extraction pass; quality score logged as a metric, no retry"
- [W] docs | PROJECT-BRIEFING.md:154,:417; summarizer-workflow.md:191,:405-407; ARCHITECTURE.md:196; tests/replay/cassette.py:45 | retry / fast-first still described | fix: docs sweep (or now in the review-fix commit)

## G11 runner split + p1b.6 (9e58acf 4b453fb) — C=0 W=4
- [W] security | transcript_chunker.py:628 | chapter_detect now gets the real description: raw (only [:500]), chained str.replace → description/title can inject `{transcript_samples}`/`{duration_minutes}` or tags | fix: sanitize_for_prompt(title, 200) / (description, 500) like plan/probe/memory + test
- [W] maintainability | transcript_chunker.py | 689→780 lines | fix: pure move of chapter sources to transcription/chapter_sources.py
- [W] test gap | tests/test_transcript_chunker.py:920 | no test that description timestamps beat the memory outline | fix: test_should_keep_description_timestamps_ahead_of_the_outline
- [W] docs | SERVICE-SUMMARIZER.md:948-954; summarizer-workflow.md:48,77,228; DATA-MODELS.md:392 | chapter chain + moved function names stale | fix: docs sweep

## G15 p1c.4 schemas (db56693) — C=0 W=5
- [W] bug | domain_types.py:108 (+:738, :617) | TravelPackingItem has no weight/emoji; NarrativeKeyMoment + MusicSection no endTimestamp → dropped by validation; PackingMission kg tally + highlight spans never reach assembly | fix: add the fields (weight float|None, emoji str, end_timestamp alias endTimestamp)
- [W] test gap | tests/test_extraction_schemas.py:70 | schema parse tests check top-level keys only (nested drops pass) | fix: ×16 recursive round-trip via model_validate→model_dump(by_alias)
- [W] possible bug | schemas/learning.txt:29-30, science.txt:23, gaming.txt:42 | leftover count pressure ("MUST list 1-3 connections", "≤16 concepts" vs registry cap 15, "6+ stages") | fix: "0-3 connections the speaker draws"; drop the counts
- [W] maintainability | schemas/travel.txt:52 | the new weight rule sits on a `>` UI line (stripped in phase 3.1) and the skeleton shows "weight": 0.4 | fix: move under RULES; drop the sample value
- [W] docs | summarizer-workflow.md:172 | says examples fall back to learning.txt | fix: docs sweep

## G10 p1b.5 (530c3ac db7240a) — C=0 W=6
- [W] possible bug | api/src/utils/synthesis-merge.ts:32 | guard can't tell runs apart: a re-run in place (stale regen video.service.ts:372, retry video.repository.ts:284) keeps the old masterSummary → blocks this run's partial/superset; cached replay mixes old summary + new tldr | fix: $unset synthesis wherever a row is re-dispatched
- [W] maintainability | apps/web/.../sse-validators.ts:133 + stream-event-processor.ts:176-196 | web schema not tied to SSESynthesisCompleteEvent; local SynthesisResult shadows the shared one; merge rule duplicated web/api | fix: `satisfies z.ZodType<SSESynthesisCompleteEvent>`; shared filledSynthesisFields in @vie/shared
- [W] best practice | api/src/routes/stream.routes.test.ts:344 | Object.assign onto a beforeAll container leaks a mock across describes | fix: mergeSynthesis: vi.fn() in createMockContainer
- [W] maintainability | api/src/routes/stream.routes.ts:162 | new branch nested 6 deep in a 178-line handler | fix: extract persistRelayedEvent
- [W] maintainability | stream.routes.test.ts 720→798, test_stream_routes.py 1446→1457, video.repository.ts 566→571 | over-limit growth | fix: split test files
- [W] docs | API-REFERENCE.md:1585,:1322; SERVICE-SUMMARIZER.md:437; DATA-MODELS.md:288 | single-emission contract still documented | fix: document two emissions + merge rules

## G14 p1c.3 (99bff3b 8793bc6) — C=0 W=4
- [W] possible bug | extraction_prompt.py:277 (:240) | frame_context (raw vision captions + OCR) is baked into the tail and _fill runs over it again → on-screen `{transcript}` duplicates the whole transcript into the tail (verified) | fix: bind frame_context late in the same single pass (LATE_BOUND_PLACEHOLDERS) or escape braces/defuse tags; test
- [W] possible bug | extractor.py:478-480 (+ transcript_chunker.py:146) | _batch_annotations slices by ChapterChunk.start_seconds, but force-split chunks carry guessed even times while their text has real markers → frames land in the wrong batch | fix: bounds from the batch's first [m:ss] marker (or full block for force_split) + test
- [W] maintainability | llm_messages.py:51-57,79-83; llm.py:96,130; llm_provider.py:255,271-283; llm_retry.py:201,300,370,414 | cache_static has no production caller since 8793bc6 (dead second caching path) | fix: delete it + _with_static_prefix + tests; fix docstrings
- [W] docs | llm-cost-model.md:107-117,:153; PROJECT-BRIEFING.md:208,:421; SERVICE-SUMMARIZER.md:398,:962; summarizer-workflow.md:183-184; audit_cache_credits.py exit 2 now expected in phase 1 | fix: rewrite "Where cache_control is set"; note exit 2 expected until phase 3

## G09 p1b.3+1b.4 (d61ecc8 4f43993 c62edb1) — C=0 W=6
- [W] performance | memory.py:282 | MEMORY_MAX_RETRIES=1 became P-P-F (fallback provider) since 90576a0 → up to 3×25 s holding phase 2 (memory FAIL ×2 after ~55 s in the quick golden) | fix: max_retries=0 (dropped-connection resend still applies) or wait_for(MEMORY_TIMEOUT_S+5) → plan-only video_memory
- [W] possible bug | phases/text.py:39-43 + phases/memory.py:108 | no-transcript videos (metadata only) still get a memory call that invents [m:ss] outline + takeaways → hero + video_memory + chapters | fix: skip memory (ctx.memory=None) when not ctx.prompt_segments + test
- [W] possible bug | memory.py:150 | _make_contiguous stretches the last section to the duration → an outline bunched in the first 20 min of a 90-min video passes | fix: if max(raw end) short of duration by > max(20 %, 5 min) → drop outline + warn + test
- [W] performance | phases/triage.py:111 | plan awaits probe_for_plan again (text branch already did) → up to +5 s, ctx.probe overwritten after memory started | fix: read ctx.probe
- [W] maintainability | pipeline_types.py:258 | dead to_video_context_compact/full + ctx.video_dna_text (dup of G08)
- [W] maintainability | phases/extraction.py:31-42 | build_prompt_transcript duplicates render_prompt_transcript (dup of G06)

## G12 p1c.1 (fa81d91) — C=0 W=6
- [W] possible bug | extraction_prompt.py:155-162 | the plan's quiz tab (quiz_arena ← enrichment.quiz) is listed in extraction's "Tabs you serve" with expect/cap though extraction has no quiz schema | fix: list only tabs whose dataSource domain ∈ content_tags ∪ modifiers + test
- [W] possible bug | extraction_prompt.py:240/:277 | frame_context re-filled (dup of G14)
- [W] possible bug | extraction_prompt.py:209-211 + extractor.py:481 | <visual_context_guide> kept for a batch whose annotation slice is empty | fix: render guide + block together per call ("" when empty) + chunked test
- [W] test gap | extraction_prompt.py:90-99,:134 | _load_schema traversal guard / missing schema / empty tags fallback untested since fa81d91 | fix: 3 tests
- [W] possible bug | extraction_prompt.py:165-172 | expect printed unclamped ("expect ~50, cap 25") and for object kinds | fix: min(expect, cap) for lists, no expect for objects + test
- [W] docs | summarizer-workflow.md:167; SERVICE-SUMMARIZER.md:962; PROJECT-BRIEFING.md:208 | old builder/layout | fix: docs sweep

## G13 p1c.2 (a9b2197 f1d8a2e f2648cd) — C=0 W=8
- [W] test gap | pipeline_orchestration.py:221 | nothing tests that annotations are rendered between phase 2 and extraction | fix: orchestration test (ctx.visual_annotations set when extraction starts) or replay assertion on uC45's extraction request
- [W] possible bug | services/assistant/src/models/requests.py:100 (+ responses.py:26, docs/RAG.md:14,64) | library-search sources filter Literal lacks "visual" → 422 | fix: add "visual" + docs rows (assistant file — outside phase-1 summarizer scope; small)
- [W] maintainability | tier_probe.py:44-46,95-105,121 | dead annotation stripping (dup of G07)
- [W] maintainability | scene_frames.py:14,24,35,69-73 + frame_ocr.py:154-180 | process_scene_frames(clean_text=…) / enrich_transcript_with_ocr still able to put OCR into clean_text (no caller) | fix: drop param + helper + tests
- [W] possible bug | vector/store.py:250 | store_visual_chunks deletes before embedding → a failed embed/cancel leaves zero visual points | fix: embed first, delete, upsert (delete only when no chunks)
- [W] maintainability | phases/assembly.py:321-329 | run_phase_assembly ~237 lines grows | fix: extract _index_in_qdrant(ctx)
- [W] maintainability | tests/test_frame_intelligence_integration.py 503→559, faithfulness.py 526→528 | fix: move TestVisualAnnotationsIntegration
- [W] docs | summarizer-workflow.md:140-146,256,345; ARCHITECTURE.md:180,268; SERVICE-SUMMARIZER.md:355-356; API-REFERENCE.md:1364; frames.py:89 | Phase 2.5 injection still described | fix: docs sweep

