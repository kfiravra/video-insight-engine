#!/usr/bin/env bash
# PreToolUse hook for Bash. Exit 2 blocks (stderr goes to Claude); a JSON
# permissionDecision "ask" surfaces a prompt even in bypass mode.
#
# Blocks:  git stash (except list/show), reset --hard|--merge|--keep, checkout -- / checkout . /
#          checkout <path> / checkout -f, switch --discard-changes|-f, clean, restore (except a
#          pure --staged), force push (-f, --force*, +ref, deleting main), branch -D/-d main,
#          worktree remove --force, update-ref -d.
#          While .claude1/ exists (infra swap in progress): git add -A/./-u, git commit -a.
# Asks:    git push, git rebase, gh pr create/merge, container-recreating docker commands.
# Override: create <state>/git-override after explicit user approval; consumed once.
# Off:      /guard off writes <state>/guard-off; blocks and asks stand down until /guard on.
#
# The command is split into segments on ; && || | and each segment is checked on its
# own, so an exemption in one segment (`git stash list`) never covers another
# (`git stash pop`). Quoted strings are scanned too when the command runs an
# interpreter (`bash -c "git reset --hard"`). Heredoc bodies that are not fed to an
# interpreter are ignored, so prose that mentions git commands cannot trip the guard.
#
# Rationale: prose bans did not stop the 2026-05-28 stash incident, a hook does.
# A container recreate killed a live pipeline run on 2026-10-21, so those ask first.

source "$(dirname "${BASH_SOURCE[0]}")/lib/tools.sh"
vie_require_jq block

tool_info=$(cat)
cmd=$(printf '%s' "$tool_info" | jq -r '.tool_input.command // empty' 2>/dev/null)
[[ -z "$cmd" ]] && exit 0

INTERP='(^|[ ;&|(`])(bash|sh|zsh|dash|eval|xargs)( |$)'

# Drop heredoc bodies unless the heredoc feeds an interpreter. Here-strings (<<<)
# are neutralised first so they are not mistaken for heredoc operators.
strip_heredocs() {
  awk -v interp="$INTERP" '
    BEGIN { inh = 0 }
    inh == 1 { if ($0 ~ ("^[ \t]*" term "[ \t]*$")) inh = 0; next }
    {
      line = $0
      gsub(/<<</, " ", line)
      if (match(line, /<<-?[ \t]*["'"'"']?[A-Za-z_][A-Za-z0-9_]*["'"'"']?/)) {
        pre = substr(line, 1, RSTART - 1)
        t = substr(line, RSTART, RLENGTH)
        sub(/<<-?[ \t]*/, "", t); gsub(/["'"'"']/, "", t)
        if (pre !~ interp) { term = t; inh = 1 }
      }
      print line
    }'
}

raw=$(printf '%s\n' "$cmd" | strip_heredocs)
# One line, single spaces, single-word quoted tokens unwrapped (git "stash" must not evade).
base=$(printf '%s' "$raw" | tr '\n\t' '  ' | sed 's/  */ /g' \
  | sed "s/'\([^' ]*\)'/\1/g; s/\"\([^\" ]*\)\"/\1/g")
# flat: multi-word quoted strings removed, so commit messages and echo text do not match.
flat=$(printf '%s' "$base" | sed "s/'[^']*'//g; s/\"[^\"]*\"//g")
# inner: the same text with the quotes dropped but their content kept; scanned only
# when an interpreter appears, so `bash -c "git reset --hard"` is caught.
inner=$(printf '%s' "$base" | sed "s/'//g; s/\"//g")

segments() {
  printf '%s\n' "$1" | sed 's/&&\|||\||\|;/\n/g' | sed 's/^ *//; s/ *$//' | grep -v '^$'
}
mapfile -t segs < <(segments "$flat")
if grep -qE "$INTERP" <<<"$base"; then
  mapfile -t -O "${#segs[@]}" segs < <(segments "$inner")
fi

G='\bgit( -[^ ]+( [^ ]+)?)*'   # git plus global options only; the next word is the subcommand

is_dangerous() {
  local c="$1"
  if grep -qE "$G stash\b" <<<"$c" && ! grep -qE "$G stash (list|show)\b" <<<"$c"; then
    echo "git stash (banned: 2026-05-28 near-loss; read with 'git show HEAD:<path>' or use a worktree)"
    return 0
  fi
  if grep -qE "$G reset\b" <<<"$c" && grep -qE '(^| )(--hard|--merge|--keep)\b' <<<"$c"; then
    echo "git reset --hard/--merge/--keep (discards uncommitted work)"
    return 0
  fi
  if grep -qE "$G checkout\b" <<<"$c"; then
    if grep -qE "$G checkout( [^ ]+)* -- " <<<"$c" || grep -qE "$G checkout \.( |$)" <<<"$c" ||
       grep -qE "$G checkout( [^ ]+)* (-f|--force)\b" <<<"$c"; then
      echo "git checkout -- <path> / . / -f (discards uncommitted work; read with 'git show HEAD:<path>')"
      return 0
    fi
    if checkout_targets_paths "$c"; then
      echo "git checkout <tree-ish> <path> or checkout <existing path> (overwrites the working tree; use 'git show <ref>:<path>')"
      return 0
    fi
  fi
  if grep -qE "$G switch\b" <<<"$c" && grep -qE '(^| )(--discard-changes|-f|--force)\b' <<<"$c"; then
    echo "git switch --discard-changes (discards uncommitted work)"
    return 0
  fi
  if grep -qE "$G clean\b" <<<"$c"; then
    echo "git clean (deletes untracked files)"
    return 0
  fi
  if grep -qE "$G restore\b" <<<"$c"; then
    if ! grep -qE '(^| )--staged\b' <<<"$c" || grep -qE '(^| )(--worktree|-W|--source|-s)( |$)' <<<"$c"; then
      echo "git restore (discards uncommitted work; only a pure --staged unstage is allowed)"
      return 0
    fi
  fi
  if grep -qE "$G push\b" <<<"$c"; then
    if grep -qE '(^| )(-f|--force|--force-with-lease(=[^ ]*)?|--force-if-includes)( |$)' <<<"$c" ||
       grep -qE ' \+[^ ]+' <<<"$c"; then
      echo "force push (rewrites remote history)"
      return 0
    fi
    if grep -qE '(^| )(--delete|-d) +(main|master)\b' <<<"$c" || grep -qE ' :(main|master)( |$)' <<<"$c"; then
      echo "deleting the main branch on the remote"
      return 0
    fi
  fi
  if grep -qE "$G branch\b" <<<"$c" && grep -qE '(^| )(main|master)( |$)' <<<"$c" &&
     grep -qE '(^| )(-[a-zA-Z]*[dD][a-zA-Z]*|--delete)( |$)' <<<"$c"; then
    echo "deleting the main branch"
    return 0
  fi
  if grep -qE "$G worktree remove\b" <<<"$c" && grep -qE '(^| )(-f|--force)( |$)' <<<"$c"; then
    echo "git worktree remove --force (deletes a worktree with uncommitted work)"
    return 0
  fi
  if grep -qE "$G update-ref( [^ ]+)* -d( |$)" <<<"$c"; then
    echo "git update-ref -d (deletes a ref outside the normal branch commands)"
    return 0
  fi
  return 1
}

# `git checkout <ref> <path>` and `git checkout <existing path>` overwrite files.
# Branch switches and creations (`-b`, `-B`, `--orphan`, `-t`, `--detach`) do not.
checkout_targets_paths() {
  local c="$1" tok after=0 positional=()
  for tok in $c; do
    if (( after == 0 )); then
      [[ "$tok" == "checkout" ]] && after=1
      continue
    fi
    case "$tok" in
      -b|-B|--orphan|-t|--track|--detach) return 1 ;;
      -*) ;;
      *) positional+=("$tok") ;;
    esac
  done
  (( ${#positional[@]} >= 2 )) && return 0
  if (( ${#positional[@]} == 1 )); then
    local p="${positional[0]}"
    [[ "$p" == /* && -e "$p" ]] && return 0
    [[ "$p" != /* && -e "$VIE_ROOT/$p" ]] && return 0
  fi
  return 1
}

swap_guard() {
  local c="$1"
  [[ -d "$VIE_ROOT/.claude1" ]] || return 1
  if grep -qE "$G add( [^ ]+)* (-A|--all|-u|--update|\.)( |$)" <<<"$c" ||
     grep -qE "$G commit( [^ ]+)* (-a|--all|-am)\b" <<<"$c"; then
    echo "infra swap in progress (.claude1/ present): this would stage the swapped .claude/ tree. Add explicit paths instead."
    return 0
  fi
  return 1
}

ask_gate() {
  local c="$1"
  if grep -qE "$G push\b" <<<"$c"; then
    echo "git push needs explicit current-turn user approval (CLAUDE.md git policy)"
    return 0
  fi
  if grep -qE "$G rebase\b" <<<"$c"; then
    echo "git rebase rewrites local history and needs explicit current-turn approval"
    return 0
  fi
  if grep -qE '\bgh( [^ ]+)* pr (create|merge)\b' <<<"$c"; then
    echo "opening or merging a PR needs explicit current-turn user approval"
    return 0
  fi
  if grep -qE '\bdocker(-compose| compose)( [^ ]+)* (restart|down|stop|rm|kill)\b' <<<"$c" ||
     grep -qE '\bdocker( [^ ]+)* (restart|stop|rm|kill)\b' <<<"$c" ||
     grep -qE '\bdocker(-compose| compose)( [^ ]+)* up\b.*(--force-recreate|--build)' <<<"$c"; then
    echo "may recreate or kill a running container: check the admin for a live pipeline run first (2026-10-21 incident)"
    return 0
  fi
  return 1
}

block() {
  cat >&2 <<EOF
BLOCKED by git-guard: $1

Command: $flat
EOF
  [[ -n "${2:-}" ]] && printf '\n%s\n' "$2" >&2
  exit 2
}

guard_off=0
[[ -f "$VIE_GUARD_OFF" ]] && guard_off=1

for seg in "${segs[@]}"; do
  if reason=$(swap_guard "$seg"); then
    block "$reason"
  fi
done

if (( guard_off == 1 )); then
  exit 0
fi

for seg in "${segs[@]}"; do
  if reason=$(is_dangerous "$seg"); then
    override="$VIE_STATE_ROOT/git-override"
    if [[ -f "$override" ]]; then
      rm -f "$override"
      echo "git-guard: override consumed, allowing: $flat" >&2
      exit 0
    fi
    block "$reason" "This mutates the working tree and needs explicit, current-turn user approval.
If the user approved it, create the single-use override file (touch $override) and retry,
or ask them to run /guard off for the rest of the session."
  fi
done

for seg in "${segs[@]}"; do
  if ask_reason=$(ask_gate "$seg"); then
    jq -cn --arg r "$ask_reason" \
      '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"ask",permissionDecisionReason:$r}}'
    break
  fi
done
exit 0
