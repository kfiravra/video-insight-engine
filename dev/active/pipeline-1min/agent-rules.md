# pipeline-1min — rules for implementation subagents (phase 1)

You are one of several Opus subagents working IN PARALLEL in the SAME git working tree
(`/home/kfir/projects/video-insight-engine`, branch `feat/pipeline-1min`). Kfir watches the
changes live in his IDE. A coordinator (the parent session) commits your work.

## Git and files
- NEVER run a git command that changes anything: no `commit`, `add`, `stash`, `checkout`,
  `reset`, `restore`, `clean`, `rebase`, `merge`, `push`, branch ops. Read-only git is fine
  (`status`, `diff`, `log`, `show`). No worktrees.
- NEVER touch `CLAUDE.md`, `.claude/**`, or `dev/active/**` task docs (the coordinator owns them),
  unless your brief says so.
- Edit ONLY the files your brief says you own (plus new files you create in the areas it names, and
  their tests). If you need a change in a file you don't own, STOP and say so in your report
  (what, where, why) — another agent may be editing it right now.
- The formatter hook strips unused imports mid-edit → add an import and its first use in ONE edit.
- Python changes run in `vie-summarizer-worker` only after a restart; don't restart containers
  unless your brief says so.

## Before writing code
1. Read `dev/active/pipeline-1min/pipeline-1min-context.md` (decisions D1–D25 are binding), your
   rows in `pipeline-1min-plan.md` (Phase 1 tables + conflicts C1–C21) and the brief section
   (`pipeline-1min-brief.md` §Phase 1 + Appendices A–D).
2. Read the anchors for your task in `dev/active/pipeline-1min/evidence/CODE-MAP-orchestration.md`
   (or `CODE-MAP-eval-web-registry.md`). Line numbers are from before phase 0. Re-verify them.
3. Read the skill for the code you touch: `.claude/skills/backend-python/SKILL.md` (summarizer),
   `.claude/skills/backend-node/SKILL.md` (api), `.claude/skills/react-vite/SKILL.md` (web), and
   the resource files they point to that match your task. Read 1–2 neighbouring files and match
   their style.

## Code rules (project, required)
Files < 500 lines; functions < 50 lines; type hints everywhere; Pydantic for validation; no dead
code, no commented-out code, no print/console.log, no TODO without a tracking id; comments say
WHY; no `any` in TS; no empty `except`/`catch`. Removal happens in the task that replaces the
thing, together with its tests. "Output contract unchanged" (brief principle 2): extraction stays
domain-keyed JSON validated by the same Pydantic models, `tabs`/`meta` shape unchanged.

## Settings (only if your brief makes you the settings owner)
A touched setting goes through all 8 touch points: `services/summarizer/src/config.py` (+ its
test), `docker-compose.yml` `x-summarizer-env`, `docker-compose.prod.yml` anchor, `.env.example`,
`.env.production.example`, `docs/INFRASTRUCTURE.md` tuning table, `docs/SERVICE-SUMMARIZER.md` env
block, the code reader + its tests. Bool passthroughs need literal defaults (`${X:-true}`). Prod
`.env` is NOT edited (D20): every change must work with today's prod `.env` (safe defaults);
anything Kfir must set goes in your report for `env-changes.md`.

## Tests and checks
- Write the tests the acceptance criteria name (behaviour, not implementation; "should … when …").
- Run TARGETED tests only — never a whole suite (the coordinator runs suites, never in parallel):
  summarizer `cd services/summarizer && .venv/bin/python -m pytest <paths> -q`;
  api `cd api && npx vitest run <paths>`; web `cd apps/web && npx vitest run <paths>`.
- Lint touched Python: `services/summarizer/.venv/bin/ruff check --config ruff.toml <files>` and
  `.venv/bin/ruff format <files>` (run from the repo root for ruff.toml); pyright as CI does:
  `cd services/summarizer && npx pyright@1.1.407 <files>`. Web typecheck = `npx tsc -b` (not
  `--noEmit`).
- No LLM spend, no dev pipeline runs, no prod access unless your brief says so. No secrets in any
  output.

## When you stop (each stop point in your brief, and at the end)
Report, concisely:
1. Task id(s) done; files changed (exact repo-relative paths, new/modified/deleted).
2. Acceptance criteria → how each is met (test names).
3. Targeted tests + lint run and their result.
4. Deviations from brief/plan and why; anything that changes model output (say so explicitly).
5. Needs from files you don't own; env changes for Kfir; open questions.
6. A suggested commit message: `p1a.3 fix(summarizer): <what>` — conventional, imperative, NO
   AI/Claude credit lines or trailers.
