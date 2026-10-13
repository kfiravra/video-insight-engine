# Brief 3.4 — firing (field groups, three cache blocks, warm-first) — coordinator, 2026-10-12

Spec: tasks.md Phase 3 row 3.4 + Kfir's two sub-items (three cache blocks; warm-first). Design: `designs/phase3-design.md`
§1, **§3.4**, §3.7 (one chunk now — `plan_chunks` returns one chunk until 3.7), **§4**, **§5**, §6 row 3.4, §7 items
1/8/15/16/17, §8 3.4 tests, **§9 (A1, A7, A15, A16 binding)**. Agent rules: `agent-rules.md` (Phase 3 section first).

## What exists (committed)
- 3.1 `schema_fields.py` (49af6e8): `load_domain_schema` (`DomainSchema.broken`), `scalar_fields`, `slice_skeleton`
  (raises `SchemaParseError` on a known-but-unparseable schema → fall back to the unfiltered domain block),
  `render_emit_only(slices, caps)` (STRICT: `caps` must name every requested path, None = no cap), `keep_requested`
  (for 3.5); `extraction_prompt.load_schema_text` (public).
- 3.3 `field_requests.py` + `field_groups.py` (b430a90): `requested_fields(tabs, content_tags, modifiers, scalars_of,
  *, primary_tag=...)` (pass `plan_result.primary_tag`; wire `scalars_of=schema_fields.scalar_fields`),
  `build_groups(fields, tabs, config)`, `visual_group(fields, fired)` (pass the paths the text groups already fired; it
  includes every requested field not fired — covers 3.2's frames-done demotion onto a text source).
- 3.2 reconcile (1e8ac82; frames-done helper = `frames_done_events()` in `phases/triage.py` — re-runs backfill/quiz-last/3–6 and yields a fresh `meta` when tabs changed): `reconcile_at_plan_done` in `phases/text.py`;
  frames-done `resolve_deferred_tabs` (+ fresh `meta` + re-fill when tabs changed) at the `visual_inject` step on
  today's path. On the group path the frames-done hook (`then_frames_done`) must call the SAME helper before the
  visual group is cut/fired.

## Build (design §3.4)
- `ctx.plan_done` / `ctx.frames_done` events; `then_frames_done(run_phase_frames)` sets `frames_done` in a `finally`
  after rendering `ctx.visual_annotations` (same function/inputs as `_render_visual_annotations`) and running 3.2's
  frames-done helper; every early return of frames included.
- `run_phase_split_extraction` member in the phase-two group (true path only): await `plan_done` → `requested_fields`
  → `build_groups` → `plan_chunks` (one chunk) → fire: text1 STREAMED (`on_first_chunk` → `warm.set`), others wait
  `warm` (fallback `GROUP_RELEASE_FALLBACK_S = 1.5` module constant) then the semaphore (`EXTRACTION_PARALLEL_BATCHES`,
  text1 acquires first); visual group: await `frames_done` → `visual_group(fields, fired)` (empty → not fired) → await
  warm → semaphore. Per-group `extraction_progress {section:"groups", batch, of}`; ONE merged `extraction_complete`.
  Interim validation/merge/assembly = today's functions (A15): `validate_domain_output_with_drops`,
  `merge_batch_extractions`, today's single assembly after the last group.
- **Three blocks** (no system prompt): block 1 = `text_block(system + <schema>… + <extraction_example…>, ttl="1h")`
  cut from today's `build_extraction_prompt` output (unmodified), identical for every group/chunk of the run; block 2 =
  today's head block (5m); block 3 = the group tail from NEW `prompts/extraction_group_job.txt` (`<your_job>` with
  `render_emit_only` + shape line, tabs-to-serve for this group's tabs, content emphasis, empty-array line; visual group
  adds `<visual_context_guide>` + annotations + `<key_frames>`). 1h before 5m; 2 breakpoints.
- Calls: feature `summarize:extraction:{gid}`, span/stage `extraction_{gid}` (chunk k ≥ 1 → `_c{k}`), Haiku via
  `get_stage_model("extraction")`, max_tokens 16384, timeout 120, max_retries 1, JSON. Ledger rows by feature = cost by
  group; `pipeline.timing` llmCalls gain `ttftMs` (schema v3).
- Streaming: `services/llm_streaming.py` (`stream_completion(kwargs, on_first_chunk)`), threaded through
  `call_llm_with_retry` → `LLMService.call_llm` → `LLMProvider._run_completion`; additive — NO `stream` key unless
  `on_first_chunk` is passed (byte identity). `stream_chunk_builder` rebuilds the response for usage/ledger. **A16: one
  DEV probe allowed (≈ $0.01, Haiku 4.5, same small prompt streamed vs not) to confirm cache read/creation (1h/5m)
  usage survives streaming + the ledger callback fires once; if fields drop, read the final chunk's usage directly.**
  Report the spend.
- `EXTRACTION_PARALLEL=false` → `_run_phases_in_order` today's path, untouched except 3.2's call. Docs/comment lines for
  the flag's new meaning (config.py comment, `docs/INFRASTRUCTURE.md`, `docs/SERVICE-SUMMARIZER.md`) — no env change.
- Prompt: register `summarizer:extraction_group_job` to **DEV** Langfuse only (find how phase 2 registered single
  prompts to DEV — `scripts/register_prompts.py`; never prod). Dev runs use `PROMPT_SOURCE=disk` anyway.

## Replay (design §4, §5)
- `tests/replay/test_replay_extraction_identity.py` + `tests/replay/baselines/<vid>.extraction.json` (from
  `<scratch>/baseline/<vid>.jsonl` extraction line + sha256; `canonical_request` copied verbatim from
  `<scratch>/baseline/capture.py`): each cassette with `EXTRACTION_PARALLEL=False` → exactly one extraction call, sha256
  == baseline; assert the premise first (plan-done reconcile records == [] and tabs == plan-parse tabs).
- `tests/replay/group_synth.py` + `fake_llm.py`: group calls answered from the recorded single extraction entry
  (sliced to the group's keys, tokens pro rata, wall = TTFT + out/R; streamed iterator for `stream=True`). Never a
  `CassetteMissError` for `summarize:extraction:*`. `scripts/replay.py` prints per-group start/TTFT/wall/out and runs
  groups at `EXTRACTION_PARALLEL_BATCHES=6`. Replay suite green in BOTH modes.
- Switch `tests/test_field_groups.py`'s cassette compositions to `_normalize_plan_data` + `reconcile_at_plan_done`
  (R33 #7, deferred to you) — drop the hard-coded re-point.

## Tests (design §8 3.4)
stream only text1 + others after its first chunk · release after 1.5 s · release when text1 fails before its first
chunk · visual only after frames_done · never > EXTRACTION_PARALLEL_BATCHES in flight · block 1 ttl 1h before block 2
5m · blocks 1+2 hash-identical across every group · no system prompt · own feature per group · ttftMs recorded ·
today's exact request when EXTRACTION_PARALLEL is false (×3) · frames_done set on early frames returns · the frames-done
reconcile helper runs before the visual group is cut.

## Out of scope (later tasks)
3.5 per-group validation/merge (`group_merge.py`), 3.6 per-group assembly/emission + quiz task, 3.7 multi-chunk,
3.8 failure policy (a group that fails = today's error handling for now), 3.9 version bump.
