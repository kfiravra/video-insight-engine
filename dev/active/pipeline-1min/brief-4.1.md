# Brief 4.1 — sweep leftovers (parallel with 4.0 b–e, 4.2, 4.3)

Task row (tasks.md 4.1 + plan.md §Phase 4 4.1): "Sweep leftovers: `inject_visual_context`, retry
paths, chapter_detect on the single path, flashcards/scenarios, fast-first,
`FRAME_TIER_EARLY_CLASSIFIER`, `to_video_context_full`/`video_dna_text`, `outboundLinks`/`itemCounts`
readers, plan cache_control, old enrich files, `category_confidence`; `.env.example`/
`.env.production.example` (with 4.0's model names); `docs/INFRASTRUCTURE.md` flags table (document
what is passed through)." AC: grep-tests for removed symbols; compose config validates.
Kfir 2026-10-13: "4.1 sweep (everything that doesn't touch LLM plumbing or config; the env example
files wait for 4.0's chosen models)". Read `agent-rules.md` (phase-4 section: ownership map), the
phase-1 deferral list at the end of `gates/g1.md`, and `gates/g3.md` §Questions Q-docs (domains.json
`requirementEvidenceNote`, `chapter_video_info` shared helper → "4.1 sweep").

## Rules for this task
- **Not yours (report the edit, don't make it):** LLM plumbing (`services/llm*.py`,
  `utils/llm_retry.py`, `pipeline_timing.py`), `src/config.py`, `docker-compose*.yml` — 4.0 b–e owns
  them now. A dead setting's removal = list it (config line, compose anchor lines, its test) in your
  report; the coordinator applies it with 4.0's commit or after.
- Verify each item against the code FIRST — phases 1–3 removed several (e.g. context.md "Settings":
  dead settings were to be deleted in phase 1). Output a table: item → state found (gone / leftover
  at file:line / still used by X) → action. Remove only true leftovers (no live reader), with their
  tests, in the task that replaces the thing (rule) — here the sweep. "plan cache_control": read
  D26(2) first (the plan cache stays 1h, no switching code) — only dead/duplicate cache_control code
  goes. "retry paths" = the extraction/synthesis retry paths the split made dead, not
  `utils/llm_retry.py`.
- `EXTRACTION_PARALLEL=false` byte-identity: if a removal could change today's single-call request
  bytes, run `$S/tools/capture.py` (both modes) vs `$S/p40a/after` and keep it identical — or don't
  remove it and ask.
- Grep-tests for removed symbols: extend the existing guard test if there is one (search tests for a
  removed-symbol / dead-code grep test), else add one small test file.
- Env example files: remove dead keys and document passthroughs NOW; model-name lines LAST — the
  coordinator messages you the 4.0 picks; until then leave model lines as they are.
- `docs/INFRASTRUCTURE.md` flags table: every setting passed through the compose anchors
  (`x-summarizer-env` + prod anchor) documented (name, default, what it does) — read the compose files
  (don't edit them). `docker compose -f docker-compose.yml config -q` and the same with the prod file
  → must validate (read-only commands).
- Prompts: if a prompt file changes, DEV Langfuse registration only (`register_prompts.py --commit`
  per agent-rules) and say so; never prod.

Scratch: `S=/tmp/claude-1000/-home-kfir-projects-video-insight-engine/af60d8e4-09b5-430c-92c1-3a4b950e7ba0/scratchpad`.
Flow: verify table → implement → targeted tests → STOP uncommitted, report (agent-rules "When you
stop" + the table + the edits you need in 4.0-owned files) → review → fold → full summarizer suite
(+ api/web only if touched) under the heavy lock → commit `p4.1 refactor(…): sweep phase-1–3
leftovers` (stage by path). Part 2 later (env example model names + any INFRASTRUCTURE model rows) =
a second `p4.1` commit when the coordinator sends the picks.
No LLM spend. No dev-stack commands. Don't run anything heavy while the coordinator announces a
measurement run (A/B, golden, dev runs) — finish or pause at a safe point when told.
