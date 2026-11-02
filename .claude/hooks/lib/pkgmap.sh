#!/usr/bin/env bash
# Map touched paths to the checks that cover them. Single source of truth for
# stop-check.sh, /check and /ship.
#
#   pkgmap.sh [--all] [--tests] <path>...
#
# Output: one check per line, tab-separated:  <label> <cwd relative to root> <command>
# A "skip" label carries a reason instead of a command.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/tools.sh"

all=0
tests=0
paths=()
for a in "$@"; do
  case "$a" in
    --all) all=1 ;;
    --tests) tests=1 ;;
    *) paths+=("$a") ;;
  esac
done

want_types=0 want_shared=0 want_api=0 want_web=0 want_adminui=0 want_hooks=0
api_ts=() web_ts=() admin_ts=() py_files=()
py_services=()

add_py_service() {
  local s="$1"
  local x
  for x in "${py_services[@]:-}"; do [[ "$x" == "$s" ]] && return; done
  py_services+=("$s")
}

if [[ ${#paths[@]} -gt 0 ]]; then
  for p in "${paths[@]}"; do
    rel="$(vie_rel "$p")"
    [[ -z "$rel" ]] && continue
    case "$rel" in
      api/*)
        want_api=1
        [[ "$rel" == *.ts ]] && api_ts+=("${rel#api/}") ;;
      apps/web/*)
        want_web=1
        [[ "$rel" == *.ts || "$rel" == *.tsx ]] && web_ts+=("${rel#apps/web/}") ;;
      packages/types/*) want_types=1; want_api=1; want_web=1 ;;
      packages/shared/*) want_shared=1; want_api=1; want_web=1 ;;
      services/admin/ui/*)
        want_adminui=1
        [[ "$rel" == *.ts || "$rel" == *.tsx ]] && admin_ts+=("${rel#services/admin/ui/}") ;;
      services/summarizer/*.py) py_files+=("$rel"); add_py_service services/summarizer ;;
      services/assistant/*.py) py_files+=("$rel"); add_py_service services/assistant ;;
      services/admin/*.py) py_files+=("$rel"); add_py_service services/admin ;;
      packages/llm-common/*.py) py_files+=("$rel"); add_py_service packages/llm-common ;;
      .claude/hooks/*|.claude2/hooks/*) want_hooks=1 ;;
    esac
  done
fi

if [[ $all -eq 1 ]]; then
  want_types=1 want_shared=1 want_api=1 want_web=1 want_adminui=1 want_hooks=1
  py_files=(services/summarizer services/assistant services/admin packages/llm-common)
  py_services=(services/summarizer services/assistant services/admin packages/llm-common)
fi

emit() { printf '%s\t%s\t%s\n' "$1" "$2" "$3"; }

[[ $want_types -eq 1 ]] && emit types . "pnpm --filter @vie/types build"
[[ $want_shared -eq 1 ]] && emit shared . "pnpm --filter @vie/shared build"

if [[ $want_api -eq 1 ]]; then
  emit api-tsc . "pnpm --filter @vie/api exec tsc --noEmit"
  if [[ $all -eq 1 ]]; then
    emit api-eslint api "pnpm exec eslint src/"
  elif [[ ${#api_ts[@]} -gt 0 ]]; then
    emit api-eslint api "pnpm exec eslint ${api_ts[*]}"
  fi
  [[ $tests -eq 1 ]] && emit api-test . "pnpm --filter @vie/api test"
fi

if [[ $want_web -eq 1 ]]; then
  # `tsc -b` follows tsconfig.json's references into tsconfig.app.json; a bare
  # `tsc --noEmit` against the root config checks nothing (2026-05-31: two TS6133
  # errors passed it and broke the Docker build). Refuse to emit the hollow form.
  if [[ -f "$VIE_ROOT/apps/web/tsconfig.app.json" ]]; then
    emit web-tsc . "pnpm --filter web exec tsc -b"
  else
    emit skip . "apps/web/tsconfig.app.json missing: tsc -b would check nothing, fix the tsconfig layout first"
  fi
  if [[ $all -eq 1 ]]; then
    emit web-eslint apps/web "pnpm exec eslint ."
  elif [[ ${#web_ts[@]} -gt 0 ]]; then
    emit web-eslint apps/web "pnpm exec eslint ${web_ts[*]}"
  fi
  [[ $tests -eq 1 ]] && emit web-test . "pnpm --filter web exec vitest run"
fi

if [[ $want_adminui -eq 1 ]]; then
  if [[ -d "$VIE_ROOT/services/admin/ui/node_modules" ]]; then
    emit adminui-tsc services/admin/ui "npx tsc -b"
    if [[ $all -eq 1 ]]; then
      emit adminui-eslint services/admin/ui "npx eslint ."
    elif [[ ${#admin_ts[@]} -gt 0 ]]; then
      emit adminui-eslint services/admin/ui "npx eslint ${admin_ts[*]}"
    fi
    [[ $tests -eq 1 ]] && emit adminui-test services/admin/ui "npx vitest run"
  else
    emit skip . "services/admin/ui has no node_modules (run npm ci there)"
  fi
fi

if [[ ${#py_files[@]} -gt 0 ]]; then
  if ruff="$(vie_ruff)"; then
    emit ruff . "$ruff check --config ruff.toml ${py_files[*]}"
  else
    emit skip . "ruff not found (PATH or services/summarizer/.venv/bin/ruff)"
  fi
  if [[ "${VIE_STOP_PYRIGHT:-0}" == "1" ]]; then
    for svc in services/summarizer services/assistant; do
      files=()
      for f in "${py_files[@]}"; do [[ "$f" == "$svc"/* ]] && files+=("${f#"$svc"/}"); done
      [[ ${#files[@]} -gt 0 ]] && emit "pyright-${svc##*/}" "$svc" "npx pyright@1.1.407 --project . ${files[*]}"
    done
  fi
  if [[ $tests -eq 1 ]]; then
    for svc in "${py_services[@]:-}"; do
      [[ -z "$svc" ]] && continue
      if [[ -x "$VIE_ROOT/$svc/.venv/bin/python" ]]; then
        emit "pytest-${svc##*/}" "$svc" ".venv/bin/python -m pytest"
      else
        emit "pytest-${svc##*/}" "$svc" "python3 -m pytest"
      fi
    done
  fi
fi

[[ $want_hooks -eq 1 ]] && emit hooks . "node --test $VIE_INFRA_DIR/hooks/tests/*.test.mjs"

exit 0
