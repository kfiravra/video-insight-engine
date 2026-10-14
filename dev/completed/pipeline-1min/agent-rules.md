# pipeline-1min — rules for implementation subagents (phases 1–4)

You are one of several Opus subagents working IN PARALLEL in the SAME git working tree
(`/home/kfir/projects/video-insight-engine`, branch `feat/pipeline-1min`). Kfir watches the
changes live in his IDE. Other agents are editing other files right now.

## Phase 4 (Kfir 2026-10-13) — read this section first; the phase-3 and phase-2 rules below still apply
- Spec: tasks.md Phase 4 rows (incl. the "Checked against the code 2026-10-13" facts under 4.0),
  plan.md §Phase 4, context.md D29 and the "4.0c docs facts" block (once recorded). Your brief
  (`brief-4.*.md`) is binding together with them.
- Order (Kfir 2026-10-13, second message — supersedes "4.0 alone"): 4.0a (LiteLLM bump) lands
  FIRST, alone, as its own commit. Then IN PARALLEL on disjoint files: 4.0 b–e ∥ 4.1 sweep ∥ 4.2 docs
  ∥ 4.3 report skeleton. Measurement runs (the A/B, the full golden, the dev cold runs ×4) run ALONE
  — no agent working, nothing else executing (the dev containers bind-mount this tree and dev reads
  prompts from disk: an edit during a measurement run changes what is measured). ONE commit per task
  id, prefix `p4.0a`, `p4.0b`, `p4.0d`, `p4.0e`, `p4.1`, `p4.2`, `p4.3` …
- **File ownership in the parallel stage** (a file has ONE owner; a needed change in a file you don't
  own goes into your report, the coordinator routes it):
  - **4.0 b–e:** LLM plumbing (`services/summarizer/src/services/llm*.py`, `utils/llm_retry.py`,
    `services/pipeline/pipeline_timing.py`), `src/config.py` (+ its test), model settings incl. the
    model env lines AND every other edit in `docker-compose.yml` / `docker-compose.prod.yml`,
    `dev/completed/pipeline-1min/env-changes.md`, the replay identity test, the A/B script(s), and
    their tests.
  - **Web full suite = `cd apps/web && npx vitest run`** (`npm test` starts watch mode and holds the heavy lock).
  - **4.1 sweep:** the leftover code/tests in its list (pipeline modules, prompts, scripts) — never
    the 4.0 files above (config / compose removals → report them); `.env.example`,
    `.env.production.example`, `docs/INFRASTRUCTURE.md` (model names only after 4.0 picks them).
  - **4.2 docs:** `docs/**` except `docs/INFRASTRUCTURE.md`, `README.md`, service READMEs; model
    names and README numbers LAST (after the gate-4.0 numbers).
  - **4.3 report:** `dev/completed/pipeline-1min/gates/g4.md` (skeleton; the coordinator fills gate 4.0
    and the 4.0 section).
- Flow per task: implement + tests + targeted runs → STOP and report WITHOUT committing → the
  coordinator runs a review → you fold the findings in (SendMessage) → full suites under the lock →
  you commit (stage by path, `--no-verify` after the hook's commands by hand) → report the hash.
- **Heavy lock path changed (2026-10-13, after the WSL crash):** `flock /tmp/vie-heavy.lock -c '<command>'` (`$VIE_HEAVY_LOCK` overrides); run wrappers live in `dev/completed/pipeline-1min/tools/`
  (use this path wherever the sections below name the phase-3 one).
- Something outside the phase-4 tasks → a QUESTION for `gates/g4.md` in your report; never code.
- `EXTRACTION_PARALLEL=false` stays today's single call: prompt bytes byte-identical (the identity
  test; from 4.0b on it keys on the prompt bytes — messages + `cache_control` — not the model id).
- Prompts: DEV Langfuse only, and only when your brief says so; never prod. No env change outside
  `env-changes.md`; the dev `.env` belongs to the coordinator. LLM spend only as your brief allows —
  state it in your report.

## Phase 3 (Kfir 2026-10-12) — read this section first; the phase-2 rules below still apply
- Design: `dev/completed/pipeline-1min/designs/phase3-design.md` is binding together with your brief.
  Phase-3 spec: tasks.md Phase 3 rows (incl. Kfir's sub-items), plan.md §Phase 3, brief.md §Phase 3
  + Appendices A–C.
- Flow per task: implement + tests + targeted runs → STOP and report WITHOUT committing → the
  coordinator runs a review → you fold the findings in (SendMessage) → full suites under the lock →
  you commit (`p3.N <type>(<scope>): …`, stage by path) → report the hash.
- **Heavy lock path changed:** `flock /tmp/claude-1000/-home-kfir-projects-video-insight-engine/4fff99d5-46a4-4e30-ad94-ea5a5baecc9b/scratchpad/locks/heavy.lock -c '<command>'`
  (the same path is used below).
- `EXTRACTION_PARALLEL=false` must keep TODAY's single extraction call byte-identical (replay test
  against the baseline recorded on 41501dd). Never edit that path's prompt bytes.
- Something you find outside the nine phase-3 tasks → put it in your report as a question for
  `gates/g3.md`; do not code it.
- Prompts: register to DEV Langfuse only when your brief says so; never prod. No env change outside
  `dev/completed/pipeline-1min/env-changes.md` (prod `.env` is never edited — safe defaults).

## Phase 2 — git (Kfir 2026-10-12; supersedes the phase-1 "coordinator commits" rule)
- You COMMIT YOUR OWN TASK, ONCE, when it is done and green: one commit per task id.
- Stage ONLY your own files, by path. New files: `git add -- <new paths>`. Then commit with
  explicit paths so nothing another agent staged can ride along:
  `git commit --no-verify -F <msgfile> -- <every path of yours that changed>`.
  NEVER `git add -A`, `git add .`, `git add -u`, `git commit -a`, or a commit without paths.
- `--no-verify` because the pre-commit hook (lint-staged) stashes and re-applies unstaged
  changes, which would hide or clobber other agents' in-flight edits. Run what the hook runs BY
  HAND first: `bash scripts/precommit-python.sh <your .py files>` (print guard + ruff format +
  ruff check --fix), from the repo root. TS files run what each package's lint-staged runs:
  api `cd api && bash ../scripts/precommit-no-debug.sh <src files> && npx eslint --fix <src files>`;
  web `cd apps/web && npx eslint --fix <src files>` (packages/types has no hook — `tsc` only).
- NEVER stage or commit `CLAUDE.md`, `.claude/**` (Kfir's uncommitted edits) or
  `dev/active/**` (the coordinator's task docs), unless your brief names a file there.
- `.git/index.lock exists` → another agent is mid-git: wait 5 s and retry (max 12 times). Never
  delete the lock file.
- Forbidden: `stash`, `checkout`/`restore` of files, `reset`, `clean`, `rebase`, `merge`,
  `push`, `--amend`, branch ops, worktrees. Read-only git (`status`, `diff`, `log`, `show`) is
  fine. If a commit went wrong, STOP and report — don't repair history.
- Commit message: `p2.N <type>(<scope>): <what>` (conventional, imperative) + a short body:
  what changed and why, "output-changing: yes/no (what)", tests. NO AI/Claude credit lines and
  NO `Co-Authored-By` trailer — this overrides any default you have.

## Files
- Edit ONLY the files your brief says you own, new files you create in the areas it names, and
  their tests. If you need a change in a file you don't own, don't edit it: put it in your report
  (file, what, why) — its owner may be editing it now; the coordinator applies it later.
- The formatter hook strips unused imports mid-edit → add an import and its first use in ONE edit.

## Shared machine (WSL, 7.7 GiB, swap full — an OOM froze Docker on 2026-10-08)
- HEAVY commands run under the lock, one at a time across all agents:
  `flock /tmp/claude-1000/-home-kfir-projects-video-insight-engine/4fff99d5-46a4-4e30-ad94-ea5a5baecc9b/scratchpad/locks/heavy.lock -c '<command>'`
  Heavy = any full suite (summarizer incl. replay, api, web), the replay suite, pyright on more
  than ~10 files, `tsc -b` / `tsc --noEmit` on a package, eslint on a package, vitest full runs.
  Targeted pytest/vitest on a few files runs without the lock.
- A full suite can wait minutes for the lock: use the Bash tool's max timeout (600000 ms) or run
  it in the background and check its output file; never kill another agent's process.
- The dev stack (containers, worker restarts, pipeline runs, writes to dev Mongo/Redis/Qdrant)
  belongs to the coordinator. Don't touch it unless your brief says so.
- No LLM spend, no prod access, no Langfuse writes unless your brief says so. Never print secrets
  (`.env` values, keys, tokens).

## Before writing code
1. Read `dev/completed/pipeline-1min/pipeline-1min-context.md` (decisions D1–D25 are binding), your
   task rows in `pipeline-1min-tasks.md` + `pipeline-1min-plan.md` (Phase 2), and
   `pipeline-1min-brief.md` §Phase 2.
2. Read the skill for the code you touch BEFORE coding: `.claude/skills/backend-python/SKILL.md`
   (summarizer), `.claude/skills/backend-node/SKILL.md` (api), `.claude/skills/react-vite/SKILL.md`
   (web), and the resource files they point to that match your task. Read 1–2 neighbouring files
   and match their style.
3. Line numbers in older docs are stale — re-verify everything in the code.

## Code rules (project, required)
Files < 500 lines; functions < 50 lines; type hints everywhere; Pydantic for validation; no dead
code, no commented-out code, no print/console.log, no TODO without a tracking id; comments say
WHY; no `any` in TS; no empty `except`/`catch`. Removal happens in the task that replaces the
thing, together with its tests. Output contract unchanged unless your task changes it.

## Settings (only if your brief makes you the settings owner)
A touched setting goes through all 8 touch points: `services/summarizer/src/config.py` (+ its
test), `docker-compose.yml` `x-summarizer-env`, `docker-compose.prod.yml` anchor, `.env.example`,
`.env.production.example`, `docs/INFRASTRUCTURE.md` tuning table, `docs/SERVICE-SUMMARIZER.md` env
block, the code reader + its tests. Bool passthroughs need literal defaults (`${X:-true}`). Prod
`.env` is NOT edited (D20): every change must work with today's prod `.env` (safe defaults).

## Tests and checks
- Write the tests the acceptance criteria name (behaviour, not implementation; "should … when …").
- While working: targeted runs. Summarizer `cd services/summarizer && .venv/bin/python -m pytest
  <paths> -q`; api `cd api && npx vitest run <paths>`; web `cd apps/web && npx vitest run <paths>`.
- BEFORE YOUR COMMIT (under the heavy lock): the full suite of every service you touched —
  summarizer `cd services/summarizer && .venv/bin/python -m pytest -q` (includes `tests/replay`);
  api `cd api && npm test`; web `cd apps/web && npm test`; pyright as CI does on your changed
  summarizer files (`cd services/summarizer && npx pyright@1.1.407 <files>`); `npx tsc -b` in
  apps/web and `npx tsc --noEmit` in api / packages/types when TS changed.
- A failure in a file you don't own that your change can't cause = another agent's in-flight
  work: note it, re-run later or report it; never edit their file to make it pass.

## When you stop
Report, concisely:
1. Task id; commit hash; files (new/modified/deleted, repo-relative).
2. Acceptance criteria → how each is met (test names).
3. Suites/lint/pyright/tsc run before the commit and their results (counts).
4. Deviations from the brief/plan and why; OUTPUT-CHANGING yes/no and what (model input,
   emitted events, stored docs).
5. Changes needed in files you don't own; env changes for Kfir; open questions/decisions.
