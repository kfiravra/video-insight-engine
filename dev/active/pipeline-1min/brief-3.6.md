# Brief 3.6 — per-group assembly + emission — coordinator, 2026-10-13

Spec: tasks.md Phase 3 row 3.6; plan table 3.6 ("a tab assembles when all its requested fields landed;
`tab_ready{final:true}`; attachments at the final re-emit; overview/meta last; `resolve_cross_tab_links` at the end.
Quiz after its owning group; synthesis ∥ moment fill ∥ faithfulness after the last group"); brief §Phase 3 + B.6.
Design: `designs/phase3-design.md` **§3.6**, §1 (assembly/emission today, TabLedger), §6 row 3.6, §7 items 1/12/17,
§8 3.6 tests, **§9 A1 (binding: per-group `tab_ready` = `final:false`, closing re-emit sends every tab `final:true`
once) + A17 (deep-copy per partial assembly)**. Agent rules: `agent-rules.md` (Phase 3 first). Never edit
`assembly/core.py` (1,930 lines) — per-group code lives outside it.

## What exists (committed)
3.2 reconcile 1e8ac82 (plan-done + frames-done `frames_done_events`, fresh `meta`), 3.3 groups, 3.4 firing
(`extraction_groups.py`, `phases/split_extraction.py`, `phases/frames_done.py`; one merged `extraction_complete`;
today's single assembly after the last group; 9c6cd70), 3.5 `group_merge.py` (f42df42; + b7a2077 overview side reads) (`validate_group`, `merge_groups`,
`requested_quality`). Phase-2 emission contract: `pipeline/tab_ledger.py` (position, final, provisional Moments,
closing re-emit, `complete.tabIds`), web reducer 2.2 (a later final replaces; final never overwritten by non-final).

## Build (design §3.6)
- New `phases/group_emission.py`: `ready_tab_events(ctx, landed)` — `assemble_response` over a DEEP COPY of the
  merged-so-far extraction (no enrichment, memory-seeded synthesis); emit each tab whose every FieldRequest landed
  (ok or failed), except overview, moment_track (held for the fill, as today) and quiz_arena; payload minus
  crossTabLinks + attachments; position = its index in that assembly; `final=False`; skipped when the same fingerprint
  already went out.
- Called by the split member after each group lands. `TabLedger` gains `groups_pending` (`_settled` false while set).
- `run_phase_assembly` split mode: skip `_first_tab_events` (every tab already went out once, or is overview / moment /
  quiz); keep the late group = moment fill ∥ synthesis ∥ the quiz TASK — the member starts `ctx.quiz_task` when every
  text group holding a field of a `quizDomains` domain has landed (the visual group only if no such text group exists),
  input = merged output so far + video_memory (B.6); `run_phase_enrichment` awaits it instead of calling. Then
  `_add_late_quiz` (attachments, quiz last, links) → `closing_events` = every tab `final:true` at its final position,
  overview last-emitted → `complete`. Faithfulness after the member (frames-done guaranteed).
- False path: emission unchanged (today's `_first_tab_events` etc.); replay identity green.

## Tests (design §8 3.6)
tab only when all its fields landed · per-group tabs non-final, every tab final once at the end · never overview /
moment_track / quiz per group · links + attachments stripped from per-group payloads · quiz starts when its owning
text groups landed · synthesis, moment fill, faithfulness after the last group · no duplicate payload per tab (replay
tab contract ×3, `tests/replay/test_replay_tab_contract.py`) · a ledger "second final" never logged on the split path.

## After 3.6 (coordinator)
Replay ×3 with synthesized group timings + dev cold runs T1dQhQAm8Tc, jMq8lEu-of0, uC45_4nnEAI, EqaMrzd9nZU (spend
stated first) → numbers into `gates/g3-notes.md`.
