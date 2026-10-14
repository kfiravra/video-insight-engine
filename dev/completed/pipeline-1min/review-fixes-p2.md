# Phase-2 review fixes — briefs for fix agents

Findings: `gates/g2-review-findings.md` (ids R1-*, R2-*, R3-*, R4-*, R5-*). Each fix agent owns a
disjoint file set below. **Fix agents do NOT commit** — all review fixes land in ONE coordinator
commit (the last of phase 2, with g2.md). Otherwise follow `agent-rules.md` (files you own only;
changes needed elsewhere → report; formatter strips unused imports → add import + use in one
edit; heavy lock for full suites/pyright/tsc; no dev-stack or Langfuse writes; no LLM spend).

Every fix: a test that fails before and passes after (behaviour, "should … when …"), unless the
fix is docs/comment only. Keep the fix minimal — no drive-by refactors. When you stop, report per
finding id: fixed (how + test name) / not fixed (why), files touched, output-changing y/n, and
targeted test results. Then run, under the heavy lock, the full summarizer suite once
(`cd services/summarizer && .venv/bin/python -m pytest -q`) and pyright 1.1.407 on your changed
src files, and report the counts (a failure in another fix agent's in-flight files = note it).

## F1 — background tasks + RAG text (R1-1, R1-2, R1-3, R3-1, R3-2, R3-3*, R3-4…R3-9, R3-10)
Owns: `services/summarizer/src/services/pipeline/pipeline_helpers.py`, `phases/qdrant_index.py`,
`src/services/transcript/english.py`, `src/services/pipeline/translation.py` + new
`src/services/pipeline/translation_flat.py`, `src/config.py` (`_STAGE_TO_SETTING` only),
`src/routes/pipeline_orchestration.py` (`run_pipeline_phases`, :95-116) + `src/routes/run_timing.py`
(`persist_run_timing`) for R3-1's bounded wait, their tests (`test_pipeline_helpers.py`,
`test_qdrant_index.py`, `test_transcript_english.py`, `test_translation.py`,
`test_prompt_render_placeholders.py` patch targets only — F2 adds a test there too: append, don't
reorder), docs `docs/ARCHITECTURE.md`, `docs/SERVICE-SUMMARIZER.md` (only the Whisper/Qdrant lines
of R3-10), `PROJECT-BRIEFING.md`, `docs/RAG.md` if a fix changes what it says.
- R1-1 + R3-1: one module-level strong-reference set + `spawn_background(coro, name)` in
  pipeline_helpers (add, done-callback discard + `log_background_error`); use it in
  qdrant_index.py. The transcript-English task must be awaitable by the runner: bounded wait
  (≤ 60 s) before `persist_run_timing`, AFTER `done` is sent (never delays `done`). Report the
  `phases/assembly.py:~419` S3 store `create_task` → `spawn_background` change for the
  coordinator (assembly.py is F3's).
- R3-3: `PipelineContext.audio_path` lives in `context.py` (F3's file) → report the deletion
  (field + `tests/test_pipeline_version_single_source.py:144`) for the coordinator; do the
  qdrant_index / `_SourceTranscript` side and the :303 test rewrite yourself.
- R3-5, R3-6, R3-8, R3-9 are output-changing (RAG text/timestamps/model) — say so in the report.

## F2 — prompts + cache tooling (R2-1 … R2-5)
Owns: `services/summarizer/src/prompts/plan.txt`, `prompts/description_analysis.txt`,
`src/services/video/description_analyzer.py`, `src/services/pipeline/plan.py` (comment only),
`scripts/cache_hit_rate.py` + `tests/test_cache_hit_rate.py`, `tests/test_plan_prompt.py`,
`tests/test_prompt_render_placeholders.py` (append the no-`{{` regression test), `docs/llm-cost-model.md`
(R2-5 wording + add `summarize:rag_translation` to the feature list for R3-10).
- R2-1: un-double braces in plan.txt; check the placeholder set is unchanged and the plan's static
  half (cached block) still renders; report the new static-half token estimate if it moved.
- R2-5: reword "switch to 5m" to state the economics (one plan call per run → a 5m write is never
  read; below ~53 % hit rate a 1h write costs more than no breakpoint) and that the choice is
  Kfir's (gate-2 decision) — do not change behaviour.
- Prompt registration is the coordinator's (dev runs `PROMPT_SOURCE=disk`); list the prompts to
  re-register.

## F3 — emission contract (R4-1…R4-3, R4-5…R4-9, R4-x `replaces`, R4-y)
Owns: `src/services/pipeline/tab_ledger.py`, `phases/moments.py`, `phases/assembly.py`,
`phases/late_quiz.py`, `phases/synthesis.py`, `assembly/core.py`, `assembly/moment_fallback.py`,
`src/services/pipeline/context.py`, `src/models/sse_events.py`, `src/routes/cached_response.py`,
`src/routes/pipeline_broker.py` (NOT `pipeline_orchestration.py` — F1's; report changes there),
`src/services/cache/pipeline_event_stream.py`, `scripts/expire_leaked_pipeline_streams.py`,
`packages/types/src/api.ts`, `api/src/routes/stream.routes.ts`, `docs/API-REFERENCE.md`,
`docs/DATA-MODELS.md`, `src/services/pipeline/pipeline_timing.py` (R4-5), `.github/workflows/ci.yml` (R4-2),
`api/src/routes/stream.routes.test.ts` + new `stream.routes.relay.test.ts` (R4-9), and their tests.
- R4-1: keep the always-Moments tab's append position; exclude it from `_moment_thumb_count` and
  the minimum-3 count so the frames-gallery / `key_info` behaviour of v8 docs is unchanged. Test both.
- R4-3: prefer deleting Fallback 3 if `always_moments_tab` fully supersedes it (prove with a test
  that the forbidden-domain case adds no moment_track).
- R4-5: add milestone `firstAssembledTabMs`: the first
  NON-provisional `tab_ready`; keep `firstTabReadyMs` as is; `tabsEmitted` excludes a withdrawn
  provisional id (or document); update DATA-MODELS.md.
- R4-6: API-REFERENCE.md: a corrected `final` can follow for the same id; the last final wins
  (the web reducer implements "last final wins"; 2.2 is told).
- R4-x `replaces`: when the tab that supersedes the provisional Moments tab has a DIFFERENT id,
  its first `tab_ready` carries `replaces: "<provisional id>"` (typed in `sse_events.py` +
  `packages/types/src/api.ts`, relayed by the API untouched; documented in API-REFERENCE.md).
  The web (2.2) takes the provisional's slot + focus on `replaces`; `complete.tabIds` stays the
  fallback. Real-broker replay test for the different-id case if the cassettes allow, else a
  ledger/unit test.
- R4-8: the guard goes INSIDE `phases/moments.py` (a safe wrapper around the builder) — the call
  site in `pipeline_orchestration.py` is F1's file.
- Cross-file items from F1 that land in your files are applied by the COORDINATOR after both of
  you finish (`assembly.py` S3 `spawn_background`, `context.py` `audio_path` deletion) — don't
  wait for them.
- api: `cd api && npm test` (under the lock) + eslint/no-debug on your api files; `tsc --noEmit`
  in api + packages/types; rebuild `packages/types` dist the way CI does before web/api tsc.

## R4-4 (golden tab-count scoring) → F2
Also owns `scripts/_eval_scoring.py`, `dev/golden-dataset/videos.yaml`, their tests. Kfir's rule
(2026-10-12) makes a Moments tab standard whenever a moment source exists. Align the golden
EXPECTATIONS with the rule (not a scorer special-case that hides tabs): for each golden video whose
`expectedTabs` lacks a moment_track tab, add it (unless its domain forbids moment_track). Show the
before/after list in your report; the change is declared in g2.
