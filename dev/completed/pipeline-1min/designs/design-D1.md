# D1 design — 1d.1 quiz-only enrichment · 1d.2 quiz policy · 1d.7 evidence-gated backfill

Read on HEAD eabe954 + in-flight wiring. At GO, re-read `enrichment.py` + `phases/enrichment.py` first (the
wiring agent may swap `{video_context}` → `ctx.video_memory` there); I build on what it left.

## Facts that shape the design
- `pipeline_orchestration.py:23` imports `_has_meaningful_data` from `enrichment.py` → the name stays.
- Toolkit `<valid_datasources>` renders from `dataSources`, and `test_datasource_registry` resolves every
  `enrichment.*` path to an `EnrichmentData` field → registry entries and model fields go together.
- Wiring item 8: `ctx.triage_dict` carries `evidence` → assembly reads it; `phases/assembly.py` untouched.
- Montreal r2 (dev doc 6ac65c737e…): its `ingredients` checklist was a BACKFILL (unplanned, no `degradedFrom`).
- No enrichment settings besides `LLM_ENRICHMENT_MODEL` (caps were module constants) → nothing for D5.

## 1d.1 — files
- NEW `src/prompts/enrich_quiz.txt` (registry `summarizer:enrich_quiz`; slots `{flavor}`, `{video_memory}`,
  `{tab_goals}`, `{extraction_data}`; single braces in the JSON example like memory.txt — the old files' `{{ }}`
  reached the model literally). Sections: `<role>` "You write a short self-check quiz for one video, from what
  the video says." + `{flavor}`; `{video_memory}` bare (block has its own tags); `<extracted_content>`;
  `<tab_goals>`; `<quiz_rules>` from enrich_study: 2–8 questions — the quiz_arena tab's `expect` when one is
  listed, else 3; fewer when the content is thin, never pad; specifics from the video, not trivia/general
  knowledge; spread across topics, no two on one fact; one "which is NOT/FALSE" when ≥ 3; ~30/50/20
  difficulty; exactly 4 options, one correct, 0-based `correctIndex`, vary its position; explanation 1–2
  sentences teaching the why, cite `m:ss` when the content gives a timestamp; `<output_format>` "Return ONLY
  `{"quiz": [...]}`" + one example item. ENGLISH_OUTPUT_DIRECTIVE prepended in code (as today).
- DELETE `src/prompts/enrich/` (11 files).
- `src/models/pipeline_types.py`: `EnrichmentData` = `quiz: list[QuizQuestion] = []`; `QuizQuestion` strict:
  question non-blank, 2–6 non-blank options, `correctIndex` in range (no clamping — a clamped index is a
  wrong answer key), explanation non-blank. DELETE `Flashcard`, `CodeCheatSheetItem` (0/43 docs, no prompt
  asks), `ScenarioOption`, `ScenarioItem`. (D2 touches only `SynthesisResult`.)
- `src/services/pipeline/enrichment.py` (rewrite, ~170 lines). Removed: `ENRICHMENT_MAP`, `_load_prompt`,
  `_apply_output_caps`, `_MAX_QUIZ_QUESTIONS/_FLASHCARDS/_SCENARIOS`, synthesis-fallback context, the
  forbidden-strip (the gate makes it unreachable). Kept: `_is_nonempty`, `_has_meaningful_data`. New:
  - `needs_quiz(plan: PlanResult | None, content_format: str | None = None) -> bool` (for D4):
    False when plan is None or `not quiz_allowed(primary_tag, content_format)` (= primary ∈
    `quizEnrichment.quizDomains` AND quiz_arena ∉ `effective_requirements(...)["forbidden"]`); True when a
    tab is a quiz tab (component `quiz_arena` or dataSource `enrichment.quiz`) — the plan has the final say,
    no evidence check (phase-3 reconcile owns the doubly-false rule); else True when
    `evidence.get(quizPolicy.requiresEvidence) is not False` AND some tab's component `can_host_quick_quiz`.
  - `render_tab_goals(tabs) -> str`: one line per tab `- "label" (component): goal — what: …; expect ~N`
    (label/goal/what through `sanitize_for_prompt(max_len=300)`).
  - `build_quiz_prompt(primary_tag, extraction, video_memory, tabs) -> str | None` (None = no flavor line):
    extraction = FULL compact JSON (`json.dumps(separators=(",", ":"), ensure_ascii=False)`; no 8,000-char
    cut; `truncate_prompt_if_needed` stays the model-limit net); replace order flavor → tab_goals →
    video_memory ("Not available" when empty) → extraction_data last.
  - `parse_quiz(data: object, cap: int) -> list[QuizQuestion]` = salvage: list under `quiz` (or a bare
    list); each item validated alone (ValidationError → dropped, counted in a log line); dedupe by
    casefolded question; `[:cap]`, cap = registry `data_source("enrichment.quiz")["cap"]` (8).
  - `async enrich_quiz(llm_service, *, primary_tag, extraction_data, video_memory, tabs) -> EnrichmentData
    | None`: `async with asyncio.timeout(30)` around `call_llm_with_retry(max_tokens=2048, timeout=25,
    max_retries=1, stage_name="enrichment", json_mode=True, use_fast_model=True,
    model_override=settings.get_stage_model("enrichment"))`; < 2 valid questions → None; never raises
    (TimeoutError / unexpected → logged, None) — the memory.py pattern.
- `src/services/pipeline/phases/enrichment.py`: feature var `summarize:enrichment` (unchanged → cassettes and
  ledger keys hold); skip (log `pipeline.enrichment` skipped + reason, no SSE) when
  `not needs_quiz(ctx.plan_result, ctx.content_format)` or `not _has_meaningful_data(extraction)` — the
  phase stays safe even if D4 schedules it unconditionally; else `enrich_quiz(..., video_memory=
  ctx.video_memory, tabs=ctx.plan_result.tabs)` → `ctx.enrichment_data = {"quiz": [...]}` →
  `enrichment_complete`; log line keeps `quiz_count`, drops flashcard/scenario counts + synthesis fallback.
- `packages/shared/src/config/domains.json`: delete top-level `enrichment` map.
- `src/shared_config/domain_config.py`: delete `get_enrichment_map`.
- `src/prompts/component_toolkit.txt`: `quiz_arena ⭐ NEW (absorbs scenario)` → `quiz_arena`; drop
  "Accepts both standard quiz items AND scenario items (…)"; `DATASOURCE: enrichment.quiz.`
- Untouched (legacy rendering, phase-4 sweep): quiz_arena's scenario branch, `assemble_scenario`,
  normalizers, `registry.py` aliases, `domain_types.py` quizzes/scenarios/flashcards.

## 1d.2 — files
- `assembly/attachments.py`: public `can_host_quick_quiz(component) -> bool` = has a primary list
  (`_PRIMARY_LIST_KEY`) AND ∉ {quiz_arena, quick_quiz} AND ∉ `quiz_policy()["attachmentHostsExclude"]`
  (read per call — no import-time snapshot, the ENRICHMENT_MAP lesson); `attach_secondaries` uses it in
  place of `_NO_QUICK_QUIZ_COMPONENTS`. An excluded host falls through to tip_callout (existing chain).
- `assembly/core.py`: `_move_quiz_tabs_last(tabs)` in the "Overview Ordering Guarantee" section — stable
  partition, quiz_arena tabs to the end when `quiz_policy()["position"] == "last"`; called after the
  min-3 fallbacks (filmstrip auto-append and fallbacks append AFTER planned tabs today, so a plan-last quiz
  is not last in the output), before `_annotate_overview_item_count`. Links are id-based → unaffected.
- `assembly/promotion.py`: no change needed (see risk 3).

## 1d.7 — files
- `assembly/core.py`: `assemble_response` passes `evidence=triage.get("evidence")` to
  `_validate_domain_requirements(..., evidence: Mapping[str, bool] | None = None)`. The required pass becomes
  `_backfill_missing_requirements(...)` (extracted — the function is ~95 lines today; forbidden-pop and
  max-cap passes extracted too so each piece is < 50 lines, behaviour unchanged): for each unmet
  `required` (unconditional list), if it is absent from `effective_requirements(..., evidence=evidence)
  ["required"]` → no backfill, `logger.info("Requirement %s for %s not backfilled: %s=false in the plan's
  evidence")` + droppedTabs entry `{"id": "", "component": req, "dataSource": "", "reason":
  "requirement_evidence_false", "detail": "<key>=false"}` (key from `requirement_evidence()`); else backfill
  as today. Missing key = no opinion = backfill (D15). Plan evidence only — memory second opinion = phase 3.

## Interfaces
- Provide (D4): `needs_quiz(plan_result, content_format=None)`; the phase no longer reads
  `ctx.synthesis_dict` → the sequential fallback branch (`pipeline_orchestration.py:228-236`) can go.
  "Never blocks tabs": hard 30 s cap, None on failure. With D2's assemble-before-synthesis order D4 picks
  (a) quiz task from extraction-done, assembly awaits it only when `needs_quiz` (≈ 6–10 s on gpt-4o-mini),
  or (b) emit non-quiz tabs first, quiz tab (last) + quick_quiz host re-emit later. Recommend (a); (b) = 3.6.
- Need: `ctx.video_memory: str`, `ctx.plan_result` (evidence, tabs with `brief`), `ctx.content_format`,
  `ctx.triage_dict["evidence"]` (wiring item 8; if absent at GO → ask D4 to pass `evidence=` instead).

## Ownership requests (files outside my brief — coordinator please confirm before GO)
1. `domains.json` beyond the old map: delete `dataSources["enrichment.flashcards"]` and
   `["enrichment.scenarios"]`; reword `quizEnrichmentNote` (says the old map "still has readers until 1d")
   and the enrichment sentence of `dataSourcesNote`. Side effect: plan `<extraction_caps>` and toolkit
   `<valid_datasources>` lose 2 paths (plan prompt text changes — tiny, output-changing).
2. `packages/shared/src/config/index.ts`: drop `enrichment: Record<string,string>` + `getEnrichmentMap()`
   (no TS caller anywhere) + the legacy-map comment on `quizEnrichment`; then `tsc` on @vie/shared.
3. Shared/unowned tests, my hunks only: `test_datasource_registry.py` (render line), `test_prompt_registry.py
   :238` (→ `summarizer:enrich_quiz`), `test_prompt_render_placeholders.py` (enrichment section; C1/D2 share
   it), `test_pipeline_integration.py` (enrichment tests; D2 shares it), `test_domain_types.py` (delete
   `TestScenarioOptionCoercion`), `test_domain_config.py` (drop the map test), `fixtures/llm_responses/*.json`
   (trim `enrichment_response` to quiz so the canary stays a valid LLM output).
4. Sweep (not mine): `register_prompts.py:9` docstring, `prompt_builder._SUBDIR_LABEL["enrich"]`,
   `packages/types` optional flashcards/scenarios/cheatSheet (cached docs).

## Tests (targeted; `.venv/bin/python -m pytest <files> -q`)
- `tests/test_enrichment.py` (rewrite, < 400 lines): `TestNeedsQuiz` — should demand when a quiz_arena tab
  is planned / when a tab reads enrichment.quiz / when a learnable plan has an allowed host (info_grid) /
  when is_learnable is missing; should not demand when the only hosts are excluded (parametrized ×5) / when
  is_learnable is false and no quiz tab / for quiz-forbidden domains (food with info_grid) / when plan is
  None. `TestParseQuiz` — keep valid items and drop invalid ones (blank question, 1 option, index out of
  range, no explanation, flashcard-shaped item = the old json_object bug); dedupe; cap 8 from the registry;
  accept a bare list; ignore flashcards/scenarios keys. `TestBuildQuizPrompt` — flavor line per quiz
  domain; video_memory verbatim; a 20,000-char extraction appears whole; tab goals carry brief what/expect;
  no `{placeholder}` left; English directive first; None without a flavor. `TestEnrichQuiz` (patch
  `call_llm_with_retry`) — quiz-only EnrichmentData; None on LLM None / unparseable / < 2 valid; None when the
  30 s cap fires (cap patched small, slow fake); stage/model/max_tokens passed. `TestHasMeaningfulData` kept.
  `TestRemovedReaders` — no `enrich/` dir, no `enrichment` key in domains.json, no `get_enrichment_map`.
- NEW `tests/test_phase_enrichment.py` — should skip without an LLM call or SSE when not demanded / when
  extraction is empty; should emit enrichment_complete with only `quiz` when demanded.
- `tests/test_attachments.py` — no quick_quiz on each excluded host (×5) → tip_callout; info_grid hosts it.
- NEW `tests/test_requirement_evidence_backfill.py` — table over every `requirementEvidence` (domain,
  component, key): false → not backfilled + droppedTabs `requirement_evidence_false`; true / missing →
  backfill attempted (spy on `_backfill_required_component`; the gate decision is the subject); real data:
  montreal shape (has_ingredients/has_steps false) → no checklist/step_player; tech has_code true →
  code_playground backfilled. Quiz-last: planned-first quiz ends last after filmstrip + fallbacks.
- Lint: ruff check/format + pyright 1.1.407 on touched files; `npx tsc` in packages/shared (request 2).
Output-changing (gate): new quiz prompt; no call for 6 quiz-forbidden domains / undemanded plans; no
flashcards/scenarios; 2,048 max_tokens, 30 s cap, 1 retry; quick_quiz hosts; quiz last; gated backfill.

## Risks / open questions
1. droppedTabs: an evidence skip is not a planned tab dying, but `phases/assembly.py` sets `tabsDropped =
   len(droppedTabs)` → +1 per skip. Keep (brief says droppedTabs) or have D4 exclude reason
   `requirement_evidence_false` from the count?
2. Montreal passes only if the plan answers has_ingredients/has_steps = false (missing = backfill). The
   yaml xfail (`until: 1b.2 / 1d.7`, non-strict) is the eval owner's to drop at gate 1.
3. Demote ladder (`spot_explorer`/`info_grid` → `checklist`) can still put a checklist on a food vlog when a
   rich tab fails to assemble. Not seen in r1/r2; skipping evidence-false rungs = phase-3 reconcile, unless
   you want it now (small change in `demote_component` + its call site).
4. quick_quiz demand is plan-time (can't see item counts or whether frame_strip wins the slot) → some quiz
   calls for learnable plans go unused (B7 estimate ~5/23 educational docs); cost ≈ $0.001 each.
5. Replay (D4): jMq8's (food) recorded enrichment goes unused; T1dQ's quiz+flashcards reply salvages to
   quiz ≤ 8. Gate: register `summarizer:enrich_quiz` + the toolkit (same slots → the old registry toolkit
   wins on prod until re-registered); delete the 11 `summarizer:enrich:*` orphans in the UI.
