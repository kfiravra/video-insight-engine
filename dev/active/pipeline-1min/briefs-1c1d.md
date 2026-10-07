# pipeline-1min — 1c+1d agent briefs (coordinator-owned)

Every agent: read `agent-rules.md` first (binding), then context.md (D1–D25), the plan's Phase 1
tables 1c/1d + conflicts C1–C21, brief §1c/§1d + Appendices B.5/B.6/D, and the CODE-MAP anchors
(`evidence/CODE-MAP-orchestration.md` §2 "1c"/"1d"). Code has moved a lot since the CODE-MAP:
read `git log --oneline c57c2a6..HEAD` and the commits touching your files first.

**Two modes.** DESIGN mode (until the coordinator says GO): read + design only — NO edits to any
repo file. Write your design to `/tmp/claude-1000/-home-kfir-projects-video-insight-engine/aa158ff0-536b-4af1-a682-696f61adc1a7/scratchpad/design-<ID>.md`
(≤ 150 lines: file-by-file changes, interfaces you need/provide, tests, risks, open questions), then
reply with a ≤ 10-line summary and stop. GO mode: implement, with the stop points below (report per
agent-rules.md at each; the coordinator commits between stops). Reason for the two modes: a dev
golden run executes the tree's prompts/code while 1a+1b is measured; nothing of 1c/1d may be in the
tree until it ends.

Shared interfaces (fixed — code against them):
- `ctx.visual_annotations: str` — rendered `<visual_annotations>` block (empty string when no frames/
  OCR), set once at frames-done by C2's renderer; C1 places it in the extraction prompt.
- `ctx.video_memory: str` — rendered `<video_memory>` block (wiring agent, 1b.4).
- `ctx.memory: MemoryResult | None`, `ctx.plan_result` (briefs/evidence/terms), `ctx.probe`.
- Settings (D5 adds them FIRST, defaults below; feature agents read `settings.X`):
  `EXTRACTION_PARALLEL: bool = True`, `FRAME_VISION_PARALLEL: bool = True`,
  `EXTRACTION_PARALLEL_BATCHES: int = 6`.

---

## C1 — extraction prompt (1c.4 → 1c.1 → 1c.3 + annotation placement; fast-first removal)
Owns: `src/prompts/base_extraction.txt`, `src/prompts/schemas/*.txt`, `src/prompts/examples/*.txt`,
`src/services/pipeline/prompt_builder.py` (template building; NOT the loader section),
`src/services/pipeline/extractor.py`, `src/services/pipeline/prompt_builder*` tests, extractor tests.
- 1c.4: 16 schema files per Appendix D (strip SCALING + invention floors; keep JSON skeleton, `> UI`
  lines, RULES, COMMON MISTAKES; narrative "merge INTO" → own key; travel "estimate weight" → "omit
  weight when not stated"; finance kept — confirm its consumer; `{duration_minutes}` removed);
  examples header → "Match this specificity and field completeness; counts come from your brief.";
  domains without an example get NO example (no learning fallback). AC: schema parse test after
  stripping; example-selection test. STOP.
- 1c.1: `base_extraction.txt` per B.5/Appendix D: remove `<critical_rule>`, "Fill all fields",
  `<completeness>` → caps from the registry (one per planned dataSource); `<tabs_to_serve>` = the
  plan's briefs (what / where / expect / cap); `{video_context}` = video_memory;
  `<visual_context_guide>` only when an annotations block is present; "An empty array is correct
  when the video has no such content."; example density line → "match this specificity, not its
  count"; say `[m:ss]` markers are absolute video time (output timestamps stay whole seconds).
  AC: prompt render tests; placeholder test updated. STOP.
- 1c.3 (caller adoption; provider support landed in 99bff3b — `llm_messages.text_block`,
  `system_prompt`): system = rules only (no per-video text); user = [transcript with markers +
  video_memory] ← the one breakpoint, then [schema(s) + briefs + caps + example + annotations
  (`ctx.visual_annotations`, appended after the transcript region per B.5) + key_frames ≤ 12 lines];
  `{duration_minutes}` and every per-video value out of the static block; chunked batches keep the
  same layout (batch transcript in the cached block). Remove `EXTRACTION_USE_FAST_FIRST` reads + the
  fast-first path in extractor.py (C6; D5 deletes the setting). AC: a test hashing the static block
  across two different videos (identical); cache-read tokens > 0 on batch 2 in replay telemetry
  (coordinate the replay cassette if needed). STOP.

## C2 — visual annotations out of clean_text (1c.2)
Owns: NEW `src/services/pipeline/visual_annotations.py`, `src/services/pipeline/scene_frames.py`
(remove `inject_visual_context` + helpers), `src/services/pipeline/phases/extraction.py` (only the
`inject_visual_context` call inside `build_prompt_transcript`), the faithfulness module(s), the
summarizer Qdrant ingestion module(s) (find them: transcript chunks / output chunks), tests.
- Renderer: `frame_descriptions` + OCR → `<visual_annotations>` chronological `[m:ss] caption |
  on-screen text` lines (+ the ≤ 12-line key_frames block if it belongs here — agree with C1).
- Plan/memory/probe never see annotation text (test on their inputs).
- Faithfulness judge input = transcript + the rendered block.
- RAG: rendered annotations indexed as separate chunks `source=visual` with timestamps in the same
  collection; transcript chunks + the S3 transcript blob stay annotation-free (D10). Check the
  assistant service reads `source` tolerantly (don't edit services/assistant — report).
- Runner Phase 2.5 removal is done by D4 (orchestration owner) — provide the call D4 makes to set
  `ctx.visual_annotations` at frames-done.
AC: renderer test (chronological, OCR joined); plan/memory inputs annotation-free; ingestion test
(visual chunks tagged `source=visual` with timestamps; transcript chunks clean). STOP.

## C3 — retry removal (1c.5, without extractor.py / phases/synthesis.py)
Owns: `src/services/pipeline/extraction_quality.py`, `src/services/pipeline/post_processor.py`
(`validate_extraction_counts`, `_COUNT_EXTRACTORS`, `FIELD_TO_DOMAINS` if now unused),
`src/services/pipeline/phases/extraction.py` (retry gate + `_attempt_synthesis_fed_retry`; C2 edits
only the build_prompt_transcript call — coordinate by re-reading), tests.
Remove `_attempt_synthesis_fed_retry`, `decide_extraction_retry`, `merge_retry_fields`,
`build_synthesis_fed_retry_prompt`, the count-validation retry gate; keep
`check_extraction_quality` + coverage as metrics. AC: removed paths' tests deleted; coverage metric
test kept. STOP.

## D1 — quiz-only enrichment + quiz policy + requirement backfill (1d.1, 1d.2, 1d.7)
Owns: `src/prompts/enrich_quiz.txt` (new), `src/prompts/enrich/*` (delete 11), `src/services/
pipeline/enrichment.py`, `src/services/pipeline/phases/enrichment.py`, `src/models/pipeline_types.py`
(`EnrichmentData`), `packages/shared/src/config/domains.json` (old `enrichment` map ONLY),
`src/shared_config/domain_config.py` (`get_enrichment_map` removal), `src/prompts/
component_toolkit.txt` (enrichment.flashcards/scenarios), `src/services/pipeline/assembly/
{attachments.py,core.py,promotion.py}`, tests. (`plan.txt` scenarios claim already removed in b7bb9a5.)
- 1d.1 per B.6: one `enrich_quiz.txt` (role + `{flavor}` from `quizEnrichment.flavor`,
  `{video_memory}`, `{extraction_data}`, `{tab_goals}`, rules from today's `enrich_study.txt`),
  demand-driven (only with a `quiz_arena` tab or an allowed `quick_quiz` host), quiz 2–8, strict
  schema + salvage valid items, timeout 30 s, never blocks tabs; remove flashcards/scenarios, their
  caps, the 11 enrich files, the old map + `get_enrichment_map` / `ENRICHMENT_MAP`, toolkit entries.
  Provide `needs_quiz(plan_result) -> bool` for D4's runner gating. STOP.
- 1d.2: `quick_quiz` hosts exclude step_player, step_flow_canvas, checklist, workout_room,
  packing_mission (`quizPolicy.attachmentHostsExclude`); quiz tab forced last (`quizPolicy`). STOP.
- 1d.7: assembly backfill of domain `required` components checks `requirementEvidence` against
  `plan.evidence` (`effective_requirements(..., evidence=)` landed in b7bb9a5) before backfilling;
  evidence false → no backfill, recorded in `droppedTabs`/log. AC: table test per domain; the
  food-travel-vlog golden assertion must pass at gate 1. STOP.

## D2 — synthesis trimmed (1d.3)
Owns: `src/prompts/synthesis.txt`, `src/services/pipeline/synthesis.py`, `src/services/pipeline/
phases/synthesis.py` (incl. removing the "already populated" retry branch), `SynthesisResult` (in
pipeline_types.py — coordinate with D1 by re-reading; touch only SynthesisResult), tests.
`masterSummary` + `seoDescription` only; input = video_memory + compact final extraction + tab
labels; `meta.tldr/keyTakeaways` come from memory (same `meta` shape — prefer filling them in the
synthesis phase result so assembly/core.py stays untouched; if core.py must change, report);
synthesis ∥ moment fill after extraction — provide the function D4's orchestration calls. The two
`synthesis_complete` emissions (wiring) must still carry the superset. AC: synthesis validator test;
meta shape test. STOP.

## D3 — vision batching (1d.4)
Owns: `src/services/media/frame_analyzer.py`, NEW `src/prompts/vision.txt` (vision prompt to a file
+ registry name), `src/services/pipeline/phases/frames.py` (vision calls only — the wiring agent's
probe await is committed by then), `src/services/media/scene_extractor.py` + `scene_detect.py`
(1024 upscale + HIGH reselect order only), tests.
8 frames/call, ≤ 5 in parallel (semaphore), max_tokens scaled per batch AND frame type (C14:
screen recordings ~190 tok/frame, food ~85), 1 retry (vision has no fallback since 90576a0 — say
whether a cross-provider fallback is worth adding), Sonnet (`LLM_VISION_MODEL` default = primary —
D5 changes the default), index→frame mapping in the prompt (A18), no 1024 upscale,
`FRAME_VISION_PARALLEL=false` = today's single call. Measure 360p-native vs 720p-after-hires on 2
videos (D7/C8: description/OCR quality, wall, cost) — this needs dev LLM spend (~$0.3–0.6): state
the run count + cost in your report BEFORE running, then run, pick, report a table; the HIGH-path
reorder follows the winner. AC: batching index-mapping test; semaphore test; measurement table.
STOP after code (before measuring) and after the measurement.

## D4 — orchestration + reliability (1c.2/1d.1/1d.3 wiring, rest of 1d.5)
Owns (after the wiring agent is done): `src/routes/pipeline_runner.py` + the orchestration module
it split out, `src/services/pipeline/context.py`, `src/services/pipeline/phases/assembly.py`,
`src/services/pipeline/pipeline_helpers.py`, `src/worker/{__main__.py,pipeline.py}`, the output
chunker for RAG (`output_chunker` — find it), `tests/replay/**`, tests.
- Wire: Phase 2.5 removal + `ctx.visual_annotations` at frames-done (C2's renderer); enrichment
  demand gating (D1's `needs_quiz`); synthesis ∥ moment fill after extraction (D2's function).
- 1d.5 rest: heartbeats around plan and extraction (reuse `run_parallel_phases`' heartbeat /
  `run_task_with_heartbeat`); embeddings preloaded at worker start; `drive_pipeline` checks the
  entry status before running (duplicate run, A4); overview tab RAG-indexed (`output_chunker` reads
  `props.*`, overview stores `props.data.*` — A23) + chunker test.
- Replay: the 3 cassettes with the new orchestration — heartbeats present; vision batches
  (synthesize per-batch latencies from the recorded single call: 6.6 s + 16.2 s per 1k out tokens,
  mark synthetic); report the new phase walls. STOP per sub-part.

## D5 — settings owner + flags (1d.6, all 1c/1d settings)
Owns: `services/summarizer/src/config.py` (+ its test), both compose files, `.env.example`,
`.env.production.example`, `docs/INFRASTRUCTURE.md` tuning table, `docs/SERVICE-SUMMARIZER.md` env
block, `docs/ERROR-HANDLING.md` (settings lines). Do this FIRST in GO mode (others read the new
settings): add `EXTRACTION_PARALLEL`, `FRAME_VISION_PARALLEL` (bool, default true, literal compose
defaults `${X:-true}`); `EXTRACTION_PARALLEL_BATCHES` default 6 + passthrough (C13); passthrough
fixes for `FRAME_VISION_ENABLED`, `FRAME_TIER_ENABLED`, chunking knobs (A16 — list them);
`LLM_VISION_MODEL` default = primary (None → primary; check what that means in get_stage_model);
delete `EXTRACTION_USE_FAST_FIRST` and the old enrichment caps (flashcards/scenarios, if settings)
through every touch point. Everything must run on prod with TODAY's `.env.production` (D20) — list
any prod env line Kfir should change for `env-changes.md`. AC: `config.test` (note:
`src/__tests__/config.test.py` is uncollectable — find/create the collected config test);
`docker compose config` validates for dev; config default == compose default for every touched
setting. STOP.
