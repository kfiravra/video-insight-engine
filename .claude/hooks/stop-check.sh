#!/usr/bin/env bash
# Stop hook. Typechecks and lints only the packages touched this session
# (recorded by post-edit.sh), via lib/pkgmap.sh. Silent when everything passes.
# On real errors it blocks the stop once with the error list; if the same errors
# are still there on the next stop, or Claude is already continuing because of
# this hook (stop_hook_active), it only reports them and clears the touched list
# so a red session is not re-checked on every Stop.
#
# When nothing was recorded (edits went through Bash, or the session was resumed)
# it falls back to the dirty files in the working tree, once per tree state.
#
# Env: VIE_STOP_DEADLINE (seconds, default 240), VIE_STOP_PYRIGHT=1 adds pyright,
#      VIE_STOP_DRY_RUN=1 prints the commands instead of running them,
#      VIE_STOP_PKGMAP overrides the package-map script (tests).

source "$(dirname "${BASH_SOURCE[0]}")/lib/tools.sh"
vie_require_jq report

input=$(cat)
sid=$(printf '%s' "$input" | jq -r '.session_id // empty' 2>/dev/null)
active=$(printf '%s' "$input" | jq -r '.stop_hook_active // false' 2>/dev/null)
state="$(vie_state_dir "$sid")"
touched="$state/touched"

# Dirty working-tree files (added, modified, renamed, untracked).
tree_files() {
  git -C "$VIE_ROOT" status --porcelain --untracked-files=all 2>/dev/null \
    | grep -vE '^.?D' | sed 's/^...//; s/.* -> //'
}
tree_hash() {
  local f
  { tree_files; while IFS= read -r f; do stat -c '%Y %s' "$VIE_ROOT/$f" 2>/dev/null; done < <(tree_files); } \
    | sha1sum | cut -c1-12
}

files=()
from_tree=0
if [[ -s "$touched" ]]; then
  # Drop files deleted since they were recorded; a check on a missing file is noise.
  while IFS= read -r f; do
    [[ -n "$f" && -e "$VIE_ROOT/$f" ]] && files+=("$f")
  done < <(sort -u "$touched")
  [[ ${#files[@]} -eq 0 ]] && : > "$touched"
fi
if [[ ${#files[@]} -eq 0 ]]; then
  th=$(tree_hash)
  [[ "$th" == "$(cat "$state/last-tree" 2>/dev/null)" ]] && exit 0
  mapfile -t files < <(tree_files)
  from_tree=1
  [[ ${#files[@]} -eq 0 ]] && { printf '%s' "$th" > "$state/last-tree"; exit 0; }
fi

pkgmap="${VIE_STOP_PKGMAP:-$VIE_LIB_DIR/pkgmap.sh}"
mapfile -t checks < <(bash "$pkgmap" "${files[@]}")
if [[ ${#checks[@]} -eq 0 ]]; then
  : > "$touched"
  (( from_tree == 1 )) && printf '%s' "$th" > "$state/last-tree"
  exit 0
fi

# Keep file:line for every tool. tsc prints it inline; eslint prints the file on
# its own line above indented "line:col error" rows; ruff (full format) prints the
# code line, then " --> file:line:col".
extract_errors() {
  awk '
    /^[^ \t].*\.(ts|tsx|js|jsx|mjs|cjs|py)$/ && $0 !~ /error/ { path = $0; next }
    /error TS[0-9]+/                               { print; n++ }
    /^[ \t]+[0-9]+:[0-9]+[ \t]+error/              { sub(/^[ \t]+/, ""); print path ":" $0; n++ }
    /^[^ ]+:[0-9]+:[0-9]+: [A-Z]+[0-9]+/           { print; n++ }
    /^[A-Z]+[0-9]+( \[\*\])? /                     { code = $0 }
    /^[ \t]*--> [^ ]+:[0-9]+:[0-9]+/               { print code; print; n++ }
    /^Found [0-9]+ error/ || /problems? \(/        { print; n++ }
    n >= 12 { exit }
  '
}

deadline=$(( $(date +%s) + ${VIE_STOP_DEADLINE:-240} ))
errors=""
skipped=""
nl=$'\n'

for line in "${checks[@]}"; do
  IFS=$'\t' read -r label cwd command <<<"$line"
  if [[ "$label" == "skip" ]]; then
    skipped+="$command; "
    continue
  fi
  if [[ "${VIE_STOP_DRY_RUN:-0}" == "1" ]]; then
    printf '%s: (cd %s && %s)\n' "$label" "$cwd" "$command"
    continue
  fi
  remaining=$(( deadline - $(date +%s) ))
  if (( remaining < 10 )); then
    skipped+="$label (deadline); "
    continue
  fi
  (( remaining > 120 )) && remaining=120
  out=$(cd "$VIE_ROOT/$cwd" 2>/dev/null && timeout "$remaining" bash -c "$command" 2>&1)
  rc=$?
  if (( rc == 124 )); then
    skipped+="$label (timeout); "
  elif (( rc != 0 )); then
    lines=$(printf '%s\n' "$out" | extract_errors)
    [[ -z "$lines" ]] && lines=$(printf '%s\n' "$out" | grep -v '^\s*$' | tail -12)
    errors+="$label (exit $rc):$nl$lines$nl"
  fi
done

[[ "${VIE_STOP_DRY_RUN:-0}" == "1" ]] && exit 0

if [[ -z "$errors" ]]; then
  : > "$touched"
  rm -f "$state/last-errors"
  (( from_tree == 1 )) && printf '%s' "$th" > "$state/last-tree"
  [[ -n "$skipped" ]] && vie_json systemMessage "stop-check skipped: $skipped"
  exit 0
fi

hash=$(printf '%s' "$errors" | sha1sum | cut -c1-12)
prev=$(cat "$state/last-errors" 2>/dev/null || true)
report="$errors"
[[ -n "$skipped" ]] && report+="skipped: $skipped$nl"

if [[ "$active" != "true" && ! -f "$state/red" && ! -f "$VIE_STATE_ROOT/red" && "$hash" != "$prev" ]]; then
  printf '%s' "$hash" > "$state/last-errors"
  jq -cn --arg r "stop-check found errors in packages touched this session. Fix them before finishing:$nl$report" \
    '{decision:"block",reason:$r}'
else
  # Same errors again, or already continuing: report, and stop re-checking until the next edit.
  : > "$touched"
  (( from_tree == 1 )) && printf '%s' "$th" > "$state/last-tree"
  vie_json systemMessage "stop-check: errors remain (not blocking again; next edit re-arms the check):$nl$report"
fi
exit 0
