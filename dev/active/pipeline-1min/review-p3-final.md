# Phase-3 final review — everything since `origin/feat/pipeline-1min` as ONE unit (R40, Kfir 2026-10-12, like R10)

READ-ONLY (no edits, no git mutations, no pipeline runs, no LLM spend, no containers). Targeted pytest on a few files
allowed; nothing heavy. Recipe + output format: `review-p3.md` (and `.claude/commands/review.md`). Range:
`git log --oneline origin/feat/pipeline-1min..HEAD` (phase-3 commits p3.1–p3.9 + fixes; 23cf977 docs; 41501dd merge).
Context: `designs/phase3-design.md` (§9 decisions binding), `pipeline-1min-tasks.md` Phase 3 rows, brief §Phase 3 +
Appendices A–C, `gates/g3-notes.md` (known deviations/questions — don't re-report them unless the code contradicts
them), `gates/g3-review-findings.md` (per-task findings already folded). Per-task reviews looked at one task at a time;
YOUR job is how the pieces interact.

## Lens A — data flow + contracts (end to end)
plan → plan-parse guard → plan-done reconcile (strict doubly-false, deferral) → `video_memory` → requested fields
(active domains only, side reads, overview) → groups → three-block prompts → per-group validate/filter → merge →
per-group emission (non-final) → final assembly (fallbacks, backfill, frames) → late group (moment fill, synthesis,
quiz task) → closing re-emit (finals once, overview last) → `complete.tabIds` → persistence (`pipeline.reconcile`,
`droppedTabs`, `pipeline.extraction._groups`, `pipeline.timing` v3, `degraded`) → API relay / web reducer / admin /
eval readers. Long videos (chunks × groups) and failure paths through the same chain. Things a single task could not
see: a field requested but never validated (or the reverse); a tab emitted per group then silently missing at the end;
evidence views disagreeing between reconcile / quiz / assembly / video_memory; positions; quiz input vs final tabs;
frames-done changes (fresh meta, re-fill) vs emitted tabs; empty `id` in `droppedTabs` (seen on dev T1dQ —
`requirement_evidence_false`); prompts vs schema keys (emit-only vs `keep_requested` vs Pydantic aliases).

## Lens B — concurrency, failure, cost, caching, operations
warm-first streaming (+ the arrival-order fix 6988803), semaphore across chunks × groups, frames-done / plan-done events
and every early return, cancellation + background tasks (quiz task, description, caption), retries (call retry + the
one unstreamed extra call), timeouts (120 s groups), error → assembly-still-runs path, cost per run (extra calls,
side reads, overview reads, cache writes: block 1 1h / block 2 5m — identical bytes across groups and chunks; 1h/5m
usage split recorded), ledger tags per group (`summarize:extraction:{gid}`), prod safety: today's prod `.env` (no
`EXTRACTION_PARALLEL` → true), PROMPT_SOURCE registry on prod (`extraction_group_job` must be staged before the deploy
— check what happens if prod lacks it), `pipeline-version.json` v9 consequences, the rollback lever
`EXTRACTION_PARALLEL=false` (identity test), files < 500 / functions < 50 across the phase.

OUTPUT (≤ 70 lines): findings in the recipe format, Critical first, max 15 Warnings, then `g3 question:` lines, then
`files reviewed: N; findings: C=<n> W=<n>`.
