#!/usr/bin/env bash
# SessionStart hook (startup, resume, clear, compact). Prints a few lines of
# context once per session instead of per prompt: branch, dirty files, guard
# state, active task folders and how to resume them. Also garbage-collects old
# session state.

source "$(dirname "${BASH_SOURCE[0]}")/lib/tools.sh"
cat >/dev/null  # consume stdin

command -v jq >/dev/null 2>&1 || echo "warning: jq is not installed; git-guard refuses every Bash command until it is (sudo apt install jq)"

find "$VIE_STATE_ROOT" -mindepth 1 -maxdepth 1 -type d -mtime +7 -exec rm -rf {} + 2>/dev/null
find "$VIE_STATE_ROOT" -maxdepth 1 -name red -mtime +1 -delete 2>/dev/null   # stale /tdd marker
# A forgotten /guard off re-enables itself after a day; the safe direction is on.
if [[ -f "$VIE_GUARD_OFF" ]] && find "$VIE_STATE_ROOT" -maxdepth 1 -name guard-off -mtime +1 | grep -q .; then
  rm -f "$VIE_GUARD_OFF"
  echo "git guard: was OFF for over a day, re-enabled"
fi
[[ -f "$VIE_GUARD_OFF" ]] && echo "git guard: OFF (/guard on to re-enable; stash, reset --hard, clean, restore and force-push run unblocked)"

cd "$VIE_ROOT" 2>/dev/null || exit 0

branch=$(git branch --show-current 2>/dev/null || echo "?")
if [[ -d .claude1 ]]; then
  dirty=$(git status --porcelain -- . ':!.claude' ':!.claude1' 2>/dev/null | wc -l | tr -d ' ')
  echo "branch $branch, $dirty dirty files (excluding the infra swap; .claude1/ present, git add -A is blocked)"
else
  dirty=$(git status --porcelain 2>/dev/null | wc -l | tr -d ' ')
  echo "branch $branch, $dirty dirty files"
fi

if [[ "$VIE_INFRA_DIR" == "$VIE_ROOT/.claude" && -f CLAUDE.md && -f .claude/CLAUDE.md ]]; then
  if ! jq -e '.claudeMdExcludes[]? | select(endswith("/CLAUDE.md"))' .claude/settings.json >/dev/null 2>&1; then
    echo "warning: both CLAUDE.md and .claude/CLAUDE.md load; add claudeMdExcludes to .claude/settings.json"
  fi
fi

shopt -s nullglob
tasks=(dev/active/*/)
shopt -u nullglob
if [[ ${#tasks[@]} -gt 0 ]]; then
  echo "active tasks (dev/active/):"
  for d in "${tasks[@]}"; do
    t=$(basename "$d")
    if [[ -f "$d/notes.md" ]]; then
      next=$(awk '/^## Next/{f=1;next} f&&NF{print;exit}' "$d/notes.md" | cut -c1-110)
      echo "  $t: ${next:-no Next entry in notes.md}"
    else
      ctx=$(ls "$d"/*-context.md 2>/dev/null | head -1)
      upd=$(grep -m1 -oE 'Last Updated:? *[0-9-]+' "${ctx:-/dev/null}" 2>/dev/null)
      echo "  $t: legacy layout${upd:+, $upd}"
    fi
  done
  echo "  /resume <task> to continue, /handoff before clearing"
fi
exit 0
