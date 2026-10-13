# Phase-3 review — recipe for review agents (read-only)

You review ONE phase-3 task's UNCOMMITTED diff (or, at the end, the whole phase as one unit) in
/home/kfir/projects/video-insight-engine (branch feat/pipeline-1min). READ-ONLY: no file edits, no
git mutations, no pipeline runs, no LLM spend, no container commands. Targeted pytest/vitest on a
few files is allowed; nothing heavy (no full suites, no pyright on > 10 files, no `tsc -b`) — other
agents are using the box's memory.

1. Read the project's review recipe `.claude/commands/review.md` fully and apply it (labels,
   severities, compliance checks; skill files: `.claude/skills/backend-python/SKILL.md` for
   services/, `backend-node` for api/, `react-vite` for apps/web/).
2. Context: `dev/active/pipeline-1min/designs/phase3-design.md` (binding design), your task's brief
   (named in your group line), `pipeline-1min-tasks.md` Phase 3 rows (incl. Kfir's sub-items),
   `pipeline-1min-plan.md` §Phase 3, `pipeline-1min-brief.md` §Phase 3 + Appendices A–C,
   `pipeline-1min-context.md` (decisions D1–D28). The brief + Kfir's sub-items are the spec.
3. Review the diff: `git diff -- <paths>` + the new files the coordinator lists. Other phase-3 agents
   may be editing other files right now — judge only your task's paths, but report an interaction
   with already-committed phase-3 work when you see one.
4. Focus: correctness bugs, async/concurrency mistakes (blocking calls in async, unawaited or
   leaked tasks, cancellation, semaphores, first-token release races), error handling, data-contract
   breaks (SSE events, Mongo doc shape incl. `pipeline.*`, prompt placeholders, registry keys vs
   readers, `packages/types` vs Pydantic), **`EXTRACTION_PARALLEL=false` byte-identity** (any change
   that can alter today's single extraction request), cache-block layout (block order, TTL order 1h
   before 5m, identical bytes across groups), output changes the report does not declare, test gaps
   for the acceptance criteria, dead code (incl. renderer outputs nobody consumes), project rules
   (functions < 50 lines, files < 500 lines — report only NEW violations or growth caused by the
   diff). Anything outside the nine phase-3 tasks → a "g3 question" line, not a finding.

OUTPUT (≤ 60 lines, no preamble): one line per finding, Critical first, then at most 10 Warnings,
no Info:
`[C|W] <label> | <file>:<line> | <issue> | <why it matters> | <concrete fix> | output-changing: y/n`
Then optional `g3 question:` lines. End with one line:
`files reviewed: N; findings: C=<n> W=<n>`.

## Groups (id → task → paths → focus)
(added by the coordinator per task)
- R31 3.1 → uncommitted: `services/summarizer/src/services/pipeline/schema_fields.py` (new), `services/summarizer/tests/test_schema_fields.py` (new) → design §3.1 + §9. Check: every schema parses after `>` lines are dropped; slices keep parents of dotted paths and nothing else; scalar list matches the registry (a registry field or its parent is never a scalar); unknown/unsafe domain names; the loader is the same one the extraction prompt uses (registry-first, `PROMPT_SOURCE`) and its cache; nothing built that no consumer (3.3 scalars, 3.4 emit-only + shape line, 3.5 key filter) will use.
- R32 3.2 → uncommitted: `reconcile.py` (new), `plan_reconcile.py`, `plan.py`, `phases/text.py`, `phases/triage.py`, `routes/pipeline_orchestration.py`, `domains.json`, maybe `domain_config.py` / `pipeline_types.py`, tests (`test_reconcile.py` new, `test_plan_reconcile.py`, `test_datasource_registry.py`, plan-phase + replay order tests) → design §3.2 + §9 A1–A5, brief Appendix C, Kfir decision 7. Check: strict doubly-false everywhere evidence is read downstream (needs_quiz, video_memory, assembly backstop, demotion_blocked) and memory missing = no opinion; demotion onto an eligible source never already shown, else drop; deferral of code-gated tabs and the frames-done decision on BOTH paths (incl. LOW tier / frames off / early frames returns); meta + triage_complete after reconcile, never listing a dropped tab; `pipeline.reconcile` + `droppedTabs` shapes vs readers (api / web / admin); 3–6 never trims a required tab; the false-path extraction request unchanged on the cassettes (agent ran the baseline capture); registry JSON vs `@vie/shared` types.
- R33 3.3 → uncommitted: `services/summarizer/src/services/pipeline/field_groups.py` (new), `services/summarizer/tests/test_field_groups.py` (new) → design §3.3 + §9 A6. Check: requested set (planned, `<d>.*`, siblings only in active domains, side-read table matches what assembly actually reads, scalars once per domain in its first group); partition; split never cuts a field; ≤ maxTextGroups merge rule; text1 rule incl. a visual first tab; deterministic order; registry values read, not hardcoded; expect default.
- R34 / R35 / R36 / R37 / R38 / R39 → added per task (paths from each agent's report).
- R36a / R36b (3.6) → uncommitted: new `phases/group_emission.py`, `tests/test_group_emission.py`; modified `tab_ledger.py`, `context.py`, `phases/assembly.py`, `phases/enrichment.py`, `phases/split_extraction.py`, `tests/test_tab_ledger.py`, `tests/test_phase_enrichment.py`, `tests/replay/test_replay_tab_contract.py`, `scripts/replay.py` → `brief-3.6.md`, design §3.6 + §9 A1/A17.
