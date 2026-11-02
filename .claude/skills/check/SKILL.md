---
name: check
description: Run this repo's typecheck, lint and tests for the packages touched in the working tree, or for everything with `all`. Use before a commit, at the end of a phase, or when asked to verify the work. This is the repo-specific runner, not the bundled /verify.
argument-hint: "[all] [--tests] [path ...]"
allowed-tools: Bash(bash .claude/hooks/lib/pkgmap.sh *), Bash(pnpm *), Bash(npx *), Bash(node --test *), Bash(*/.venv/bin/ruff *), Bash(*/.venv/bin/python -m pytest *)
---

Changed files in the working tree:

!`git status --porcelain 2>/dev/null | awk '{print $NF}' | head -200 || true`

Arguments: $ARGUMENTS

## Steps

1. Scope. `all` → every package. Explicit paths → those. Otherwise the changed files above. Add
   `--tests` when the user asked for tests or the phase is ending; typecheck and lint alone are the
   default because the three test suites take minutes.
2. Get the command list, one check per line (`label`, `cwd`, `command`):
   `bash .claude/hooks/lib/pkgmap.sh [--all] [--tests] <paths...>`
   The map builds `@vie/types` and `@vie/shared` before api and web when they changed, uses
   `tsc -b` for web, resolves ruff from the summarizer venv, and emits `skip` lines with a reason
   when a tool is missing.
3. Run the checks one at a time from their `cwd`, in the order given. Never run the api, web and
   python suites in parallel. Stop early only on a build failure of a shared package.
4. Report failures only: label, then the error lines with `file:line`. If everything passed, one
   line: `check: <n> checks green`. Mention skipped checks with their reason.
5. Fix what you broke, then re-run only the failed labels. Do not re-run green checks.

The Stop hook runs the same map on the files you edited this session, so a green `/check` means a
silent stop.
