#!/bin/bash
TOOLS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$TOOLS/../../../../services/summarizer"
for v in T1dQhQAm8Tc jMq8lEu-of0 uC45_4nnEAI; do
  echo "### $v"
  .venv/bin/python ../../scripts/replay.py --video "$v" --speed 0.1 2>&1 | grep -v -i "warn" | tail -22
done
