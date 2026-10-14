#!/bin/bash
# usage: devruns.sh <tag>   — 4 cold dev runs, one at a time (eval user), outputs tools/out/runs/<tag>-<vid>.json
TOOLS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$TOOLS/../../../.." && pwd)"
OUT="$TOOLS/out/runs"
cd "$ROOT"
. "$TOOLS/_env.sh"
load_env_keys '^(EVAL_USER_[A-Z_]+|MONGODB_URI)='
mkdir -p "$OUT"
for v in T1dQhQAm8Tc jMq8lEu-of0 uC45_4nnEAI EqaMrzd9nZU; do
  echo "### $v $(date +%T)"
  services/summarizer/.venv/bin/python "$TOOLS/devrun.py" "$v" --cold > "$OUT/$1-$v.json" 2> "$OUT/$1-$v.err"
  echo "rc=$? $(date +%T)"
done
