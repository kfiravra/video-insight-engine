#!/usr/bin/env python3
"""Replay a recorded pipeline run through the real orchestrator — zero spend.

Drives ``stream_summarization`` on a cassette from
``services/summarizer/tests/replay/cassettes/``: LLM calls answer with the
recorded outputs after the recorded latency, media/transcript/S3/Qdrant
steps sleep their recorded walls, and every outbound socket is refused.
Prints the recorded-vs-replayed phase walls, the DONE line and the
``pipeline.timing`` counts.

Run with the summarizer venv::

    services/summarizer/.venv/bin/python scripts/replay.py --video T1dQhQAm8Tc
    services/summarizer/.venv/bin/python scripts/replay.py --video T1dQhQAm8Tc --speed 0

``--speed 1`` (default) reproduces the recorded walls (~4 min for
T1dQhQAm8Tc); ``--speed 0`` skips every sleep (well under a second, for CI);
values in between scale the sleeps and the table rescales them back.
Exit code 1 when the replay diverged (cassette miss, unused recording, network
attempt) or, at ``--speed`` > 0, when a phase is outside ``--tolerance``.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SUMMARIZER_DIR = _REPO_ROOT / "services" / "summarizer"
sys.path.insert(0, str(_SUMMARIZER_DIR))
# Same cwd as the test suite: no repo-root .env (and its keys) gets loaded.
os.chdir(_SUMMARIZER_DIR)
# LiteLLM would otherwise fetch its model-cost map over the network at import.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from tests.replay.cassette import available_cassettes, load_cassette  # noqa: E402
from tests.replay.driver import ReplayResult, run_replay  # noqa: E402
from tests.replay.report import (  # noqa: E402
    DEFAULT_TOLERANCE,
    format_table,
    out_of_tolerance,
    phase_rows,
)

logger = logging.getLogger("replay")


def _divergences(result: ReplayResult) -> list[str]:
    problems = [f"cassette miss: {m}" for m in result.llm_misses]
    problems += [f"unused recording: {k}" for k in result.llm_unused]
    problems += [f"network attempt: {a}" for a in result.network_attempts]
    if result.done_line is None:
        problems.append("no DONE line logged")
    return problems


def _print_summary(result: ReplayResult) -> None:
    timing = result.timing or {}
    print(f"video={result.video_id} speed={result.speed} wall={result.wall_ms / 1000:.1f}s")
    print(f"events={len(result.events)} last={result.event_names()[-3:]}")
    print(f"llm served={result.llm_calls_served} model_mismatches={result.llm_model_mismatches}")
    print(f"timing counts={timing.get('counts')}")
    print(f"timing milestones={timing.get('milestones')}")
    print(result.done_line or "(no DONE line)")


def _configure_logging(verbose: bool) -> None:
    """Pipeline logs only on ``--verbose`` (the summarizer installs its own handlers)."""
    logging.basicConfig(level=logging.INFO)
    if not verbose:
        for handler in logging.getLogger().handlers:
            handler.setLevel(logging.CRITICAL)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "replay").splitlines()[0])
    parser.add_argument("--video", required=True, choices=available_cassettes())
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE)
    parser.add_argument("--verbose", action="store_true", help="show pipeline logs")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    _configure_logging(args.verbose)
    cassette = load_cassette(args.video)
    result = asyncio.run(run_replay(cassette, speed=args.speed))
    _print_summary(result)
    rows = phase_rows(result, cassette)
    if args.speed > 0:
        print(format_table(rows))
    else:
        print("(phase walls are not comparable at --speed 0)")
    if cassette.recorded.estimated:
        print("note: recorded sub-steps are ESTIMATED for this cassette (see its notes)")
    problems = _divergences(result)
    if args.speed > 0:
        problems += [
            f"phase outside tolerance: {n}" for n in out_of_tolerance(rows, args.tolerance)
        ]
    for problem in problems:
        print(f"FAIL {problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
