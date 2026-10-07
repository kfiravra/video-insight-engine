"""Synthesized LLM entries for stages the recorded runs predate (pipeline-1min 1b).

The three cassettes were recorded before the tier probe existed: their runs
made one ``classifier`` call instead. The classifier is gone (1b.1), so its
entry becomes a ``tier_probe`` entry built from the same recorded answer —
domain, format and confidence copied, ``has_visual_demo`` judged per video —
and marked ``synthetic: true``. Assumptions (gate-0 tier-probe A/B, Haiku 4.5
at temperature 0, bare JSON): ≈ 1.0 s per call (A/B: 0.89–1.03 s without the
reasoning tail, max 1.64 s with it), ≈ 1,800 input / 40 output tokens.

``build_cassette`` applies this to a fresh dump that still has a classifier
span; the committed cassettes were converted with the same function.
"""

from __future__ import annotations

import json
from typing import Any

PROBE_FEATURE = "summarize:tier_probe"
PROBE_SPAN = "tier_probe"
PROBE_MODEL = "anthropic/claude-haiku-4-5-20251001"
PROBE_LATENCY_MS = 1000
PROBE_USAGE = {"input": 1800, "output": 40, "cacheRead": 0, "cacheWrite": 0}
CLASSIFIER_SPAN = "classifier"

PROBE_NOTE = (
    "tier_probe#0 is SYNTHETIC (task 1b.1): the recorded run called the retired "
    "classifier instead; its domain/format/confidence are kept, has_visual_demo is "
    "judged from the video, and wall/tokens are the gate-0 A/B Haiku numbers "
    f"({PROBE_LATENCY_MS} ms, {PROBE_USAGE['input']} in / {PROBE_USAGE['output']} out). "
    "See tests/replay/synthetic.py."
)


def probe_output(classifier_output: str, has_visual_demo: bool) -> str:
    """The probe's bare-JSON answer from a recorded classifier answer."""
    recorded = json.loads(classifier_output)
    return json.dumps(
        {
            "domain": recorded["domain"],
            "format": recorded["format"],
            "has_visual_demo": has_visual_demo,
            "confidence": recorded["confidence"],
        }
    )


def probe_entry(classifier_entry: dict[str, Any], has_visual_demo: bool) -> dict[str, Any]:
    """The cassette's ``tier_probe#0`` entry replacing a recorded ``classifier`` entry."""
    return {
        "feature": PROBE_FEATURE,
        "span": PROBE_SPAN,
        "model": PROBE_MODEL,
        "latencyMs": PROBE_LATENCY_MS,
        "output": probe_output(classifier_entry["output"], has_visual_demo),
        "finishReason": "stop",
        "usage": dict(PROBE_USAGE),
        "ordinal": 0,
        "synthetic": True,
    }


def replace_classifier_entries(
    entries: list[dict[str, Any]], has_visual_demo: bool
) -> list[dict[str, Any]]:
    """``entries`` with each recorded classifier call swapped for its probe entry."""
    return [
        probe_entry(entry, has_visual_demo) if entry["span"] == CLASSIFIER_SPAN else entry
        for entry in entries
    ]
