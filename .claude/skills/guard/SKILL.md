---
name: guard
description: Stand the git working-tree guard down or bring it back for this session. `/guard off` lets stash, reset --hard, checkout --, clean, restore and force-push run; `/guard on` restores the blocks; `/guard` shows the state.
argument-hint: "[on|off]"
disable-model-invocation: true
allowed-tools: Bash(bash .claude/hooks/guard-ctl.sh *)
---

Run `bash .claude/hooks/guard-ctl.sh $ARGUMENTS` (before the swap: `.claude2/hooks/guard-ctl.sh`)
and repeat its one line of output. Nothing else.

What it changes: `off` writes `state/guard-off`; while that file exists `git-guard.sh` neither
blocks nor asks, SessionStart prints `git guard: OFF`, and after a day the file is removed on the
next session start. `on` removes the file. The `permissions.ask` rules in `settings.json` still
prompt for push, PR and container commands; those are harness-side and this skill does not touch
them. The single-use `state/git-override` file is unaffected.

Only the user runs this skill. If a blocked command is needed and the user has approved it in the
current turn, prefer the single-use override the block message names; `/guard off` is for the
user's own sessions of deliberate history surgery.
