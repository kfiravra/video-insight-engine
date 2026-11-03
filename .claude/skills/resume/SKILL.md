---
name: resume
description: Resume a task from dev/active/ after a clear, compaction or crash, or list the active tasks.
argument-hint: "[task-name]"
disable-model-invocation: true
---

Active task folders:

!`ls -1 dev/active 2>/dev/null || true`

Task argument: $ARGUMENTS

## No argument: list

For each folder above, print one row: name, last modified (`ls -la`), and the first line under
`## Next` in `notes.md` (or the `Last Updated` line of `<task>-context.md` for the legacy layout).
Then ask which one to resume. Do not read anything else.

## With an argument: resume `<task>`

If `dev/active/<task>/` is missing, list the folders and suggest `/task-plan <task>`.

Read, in this order, and nothing else:

1. `notes.md` (State, Next, Blockers, Last reviewed, Log). Legacy layout: `<task>-context.md`.
2. The phase in `plan.md` that `notes.md` names as current, plus the Decisions section. Legacy
   layout: the unchecked items of `<task>-tasks.md` and the matching section of `<task>-plan.md`.
3. `git status --short` and `git log --oneline -5` to see where the branch actually is.

Never read `research/`, `screenshots/`, `gates/`, `tools/` or snapshot JSON files; reference them
by path if the notes point there. Keep the resume under about 6k tokens of reads.

## Output

```
## Resuming: <task>
State: <one line from notes.md>
Branch: <branch> (<n> commits since main, <m> dirty files)
Phase: <current phase> (<done>/<total> steps)

Next:
1. ...
2. ...
3. ...

Blockers: <or none>
```

Then ask the user what to do next and end the turn. Do not start any Next step, run a command
that changes state (branch switch, pull, install, build), or edit a file in the same turn; work
begins on the user's next message. If the notes look stale against `git log` (commits after the
last Log entry), say so in the report and offer to reconcile them.
