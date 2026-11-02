#!/usr/bin/env bash
# PostToolUse hook for Edit|Write and for Bash.
# 1. Records the touched paths for stop-check.sh (per-session state).
#    Edit/Write: the file_path. Bash: files the command wrote through `sed -i`,
#    `>`/`>>` redirects or `tee`, resolved against the command's cwd and kept only
#    when they exist. In bypass mode edits go through Bash, and before this the
#    Stop check never saw them (comparison 2026-11-01, §4.2 item 3).
# 2. Formats each file: eslint --fix for TS in api / apps/web / services/admin/ui,
#    ruff format for Python. Never `ruff check --fix` here: it deletes
#    `import x as x` re-exports and imports that are not used yet mid-edit.
# Always exits 0; formatting problems surface in stop-check, not here.

source "$(dirname "${BASH_SOURCE[0]}")/lib/tools.sh"
vie_require_jq report

input=$(cat)
tool=$(printf '%s' "$input" | jq -r '.tool_name // empty' 2>/dev/null)
sid=$(printf '%s' "$input" | jq -r '.session_id // empty' 2>/dev/null)

files=()
if [[ "$tool" == "Bash" ]]; then
  cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // empty' 2>/dev/null)
  [[ -z "$cmd" ]] && exit 0
  cwd=$(printf '%s' "$input" | jq -r '.cwd // empty' 2>/dev/null)
  base="${cwd:-$VIE_ROOT}"
  flat=$(printf '%s' "$cmd" | tr '\n\t' '  ' | sed "s/'//g; s/\"//g; s/  */ /g")
  candidates=()
  # redirect targets: anything after > or >> that is not a descriptor
  mapfile -t -O "${#candidates[@]}" candidates < <(printf '%s' "$flat" \
    | grep -oE '(^|[^<>&0-9])>>? *[^ ;&|<>]+' | sed 's/.*>>\? *//')
  # tee targets: every non-option word after tee up to the next operator
  mapfile -t -O "${#candidates[@]}" candidates < <(printf '%s' "$flat" \
    | grep -oE '(^|[ ;&|])tee( -[a-zA-Z]+)*( [^ ;&|<>-][^ ;&|<>]*)+' | sed 's/.*tee//; s/ -[a-zA-Z]*//g' | tr ' ' '\n')
  # sed -i segments: every word of the segment; the existence check below keeps the files
  mapfile -t -O "${#candidates[@]}" candidates < <(printf '%s\n' "$flat" \
    | sed 's/&&\|||\||\|;/\n/g' | grep -E '(^| )sed( [^ ]+)* -i' | tr ' ' '\n')
  for tok in "${candidates[@]}"; do
    [[ -z "$tok" || "$tok" == -* || "$tok" == "sed" ]] && continue
    abs="$tok"
    [[ "$abs" != /* ]] && abs="$base/$tok"
    [[ -f "$abs" ]] || continue
    abs="$(cd "$(dirname "$abs")" 2>/dev/null && pwd)/$(basename "$abs")"
    rel="$(vie_rel "$abs")"
    [[ -n "$rel" ]] && files+=("$rel")
  done
else
  fp=$(printf '%s' "$input" | jq -r '.tool_input.file_path // empty' 2>/dev/null)
  [[ -z "$fp" ]] && exit 0
  rel="$(vie_rel "$fp")"
  [[ -n "$rel" ]] && files+=("$rel")   # empty = outside the project (auto-memory, scratchpad)
fi

[[ ${#files[@]} -eq 0 ]] && exit 0
touched="$(vie_state_dir "$sid")/touched"
printf '%s\n' "${files[@]}" | sort -u >> "$touched"

format_ts() {                         # $1 = package dir relative to root, $2 = absolute file
  local bin
  if bin="$(vie_eslint_bin "$1")"; then
    (cd "$VIE_ROOT/$1" && "$bin" --fix "$2") >/dev/null 2>&1 || true
  fi
}

for rel in "${files[@]}"; do
  case "$rel" in
    *node_modules/*|*/dist/*|*/.venv/*|*__pycache__*) continue ;;
  esac
  abs="$VIE_ROOT/$rel"
  case "$rel" in
    api/*.ts) format_ts api "$abs" ;;
    apps/web/*.ts|apps/web/*.tsx) format_ts apps/web "$abs" ;;
    services/admin/ui/*.ts|services/admin/ui/*.tsx) format_ts services/admin/ui "$abs" ;;
    *.py)
      if ruff="$(vie_ruff)"; then
        "$ruff" format "$abs" >/dev/null 2>&1 || true
      fi ;;
  esac
done
exit 0
