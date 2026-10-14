# Brief 4.2 — docs (parallel with 4.0 b–e, 4.1, 4.3)

Task row (tasks.md 4.2 + plan.md §Phase 4 4.2): "Docs: summarizer docs' stale lines (S3/region
defaults, "SHORT < 30 min" vs 900 s, "3–6 LLM calls", pass-2 stream seek, fast model on prod, vision
model, chunked semantics, new phases + v9 flow in `docs/summarizer-workflow.md` and
`SERVICE-SUMMARIZER.md`); README numbers that changed; eval sentence → "twice a month" after the
first scheduled run." AC: `/update-docs` pass; no stale claim left from the list.
Kfir 2026-10-13: "4.2 docs (v9 flow and stale lines now; README numbers last, from the prod
benchmark and gate 4.0)".

Read: `agent-rules.md` (phase-4 section: ownership map — you own `docs/**` EXCEPT
`docs/INFRASTRUCTURE.md` (4.1), plus `README.md` and service READMEs), `.claude/commands/update-docs.md`
or the update-docs skill (apply its pass), the code as it is on HEAD (the docs must describe the
code, not the plan): v9 flow = `services/summarizer/src/routes/pipeline_runner.py` /
`pipeline_orchestration.py`, `services/pipeline/phases/*` (text, split_extraction, group_emission,
frames_done, assembly…), `field_requests.py` / `field_groups.py`, `extraction_group_prompt.py`,
`reconcile*.py`, `pipeline_timing.py`; the gate reports `gates/g1.md`..`g3.md` (+ `g3.md` §Prod) for
what changed and why; `docs/CROSS-CUTTING.md` / `docs/DATA-MODELS.md` if `pipeline.*` fields
(`timing`, `reconcile`, `extraction._groups`, `droppedTabs`, `degraded`) or SSE events (`tab_ready`
`final`/`provisional`/`replaces`, new phases) are undocumented.

## Now
- `docs/summarizer-workflow.md`: the v9 call order (metadata ∥ description → transcript → tier probe
  → plan ∥ memory → reconcile → text groups (warm-first, staggered) ∥ frames → visual group at
  frames-done → per-group assembly + `tab_ready` → synthesis / moment fill / faithfulness → closing
  re-emit → translation), with file anchors — verify each against the code.
- `docs/SERVICE-SUMMARIZER.md`: the stale lines in the list (verify each; fix or delete), the new
  phases, `EXTRACTION_PARALLEL` semantics (the rollback lever's real scope — g3 Q7), chunked/long-video
  semantics (3.7).
- Any other doc the update-docs pass flags as stale for phases 0–3 (e.g. `docs/OBSERVABILITY.md` for
  `pipeline.timing` v3, `docs/llm-cost-model.md` for the three cache blocks, `docs/IDEMPOTENCY.md` for
  v9).
## Last (the coordinator messages you when ready)
- Model names (fast model on prod, vision model, per-stage models) — after 4.0 picks them.
- README numbers — from `gates/g3.md` §Prod and the gate-4.0 numbers.
- The eval "twice a month" sentence: the scheduled eval has NOT run yet (manual dispatch until the
  secrets land, D13/D20) → leave the sentence as is and report it as open.

Rules: describe the code on HEAD; never invent numbers (cite the gate file a number comes from);
no code edits. No LLM spend. Flow: part 1 → STOP uncommitted, report (files, each stale claim → fixed
how, open items) → review → fold → commit `p4.2 docs: v9 flow + stale summarizer docs` (stage by
path). Part 2 (model names + README numbers) → a second `p4.2` commit when the coordinator sends the
numbers.
