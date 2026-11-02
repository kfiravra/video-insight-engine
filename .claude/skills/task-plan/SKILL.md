---
name: task-plan
description: Plan a multi-day task through a grilling interview, then write dev/active/<task>/plan.md and notes.md.
argument-hint: "[task-name] [one-line description]"
disable-model-invocation: true
---

Adapted from mattpocock/skills `grilling` (MIT, a496f55). Task: $ARGUMENTS

## 1. Name it

The first word of the arguments is the task slug (kebab-case). If `dev/active/<slug>/` already
exists, stop and tell the user to run `/resume <slug>` instead.

## 2. Orient (facts are your job, never the user's)

- Read [PROJECT-BRIEFING-TLDR.md](../../../PROJECT-BRIEFING-TLDR.md) and the `docs/` rows in [CLAUDE.md](../../CLAUDE.md) that match the area.
- Call the Skill tool with the matching domain skill (`vie-api`, `vie-web`, `vie-python`) so its
  gotchas are in context.
- For anything else you need to know (current code paths, existing helpers, test layout), dispatch
  up to three Explore agents with `model: opus`. Do not block the interview on them: ask the
  questions that do not depend on their findings now.

## 3. Grill

Map the work as a design tree: every decision branches into the decisions that hang off it. Work
in rounds over the frontier (decisions whose prerequisites are settled). Each round:

- Ask through `AskUserQuestion`, at most four questions per round, every question with a
  recommended option listed first and marked "(Recommended)" and worded so "yes" accepts it.
- Decisions are the user's; facts you could look up are yours. Recompute the frontier after each
  round. Stop when the frontier is empty or the user says "enough".

Typical frontier: scope and non-goals, user-visible behaviour, data model or contract changes,
rollout and rollback, verification, what "done" means.

## 4. Write the two files

`dev/active/<slug>/plan.md`:

```
# <Task title>
Created: YYYY-MM-DD · Branch: <feature/...>

## Goal
One paragraph: what changes for whom, and why now.

## Decisions
- <decision>: <choice> (why; alternatives rejected)

## Phases
### Phase 1: <name>
- [ ] step
- [ ] step
Verify: <exact commands or `/check`>
Commit: `type(scope): ...` when green

### Phase 2: ...

## Risks
- <risk>: <mitigation or rollback>

## Out of scope
- ...
```

`dev/active/<slug>/notes.md`:

```
# <Task title> notes
## State
Phase 1 not started.
## Next
1. <first concrete action>
## Blockers
(none)
## Last reviewed
(none)
## Log
- YYYY-MM-DD created plan
```

Task folders are local (gitignored by convention) and never committed. Keep research, screenshots
and tool output in subfolders (`research/`, `screenshots/`) so `/resume` can skip them.

## 5. Finish

Print the phase list and the first command to run. Remind: each phase ends with `/check`, then
`/phase-review`, then one commit; `/handoff` before clearing the session.
