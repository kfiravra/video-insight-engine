---
name: handoff
description: "Save the task state before clearing or compacting: update dev/active/<task>/notes.md, write durable learnings to auto-memory, print a short handoff."
argument-hint: "[what the next session will focus on]"
disable-model-invocation: true
---

Adapted from mattpocock/skills `handoff` (MIT, a496f55). Focus for the next session: $ARGUMENTS

Replaces the old `/task-plan-update` and the session-snapshot hook. Three outputs, in order:

## 1. notes.md

Find the task folder this session worked in (`dev/active/<task>/`; ask if ambiguous). Update
`notes.md` in place:

- **State**: one or two sentences on where the work is, including anything half-done.
- **Next**: the first three concrete actions, each one line, each runnable without this
  conversation.
- **Blockers**: what is waiting on the user or an external fact, or `(none)`.
- **Last reviewed**: the commit SHA of the last `/phase-review`, if one ran.
- **Log**: append `- YYYY-MM-DD <what landed, commit SHAs, decisions made>`.

Tick finished steps in `plan.md`. Do not copy diffs, test output or plan text into the notes;
reference files, commits and docs by path or SHA. Legacy three-file tasks: update
`<task>-context.md` and `<task>-tasks.md` the same way.

## 2. Auto-memory

For each learning that outlives this task (a gotcha, a user preference, a decision not derivable
from the code), write a memory file following the memory instructions in effect, and add its index
line. Skip anything the repo, the docs or the notes already record.

## 3. Handoff message

Print at most ten lines: task, branch and HEAD, state, the three next actions, blockers, and the
skills the next session should start with (usually `/resume <task>`, then the matching domain
skill loads on its own). Redact secrets if any appeared in the session.
