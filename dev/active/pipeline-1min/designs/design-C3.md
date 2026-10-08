# design-C3 — 1c.5 retry removal (without extractor.py / phases/synthesis.py)

Read at HEAD eabe954. The count gate is already a no-op (`validate_extraction_counts` returns
`{}` since 1b.2), so the only live trigger left is `score < 0.6` (A25: 7/55 firings, 0/3
improved). All 3 cassettes record one `summarize:extraction/extraction#0`, no retry → replay
unaffected.

## File-by-file

### 1. `src/services/pipeline/extraction_quality.py` (309 → ~95 lines)
- DELETE: `RETRY_SCORE_THRESHOLD`, `HARD_MISS_SCORE_GATE`, `NARRATIVE_FORMATS`, `STEP_LIKE_FIELDS`,
  `_is_narrative_content`, `RetryDecision`, `decide_extraction_retry`, `merge_retry_fields`,
  `build_synthesis_fed_retry_prompt`.
- KEEP unchanged: `ExtractionQuality` (dataclass), `_resolve_dot_path`, `check_extraction_quality`.
- Imports: drop `logging`/`logger` (no longer used), `TYPE_CHECKING` + `ContentTraits`. This
  removes the module's dependency on `classifier.py`, which helps the wiring agent delete the
  classifier.
- Module docstring: "Extraction quality metric: share of planned extraction dataSources the
  extraction populated."

### 2. `src/services/pipeline/post_processor.py` (281 → ~230 lines)
- DELETE the whole "Extraction Count Validation (retired)" section: `_COUNT_EXTRACTORS`,
  `_derive_field_to_domains`, `FIELD_TO_DOMAINS` (its only reader is `decide_extraction_retry`),
  `validate_extraction_counts`.
- Drop the `TYPE_CHECKING` / `PlanResult` import (now unused). `Any` stays (`_collect_offsets`).
- KEEP: the coverage block (`COVERAGE_*`, `_TIMESTAMP_PATHS`, `_collect_offsets`,
  `coverage_is_degraded`, `compute_extraction_coverage`) and the four older helpers. Leave alone
  (out of brief): `drop_empty_tabs`, `assign_section_accents`, `merge_narrative`,
  `resolve_celebrations` have no src caller, and `_CELEBRATION_TAB_IDS` lists
  flashcards/scenarios → candidates for the 4.1 sweep.

### 3. `src/services/pipeline/phases/extraction.py` (387 → ~290 lines). Shared with C2.
My hunks only:
- Module docstring line 1: "… with count validation" → "… with quality and coverage metrics".
- Imports: drop `Any` (line 7); the whole `extraction_quality` import block becomes
  `from src.services.pipeline.extraction_quality import check_extraction_quality`; drop
  `truncate_json_safely` (only the retry used it) and `validate_extraction_counts`; drop
  `from src.services.pipeline.synthesis import synthesize` (line 33).
- DELETE `_attempt_synthesis_fed_retry` (lines 120-189).
- `run_phase_extraction` docstring: "Run adaptive extraction, then record coverage and quality
  metrics."
- REPLACE lines 327-387 (the quality gate, count validation, retry decision, retry call and
  "count mismatch" warning) with one call `_record_extraction_quality(ctx)`, placed after
  `_record_extraction_coverage`. The new helper (~20 lines) sits next to
  `_record_extraction_coverage` and mirrors it:
  ```python
  def _record_extraction_quality(ctx: PipelineContext) -> None:
      """Log the share of planned dataSources the extraction populated (metric only).

      The synthesis-fed retry this score used to gate was removed (pipeline-1min 1c.5):
      7 firings in 55 runs, none of them improved the output.
      """
      if ctx.plan_result is None or not ctx.extraction_data:
          return
      quality = check_extraction_quality(ctx.plan_result.tabs, ctx.extraction_data)
      logger.info("pipeline.extraction_quality score=%.2f populated=%d/%d", quality.score,
                  quality.populated, quality.total,
                  extra={"video_id": ..., "score": ..., "populated": ..., "total": ...,
                         "empty_fields": quality.empty_fields})
  ```
  The `ctx.triage is not None` condition is dropped because the metric does not need it.
  `count_warnings` and `hard_miss_fields` leave the log line.
- NOT touched: `build_prompt_transcript` and its `inject_visual_context` import/call (C2's), the
  chapter-splitting block, and the main `extract(...)` call (C1 for 1c.3, D4 for the heartbeat).
- After the change the phase no longer reads `ctx.content_format`, `ctx.content_traits`,
  `plan_result.content_tags` or `ctx.video_dna_compact`-for-retry, and never sets
  `ctx.synthesis_dict`.

## Tests
- `tests/test_extraction_quality.py`: delete `TestBuildSynthesisFedRetryPrompt`,
  `TestDecideExtractionRetry`, `TestFormatAwareRetryGate` and `TestMergeRetryFields`, and their
  imports (`ContentTraits`, `HARD_MISS_SCORE_GATE`, the 3 functions, `_is_narrative_content`).
  KEEP `TestIsEmptyData`, `TestResolveDotPath` and `TestCheckExtractionQuality` (the metric test).
  Docstring updated. ~175 lines.
- `tests/test_post_processor.py`: delete `TestValidateExtractionCounts` plus the
  `validate_extraction_counts` and `PlanResult` imports. KEEP `TestComputeExtractionCoverage`
  (the coverage metric test).
- `tests/test_extraction_coverage.py`: unchanged (kept).
- NEW `tests/test_extraction_phase_metrics.py`. Its ctx builder copies the one in
  `test_extraction_prompt_transcript.py`, plus `plan_result=SimpleNamespace(tabs=[…])` and
  `synthesis_dict={}`; `extract` is patched with a counting async generator.
  - `test_should_call_extraction_once_when_planned_fields_come_back_empty`
  - `test_should_keep_the_first_extraction_when_quality_is_low`
  - `test_should_leave_synthesis_to_its_phase_when_quality_is_low` (`ctx.synthesis_dict == {}`)
  - `test_should_log_the_quality_score_when_a_plan_is_present` (caplog record
    `pipeline.extraction_quality`, `score == 0.5`)
  - `test_should_skip_the_quality_metric_when_there_is_no_plan`
- Run (targeted): `pytest tests/test_extraction_quality.py tests/test_post_processor.py
  tests/test_extraction_coverage.py tests/test_extraction_prompt_transcript.py
  tests/test_extraction_phase_metrics.py tests/test_pipeline_runner.py -q`; ruff check + format on
  the 3 src files and 3 test files; pyright on the 3 src files.

## Interfaces
- Provide: nothing new. Removed public names (grep-verified, no readers outside my files and their
  tests): the 9 extraction_quality names and the 4 post_processor names listed above.
  `check_extraction_quality` keeps its signature, because `scripts/eval_extraction_models.py:62,376`
  imports it.
- Need: nothing.

## Hand-offs (files I don't own)
- **C1 (extractor.py)**: once C3 lands, `extract(extra_instruction=…, force_primary_model=…)` has
  no src caller. Remove both params, the `<retry_guidance>` block and its SECURITY comment
  (`extractor.py:146-147,158,199-202`) together with fast-first (`:219-221`).
  `tests/test_chunked_extraction.py:703` tests `force_primary_model`.
  `scripts/eval_extraction_models.py:345` (+ `tests/test_eval_extraction_models.py`) passes
  `force_primary_model=not use_fast`: that is a fast-vs-primary A/B tool with no owner, and it
  loses its point once fast-first is gone → coordinator decides (delete or adapt).
  **Order: C3 before C1's param removal.** Otherwise the retry call site breaks pyright for a
  moment.
- **D2 (phases/synthesis.py:51-60)**: the "already populated (from extraction retry)" branch can
  no longer be reached (`ctx.synthesis_dict` is set only by `phases/synthesis.py` itself). It is
  already in D2's brief.
- **D4 (tests/replay/cassette.py:45)**: the docstring still names the "synthesis-fed retry under
  summarize:extraction" → stale wording only.
- **Wiring / classifier deletion**: two fewer readers of `ContentTraits` and
  `ctx.content_traits`/`content_format`. The CODE-MAP rows `extraction.py:316` and
  `extraction_quality.py:188` disappear.
- **Docs (unowned, stale after C3)**: `docs/summarizer-workflow.md:191,199` + Scenario E
  `:398-410` (the whole scenario becomes obsolete); `docs/SERVICE-SUMMARIZER.md:63,404-407,594,
  938,941,964`; `docs/ARCHITECTURE.md:196`; `PROJECT-BRIEFING.md:154`. → coordinator /
  update-docs at phase end, or give them to me.
- Settings: none (`EXTRACTION_USE_FAST_FIRST` is C1 code + D5 touch points).

## Coordination with C2 on phases/extraction.py
Disjoint hunks except the import block. C2 removes line 32 (`inject_visual_context`); I remove
line 33 (`synthesize`) and lines 13-18/23/29 → they sit next to each other. Before each Edit I
re-read the file and use small anchors that leave C2's lines out, so a mismatch fails the Edit
rather than clobbering. Either order works; the coordinator may need hunk-wise staging if both
are in flight.

## Risks
- **Output change (explicit):** runs that would have triggered the retry (score < 0.6, about 13%
  of the ledger) now ship the first-pass extraction. Synthesis then runs in its own phase on the
  same extraction instead of early. These runs save about 1 extra extraction plus 1 synthesis
  call (A25: $0.077 + 103 s across 7 firings). All other runs are byte-identical.
- With no retry, a genuinely thin extraction has no in-run recovery until 3.8 (per-group retry).
  The brief accepts this.
- The `ctx.synthesis_dict` short-circuit in phases/synthesis.py stays as dead code until D2's
  commit. It is harmless.

## Open questions
1. Metric visibility: the stdlib handler (`format="%(message)s"`) drops `extra=`, so today's
   `pipeline.extraction_quality` line shows no numbers. Planned: put `score`/`populated`/`total`
   in the message text and keep `extra`. Say no and I keep today's message unchanged.
2. Docs listed above: mine or the coordinator's?
3. `scripts/eval_extraction_models.py`: delete with fast-first (C1), or keep?
4. Dead post_processor helpers (`drop_empty_tabs` & co.): leave them for 4.1 (default), or remove
   now?

Suggested commit: `p1c.5 perf(summarizer): drop the synthesis-fed extraction retry and count gate — quality and coverage stay metrics`
