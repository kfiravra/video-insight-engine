# Phase-1 review fixes — briefs for fix agents

Findings: `gates/g1-review-findings.md` (24 groups; IDs below = group + item order there).
Rules: `agent-rules.md` (no git mutations; own files only; targeted tests; report format). Every fix
lands in ONE "p1 review fixes" commit made by the coordinator at the end — so do NOT worry about
commit boundaries, but DO keep each fix tested (regression test for every bug fix).
Defaults: fix every C, every bug / possible-bug / security / performance / test-gap Warning, and
cheap dead-code removals. DEFER (do not do; they are listed for phase 4): pure-move file splits
(youtube.py, transcript_chunker.py, extractor.py, scene_extractor.py, core.py, output_chunker.py,
video.service.ts, video.repository.ts, big test files) and narrative docs (summarizer-workflow.md,
PROJECT-BRIEFING*.md, ARCHITECTURE.md). Report per finding: fixed / skipped (why) / deferred.
Model-output impact: say for each fix whether it can change pipeline output (golden re-check).

## FX1 — media, proxy, frames
Owns: services/media/{download_utils,local_video,scene_detect,frame_extractor,hires_prefetch,
frame_analyzer,frame_ocr}.py, services/video/{playlist,youtube,transcript_fetcher}.py (no splits),
pipeline/assembly/moment_frame_fill.py, pipeline/scene_frames.py, media/scene_extractor.py (lowres
close + comments only), tests/conftest.py, their tests.
Do: G01 1–2,4–6 (G01-3 split deferred) · G03 2,3,5 (with_captions removal + port tests),7 ·
G04 1–6 · G05 1–2 · G13 4 (drop clean_text OCR path) · G20 3 (vision batch timeout sizing + no
retry after a full-deadline timeout or a stage deadline keeping finished batches), G20 5 (rollback
e2e test) · G22 3 (conftest docstring). G20 4 (threshold recalibration on native frames): add a
TRACKED follow-up line in your report (no code) unless a cheap, test-pinned fix is obvious.

## FX2 — transcript, chapters, extraction prompt, LLM plumbing, SETTINGS OWNER
Owns: services/transcript/*, services/transcription/{transcript,transcript_chunker}.py,
pipeline/{extraction_prompt,extractor,prompt_builder}.py, pipeline/phases/extraction.py,
services/{llm,llm_provider,llm_messages}.py, utils/llm_retry.py, config.py + both composes +
.env.example/.env.production.example + docs/INFRASTRUCTURE.md + docs/SERVICE-SUMMARIZER.md (env/
settings rows only), tests/test_config.py, their tests.
Do: G06 1–4 (TRANSCRIPT_CLEANING_ENABLED → default false everywhere) · G11 1,3 · G12 1,3,4,5 ·
G14 1–3 (delete cache_static end to end) · G03 4 (capped await of the description analysis in
_split_chapters) · G07 1 (LLM_CLASSIFIER_MODEL compose passthrough with the Haiku literal default +
docs rows) · G22 1 (document per-run × WORKER_CONCURRENCY; fix WORKER_CONCURRENCY rationale), G22 4
(test_config lazy root) · G21 items in llm_retry/llm_provider if any (coordinator adds).

## FX3 — plan, probe, memory, orchestration, assembly
Owns: pipeline/{plan,plan_prompt,memory,tier_probe,video_memory,enrichment? NO}.py,
pipeline/phases/{text,memory,triage,probe,assembly,synthesis,enrichment}.py,
routes/pipeline_orchestration.py, routes/run_timing.py, pipeline/assembly/{core,cross_tab,
promotion,attachments}.py, shared_config/domain_config.py, models/pipeline_types.py, prompts/plan.txt,
pipeline/prompt_registry.py (docstring), tests/replay/{build_cassette,cassette_timing}.py, their tests.
Do: G08 1 (PLAN_MAX_TOKENS ~3,500; timeout 60 s = deviation from the brief's 45 s — measured plan
walls 36.7–39.8 s; count finish_reason=length), 3 (correct the guard docstring: wording-only
prompt edits need registration — vie-langfuse-init re-registers on compose up; sha-based guard
optional only if small), 4,5,6,7,8 · G09 1–4 · G03 1 (description out of the phase-2 group;
capped awaits where read) · G13 1 (orchestration render test) · G07 2,3 · G17 1 / G19 1 (the quiz
must not block tabs: start it as a task after extraction, emit non-quiz tabs, add the quiz tab last;
re-send quick_quiz hosts like the overview) · G19 2 (Key Info fallback when memory failed), G19 5 ·
G17 3 (QuizQuestion duplicate options — pipeline_types.py) · G18 1,2,3 · G13 6 / G23 5 (extract
_write_shared_artifacts(ctx, result) in phases/assembly.py with one eval_run check).

## FX4 — api, web, assistant, eval + bench scripts
Owns: api/src/**, apps/web/src/**, packages/types/src/**, packages/shared/src/** (TS helper only),
services/assistant/src/** (the two findings only), scripts/{register_prompts,_eval_*,run_eval,
benchmark_fast_models,_bench_quality_scorers,spotcheck_frame_vision}.py, their tests.
Do: G10 1–4 (G10 5 split deferred) · G23 1,2,3 · G13 2 · G02 1,3 · G08 2 (eval coverage accepts
promotion targets → THEN rescore the stored baseline reports r1/r2 + this phase's quick and full
golden reports with the same scorer and rebuild noise.json ($0, from stored rows) so the gate stays
paired; report before/after numbers) · G20 2 (spotcheck/benchmark imports) · G19 3 · G17 5.
Rebuild vie-api only if asked (coordinator does it).

## FX5 — schemas, models, vector, quiz enrichment
Owns: models/domain_types.py, prompts/schemas/*.txt, prompts/examples/*.txt, services/vector/*.py,
pipeline/enrichment.py, prompts/enrich_quiz.txt, their tests.
Do: G15 1–4 · G13 5 (store_visual_chunks: embed first, then delete + upsert) · G17 2 (needs_quiz:
strip-only demand requires a sparse host), G17 4 (quiz attempt timeout ~14 s; record_llm_failure on
the outer timeout).

## FX6 — contract docs (after FX2 releases SERVICE-SUMMARIZER.md/INFRASTRUCTURE.md)
docs/API-REFERENCE.md (two synthesis_complete emissions + merge rules; versions filter), RAG.md
(visual source), DATA-MODELS.md (evalRun, isEvalUser, partial synthesis), IDEMPOTENCY.md (version
pools + numbering), llm-cost-model.md (cache layout, audit exit 2 expected until phase 3),
OBSERVABILITY.md (register_prompts --label/--dry-run), SERVICE-SUMMARIZER.md non-env sections
(retry removed, chapter chain, synthesis budget/order, vision batching, classifier → probe,
stream-URL removed, single 720p), ERROR-HANDLING.md (credit 400 note). Narrative docs → 4.2 list.

## Additions after G21
- FX2 also: G21 3 (fallback attempt non-retryable → None), G21 4 + 7 (description analysis 30 s total cap; split the long function; fix the comment) — owns services/video/description_analyzer.py too.
- FX3 also: G21 5 (llm_feature_var for translation; owns pipeline/phases/translation.py too).
- FX7 (new, small) — worker: G21 1 + 2 (C) — owns src/worker/pipeline.py + its tests.
