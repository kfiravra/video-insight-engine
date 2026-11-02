#!/usr/bin/env bash
# Backs the /guard skill.  guard-ctl.sh off | on | status
# `off` writes <state>/guard-off: git-guard.sh stops blocking and asking (the
# settings.json ask rules still prompt; they are harness-side). session-start.sh
# prints "git guard: OFF" while the file exists and removes it after a day.
# `on` removes the file. Anything else prints the current state.
source "$(dirname "${BASH_SOURCE[0]}")/lib/tools.sh"

case "${1:-status}" in
  off)
    mkdir -p "$VIE_STATE_ROOT"
    : > "$VIE_GUARD_OFF"
    echo "git guard: OFF. stash, reset --hard, checkout --, clean, restore, force-push run unblocked; push/rebase/PR/docker no longer ask from the hook. /guard on to re-enable (auto re-enables after a day)."
    ;;
  on)
    rm -f "$VIE_GUARD_OFF"
    echo "git guard: ON."
    ;;
  *)
    if [[ -f "$VIE_GUARD_OFF" ]]; then
      echo "git guard: OFF since $(date -r "$VIE_GUARD_OFF" '+%Y-%m-%d %H:%M'). /guard on to re-enable."
    else
      echo "git guard: ON. /guard off to stand it down for this session."
    fi
    ;;
esac
exit 0
