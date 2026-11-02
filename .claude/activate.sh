#!/usr/bin/env bash
# Swap the Claude Code infrastructure between the old `.claude/` and this folder.
#   .claude2/activate.sh [--dry-run] [--force]   swap in:  .claude -> .claude1, .claude2 -> .claude
#   .claude/activate.sh --revert [--dry-run]     swap back
# Run from a plain shell, not inside a Claude Code session: settings and hooks
# hot-reload, so the window between the two renames would have no guard.
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[[ -n "$ROOT" ]] || { echo "activate: not inside a git repository" >&2; exit 1; }
cd "$ROOT"

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SELF_NAME="$(basename "$SELF_DIR")"
MODE=forward
DRY=0
FORCE=0
for a in "$@"; do
  case "$a" in
    --revert) MODE=revert ;;
    --dry-run) DRY=1 ;;
    --force) FORCE=1 ;;
    -h|--help) sed -n '2,6p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "activate: unknown option $a" >&2; exit 1 ;;
  esac
done

EXCLUDE="$ROOT/.git/info/exclude"
MARK_BEGIN="# vie-infra-swap begin"
MARK_END="# vie-infra-swap end"

say() { printf '%s\n' "$*"; }
run() {
  say "  \$ $*"
  if [[ $DRY -eq 0 ]]; then "$@"; fi
}

guard_session() {
  if [[ $FORCE -eq 0 && -n "${CLAUDECODE:-}" ]]; then
    echo "activate: refusing to run inside a Claude Code session (hooks hot-reload mid-swap). Use a plain shell or --force." >&2
    exit 1
  fi
}

add_exclude() {
  say "  + $EXCLUDE: /.claude1/"
  if [[ $DRY -eq 0 ]]; then
    mkdir -p "$(dirname "$EXCLUDE")"
    touch "$EXCLUDE"
    grep -qF "$MARK_BEGIN" "$EXCLUDE" || printf '%s\n/.claude1/\n%s\n' "$MARK_BEGIN" "$MARK_END" >> "$EXCLUDE"
  fi
}

remove_exclude() {
  say "  - $EXCLUDE: /.claude1/"
  if [[ $DRY -eq 0 && -f "$EXCLUDE" ]]; then
    sed -i "/^$MARK_BEGIN\$/,/^$MARK_END\$/d" "$EXCLUDE"
  fi
}

forward() {
  [[ "$SELF_NAME" == ".claude2" ]] || { echo "activate: the forward swap must be run as .claude2/activate.sh" >&2; exit 1; }
  [[ -d .claude2 ]] || { echo "activate: .claude2 is missing" >&2; exit 1; }
  [[ ! -e .claude1 ]] || { echo "activate: .claude1 already exists; is the swap already applied?" >&2; exit 1; }
  [[ -f .claude/settings.json ]] || { echo "activate: .claude/settings.json missing; nothing to swap out" >&2; exit 1; }
  if [[ $FORCE -eq 0 && -d .claude/worktrees && -n "$(ls -A .claude/worktrees 2>/dev/null)" ]]; then
    echo "activate: .claude/worktrees is not empty; finish or remove those worktrees first (or --force)" >&2
    exit 1
  fi
  # Decided before the renames: after them these names point at the other tree
  # (first version tested after the mv and silently skipped the copy).
  copy_local=0
  [[ -f .claude/settings.local.json && ! -f .claude2/settings.local.json ]] && copy_local=1
  say "Plan (swap in):"
  run mv .claude .claude1 || { echo "activate: could not rename .claude; nothing changed" >&2; exit 1; }
  if ! run mv .claude2 .claude; then
    echo "activate: could not rename .claude2; putting .claude back" >&2
    mv .claude1 .claude
    exit 1
  fi
  if [[ -f .mcp.json ]]; then run cp .mcp.json .claude1/mcp.root.json; fi
  run cp .claude/mcp.json .mcp.json
  if [[ $copy_local -eq 1 ]]; then
    run cp .claude1/settings.local.json .claude/settings.local.json
  fi
  add_exclude
  if [[ $DRY -eq 1 ]]; then
    say "(dry run; nothing changed)"
    return
  fi
  say "Hook tests:"
  node --test .claude/hooks/tests/*.test.mjs
  say ""
  say "Done. Restart Claude Code, then check: /status (project settings), /context (only .claude/CLAUDE.md under Memory files), /mcp, /doctor."
  say "git status shows the tracked .claude/** files as changed while swapped. The git guard refuses 'git add -A' until you revert or adopt."
}

revert() {
  [[ "$SELF_NAME" == ".claude" ]] || { echo "activate: --revert must be run as .claude/activate.sh after a swap" >&2; exit 1; }
  [[ -d .claude1 ]] || { echo "activate: .claude1 is missing; nothing to revert" >&2; exit 1; }
  [[ ! -e .claude2 ]] || { echo "activate: .claude2 already exists" >&2; exit 1; }
  say "Plan (swap back):"
  run mv .claude .claude2 || { echo "activate: could not rename .claude; nothing changed" >&2; exit 1; }
  if ! run mv .claude1 .claude; then
    echo "activate: could not rename .claude1; putting .claude back" >&2
    mv .claude2 .claude
    exit 1
  fi
  if [[ -f .claude/mcp.root.json ]]; then
    run mv .claude/mcp.root.json .mcp.json
  else
    run rm -f .mcp.json
  fi
  remove_exclude
  if [[ $DRY -eq 1 ]]; then
    say "(dry run; nothing changed)"
  else
    say "Done. Restart Claude Code. The new infra is back in .claude2/ (its settings.local.json travelled with it)."
  fi
}

guard_session
case $MODE in
  forward) forward ;;
  revert) revert ;;
esac
