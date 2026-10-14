#!/bin/bash
# FULL golden (18 live), alone, concurrency 3, bypassCache; then gate.py vs noise.json
TOOLS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$TOOLS/../../../.." && pwd)"
cd "$ROOT"
. "$TOOLS/_env.sh"
load_env_keys '^(EVAL_USER_[A-Z_]+|LANGFUSE_(PUBLIC|SECRET)_KEY|LANGFUSE_BASE_URL)='
services/summarizer/.venv/bin/python scripts/run_eval.py --concurrency 3
R=$(command ls -t reports/eval-*.json | head -1)
echo "REPORT $R"
services/summarizer/.venv/bin/python scripts/gate.py --report "$R" --noise dev/golden-dataset/noise.json
echo "gate rc=$?"
