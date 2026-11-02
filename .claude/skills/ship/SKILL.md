---
name: ship
description: "Pre-PR gate for the current branch: full checks, secrets and docs drift, PR body, task archive. Never pushes or opens the PR itself."
argument-hint: "[task-name]"
disable-model-invocation: true
---

Replaces the old `/complete-task`, `/update-docs`, `/security-check` and `/test`. Task: $ARGUMENTS

## 1. Checks

Call the Skill tool with `check` and the argument `all --tests`. Stop here if anything is red.
If `.claude/` changed on this branch, `node --test .claude/hooks/tests/*.test.mjs` is part of it.

## 2. Secrets

`git diff main...HEAD` plus the working tree, grep for `ghp_`, `github_pat_`, `figd_`, `sk-`,
`eyJhbGciOi`, `AKIA`, `mongodb+srv://`, and any `.env` content. A hit stops the gate.

## 3. Docs drift

Map what changed to what must be updated, and propose the edits (apply them when they are
mechanical):

| Change | Update |
|---|---|
| Routes, envelopes, status codes | [docs/API-REFERENCE.md](../../../docs/API-REFERENCE.md), [docs/SERVICE-API.md](../../../docs/SERVICE-API.md) |
| Collections, schema fields | [docs/DATA-MODELS.md](../../../docs/DATA-MODELS.md) |
| Auth, rate limits, CORS | [docs/SECURITY.md](../../../docs/SECURITY.md) |
| Error codes, retry, DLQ | [docs/ERROR-HANDLING.md](../../../docs/ERROR-HANDLING.md) |
| Pipeline stages, prompts, models | [docs/summarizer-workflow.md](../../../docs/summarizer-workflow.md), [docs/SERVICE-SUMMARIZER.md](../../../docs/SERVICE-SUMMARIZER.md), [docs/OBSERVABILITY.md](../../../docs/OBSERVABILITY.md) |
| Compose, env vars, ports, deploy | [docs/INFRASTRUCTURE.md](../../../docs/INFRASTRUCTURE.md), [docs/DEPLOY.md](../../../docs/DEPLOY.md), `.env.example` |
| Frontend structure or patterns | [docs/FRONTEND.md](../../../docs/FRONTEND.md) |
| New gotcha the code cannot tell you | [.claude/CLAUDE.md](../../CLAUDE.md) gotchas, or the domain skill |
| Commands, repo layout | `README.md` |

## 4. PR body

Draft it from the commits and the plan, in this shape, with no attribution line:

```
## What
## Why
## How to test
## Risks and rollback
```

## 5. Archive the task

If `dev/active/<task>/` exists and every phase is ticked, offer to move it to `dev/completed/<task>/`
and append `ARCHIVED <date>: <reason>` to `notes.md`. Task folders are local; this is a filesystem
move, not a commit.

## 6. Hand back

State plainly: checks green, secrets clean, docs updated or listed, PR body ready. Pushing the
branch and opening the PR need an explicit verb from the user ("push", "open the PR"); do not do
either here.
