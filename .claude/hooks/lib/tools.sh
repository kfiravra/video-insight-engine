#!/usr/bin/env bash
# Shared helpers for the hooks. Source this file; do not execute it.
# Every path derives from this file's location, so the hooks work before the
# folder swap (.claude2/) and after it (.claude/).

VIE_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VIE_INFRA_DIR="$(cd "$VIE_LIB_DIR/../.." && pwd)"
VIE_ROOT="${CLAUDE_PROJECT_DIR:-$(cd "$VIE_INFRA_DIR/.." && pwd)}"
# Tests point this at a temp dir so they never touch live session state.
VIE_STATE_ROOT="${VIE_STATE_ROOT:-$VIE_INFRA_DIR/state}"
# Written by /guard off, removed by /guard on. While present, git-guard.sh
# stands down and session-start.sh says so.
VIE_GUARD_OFF="$VIE_STATE_ROOT/guard-off"

# Every hook parses its stdin with jq. Without it a hook cannot tell a safe
# command from a destructive one, so the caller decides how to fail:
#   vie_require_jq block   -> exit 2 (PreToolUse: refuse the tool call)
#   vie_require_jq report  -> print one line, exit 0 (Stop/PostToolUse/SessionStart)
vie_require_jq() {
  command -v jq >/dev/null 2>&1 && return 0
  local msg="jq is not installed; the Claude Code hooks cannot parse their input. Install it (sudo apt install jq) and retry."
  if [[ "${1:-report}" == "block" ]]; then
    printf 'BLOCKED by %s: %s\n' "$(basename "$0")" "$msg" >&2
    exit 2
  fi
  printf '%s: %s\n' "$(basename "$0")" "$msg"
  exit 0
}

# Per-session state directory (created on demand).
vie_state_dir() {
  local d="$VIE_STATE_ROOT/${1:-nosession}"
  mkdir -p "$d" 2>/dev/null
  printf '%s' "$d"
}

# Absolute or relative path -> path relative to VIE_ROOT. Empty when outside the project.
vie_rel() {
  local p="$1"
  case "$p" in
    "$VIE_ROOT"/*) printf '%s' "${p#"$VIE_ROOT"/}" ;;
    /*) printf '' ;;
    *) printf '%s' "$p" ;;
  esac
}

# Prints a ruff binary (PATH first, then the summarizer venv) or returns 1.
vie_ruff() {
  if command -v ruff >/dev/null 2>&1; then
    command -v ruff
    return 0
  fi
  local r="$VIE_ROOT/services/summarizer/.venv/bin/ruff"
  if [[ -x "$r" ]]; then
    printf '%s' "$r"
    return 0
  fi
  return 1
}

# Prints the eslint binary of a package dir (relative to VIE_ROOT) or returns 1.
vie_eslint_bin() {
  local b="$VIE_ROOT/$1/node_modules/.bin/eslint"
  if [[ -x "$b" ]]; then
    printf '%s' "$b"
    return 0
  fi
  return 1
}

# One-key JSON object, e.g. vie_json systemMessage "text".
vie_json() {
  jq -cn --arg k "$1" --arg v "$2" '{($k): $v}'
}
