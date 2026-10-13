# Briefs 3.7 (long videos) ∥ 3.8 (failure paths) — coordinator, 2026-10-13

Run IN PARALLEL on disjoint files (design §6 row "3.7 ∥ 3.8"). Agent rules: `agent-rules.md` (Phase 3 first). Design:
`designs/phase3-design.md` §1, **§3.7 / §3.8**, §5, §8 3.7/3.8 tests, §9 (A13). Committed phase-3 stack:
`git log --oneline 41501dd..HEAD`. Open questions: `gates/g3-notes.md` (don't solve them).

## 3.7 — long videos (owner: `extraction_chunks.py`, new `tests/test_extraction_chunks.py`, synthetic 90-min fixture)
Spec: tasks.md 3.7 ("outline-merged chunks ≤ 50k tokens / 40 min; chunks × groups; annotations sliced per chunk;
chapter_detect only without outline + synthetic 90-min test"); brief §Phase 3 long-videos bullet.
- `plan_chunks(ctx) -> list[ExtractionChunk(index, transcript, batch_context, start_s, end_s)]`: when
  `needs_chapter_batching(duration, transcript)` is false → ONE chunk (no chapter work, no description wait); else
  today's `_split_chapters` chain (creator chapters → description → memory outline; `chapter_detect` only without an
  outline, D6) → `batch_chapters` (≤ 50k tokens / 40 min) → chunk transcript = `_build_batch_transcript`, context =
  `_build_batch_context`, annotation window per chunk. Reuse today's helpers WITHOUT changing the false path's request
  bytes (identity test; make helpers public only if bytes stay identical — else thin wrappers).
- Calls = chunks × groups under the ONE semaphore; each chunk warms its own prefix with its own text1 (per-chunk warm
  event); the visual group's annotations + key_frames sliced to the chunk window; merge chunk-major (3.5 already
  orders by chunk); per-group emission (3.6) treats a field as landed only when every chunk's call for it landed.
- Tests (design §8 3.7): one chunk under 40 min / 50k tokens · chunks × groups under one semaphore for a synthetic
  90-min transcript · annotations sliced per chunk · each chunk's text1 warms its prefix · chapter_detect only without
  an outline.
- If you need a change in `extraction_groups.py` / `split_extraction.py` / `group_emission.py` that 3.8 also touches,
  report it (3.8 owns `extraction_groups.py`'s failure hook); coordinate through the coordinator.

## 3.8 — failure paths (owner: new `group_failures.py`, `extraction_groups.py` failure hook, new `tests/test_group_failures.py`)
Spec: tasks.md 3.8 ("group fail → demote/drop + `droppedTabs`; all fail → error; plan fail → fallback set").
- A group = one `call_llm_with_retry(max_retries=1)` + parse; a parse failure (or a parsed answer `validate_group`
  keeps nothing from — R35) → ONE more call (`max_retries=0`); still nothing → `status="failed"`. Failed fields count
  as landed (tabs never wait forever). Final assembly then drops/demotes their tabs through today's fallbacks;
  `relabel_group_drops(dropped, failed_fields)` rewrites those droppedTabs reasons to `extraction_group_failed`.
  Degraded coverage already flagged (3.4 W2) — keep it. All groups of every chunk failed → today's error (`FAILED`,
  `LLM_ERROR`, "Extraction failed to produce data"). Plan failure → `_build_fallback_plan` tabs feed
  `requested_fields` unchanged (expect defaults, no evidence → no reconcile action).
- Tests (design §8 3.8): other groups' tabs ship + `extraction_group_failed` recorded when one group fails twice ·
  one retry on unparseable JSON · run fails as today when every group fails · fallback plan's default fields requested
  when the plan call fails.

## Both
Hard rules: `EXTRACTION_PARALLEL=false` untouched (identity test green); files < 500, functions < 50; no prompt edits;
no env change; outside-the-nine-tasks → g3 question. Flow: implement + tests + targeted runs + replay both modes
(heavy lock) → precommit → pyright → STOP uncommitted → review → fold → full suite → commit `p3.7 …` / `p3.8 …`.
