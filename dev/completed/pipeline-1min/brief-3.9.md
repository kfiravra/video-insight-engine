# Brief 3.9 — PIPELINE_VERSION v9 + prompts + demo re-run script — coordinator, 2026-10-13

Spec: tasks.md Phase 3 row 3.9 ("`PIPELINE_VERSION` → v9 (force-recreate summarizer/worker/api); prompts
re-registered; demo library re-run script (runs on Kfir's go)"); plan table 3.9 ("Script dry-runs; version visible on
new docs"). Design `designs/phase3-design.md` §3.9, §8 3.9 tests. Agent rules: `agent-rules.md` (Phase 3 first).
Kfir (phase-3 rules): the demo re-run script is WRITTEN, NOT RUN; prompts to DEV Langfuse only (prod staging waits for
the push word); no env change outside `env-changes.md`.

## Build
- `packages/shared/src/config/pipeline-version.json` v8 → v9 (single value read by api + summarizer — context gotcha:
  bind-mounted single file → the COORDINATOR force-recreates summarizer/worker/api; you don't touch containers).
  Find every reader/test that pins "v8" (`grep -rn '"v8"\|v8' --include=*.py --include=*.ts` in services/, api/,
  packages/, scripts/, apps/web/ — ignore fixtures that record history, e.g. replay cassettes) and update what must
  follow the version; `docs/IDEMPOTENCY.md` version line (+ any doc that states the current version). Read
  `docs/IDEMPOTENCY.md` §PIPELINE_VERSION bumps first (dedupKey rows, `?bypassCache=true`).
- Prompts: list every prompt file changed since `origin/feat/pipeline-1min` (`git diff --stat origin/feat/pipeline-1min
  -- services/summarizer/src/prompts`) and, for each, whether DEV Langfuse already serves the file's text
  (`scripts/register_prompts.py` dry run against DEV — find how phase 2 did DEV-only registration; NEVER prod). Register
  to DEV only what differs. Report the prod staging list for the push word (`--label pipeline-1min`).
- New `scripts/demo_rerun.py` (+ unit test): reads a `demo-export.sh` `urls.txt` (or the v8 completed docs of the demo
  user/library — read `scripts/demo-export.sh` and the demo tooling first; CODE-MAP-eval-web-registry.md has notes),
  DRY-RUN by default (prints count × ≈ $0.15–0.33 per video and the list), `--apply` submits each via the eval API
  helper (`scripts/_eval_api*.py`) with `bypassCache`, consumes the SSE (the stream DRIVES the pipeline), concurrency 1,
  stops on the first failure unless `--keep-going`; never prints secrets. Do NOT run `--apply`.
- Tests (design §8 3.9): config reads v9 · demo_rerun dry run lists videos + cost without submitting · register_prompts
  sync test covers `extraction_group_job`.

## Flow
implement + tests → precommit (python + TS where touched) → full summarizer + api + web suites under the heavy lock
(sequential; web via `npx vitest run`) + `tsc` where TS changed → STOP uncommitted → review → fold → commit
`p3.9 chore(pipeline): …`.
