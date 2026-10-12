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
values in between (>= 0.02) scale the sleeps and the table rescales them back.
Exit code 1 when the replay diverged (cassette miss, unused recording, model
mismatch, network attempt, unfaked subprocess) or, at ``--speed`` > 0, when a
phase is outside ``--tolerance``.

Split extraction (pipeline-1min 3.4, ``EXTRACTION_PARALLEL`` — the default):
the group calls are synthesized from the recorded single call
(``tests/replay/group_synth.py``) and run at the prod
``EXTRACTION_PARALLEL_BATCHES`` (the cassettes pin 2); one line per group call
gives its start, time to first chunk, wall and output tokens (seconds at
speed 1, from the run start; plan-done and frames-done for reference). Its
recorded single call is then unused by design (not a divergence). The tab
timeline (3.6) lists every ``tab_ready`` (provisional, per group, final) with
the extraction, quiz and ``complete`` events between them.
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
from tests.replay.group_synth import EXTRACTION_KEY, is_group_feature  # noqa: E402
from tests.replay.report import (  # noqa: E402
    DEFAULT_TOLERANCE,
    check_speed,
    divergences,
    format_table,
    out_of_tolerance,
    phase_rows,
)

from src.config import Settings, settings  # noqa: E402

logger = logging.getLogger("replay")

# Prod's per-run extraction slots (the config default); the cassettes pin 2.
_PROD_PARALLEL_BATCHES = Settings.model_fields["EXTRACTION_PARALLEL_BATCHES"].default


def _print_summary(result: ReplayResult) -> None:
    timing = result.timing or {}
    print(f"video={result.video_id} speed={result.speed} wall={result.wall_ms / 1000:.1f}s")
    print(f"events={len(result.events)} last={result.event_names()[-3:]}")
    print(f"llm served={result.llm_calls_served} model_mismatches={result.llm_model_mismatches}")
    print(f"timing counts={timing.get('counts')}")
    print(f"timing milestones={timing.get('milestones')}")
    print(result.done_line or "(no DONE line)")


def _seconds(ms: int | None, speed: float) -> str:
    """Offset/wall at speed 1 (replayed sleeps scale with ``speed``)."""
    if ms is None:
        return "—"
    return f"{ms / 1000 / speed:6.1f}" if speed > 0 else f"{ms / 1000:6.3f}"


def _phase_end(timing: dict, *names: str) -> int | None:
    ends = [p["endMs"] for p in timing.get("phases", []) if p["name"] in names]
    return max(ends) if ends else None


def _print_groups(result: ReplayResult) -> None:
    """One line per split-extraction group call: start, TTFT, wall, output tokens."""
    timing = result.timing or {}
    calls = [c for c in timing.get("llmCalls", []) if is_group_feature(c.get("feature") or "")]
    if not calls:
        return
    speed = result.speed
    plan_done, frames_done = _phase_end(timing, "plan", "memory"), _phase_end(timing, "frames")
    print(
        f"plan-done {_seconds(plan_done, speed)} s · frames-done {_seconds(frames_done, speed)} s"
    )
    print(f"{'group call':<22} {'start s':>7} {'ttft s':>7} {'wall s':>7} {'out':>6}")
    for call in sorted(calls, key=lambda c: c["startMs"]):
        print(
            f"{call['span']:<22} {_seconds(call['startMs'], speed):>7} "
            f"{_seconds(call.get('ttftMs'), speed):>7} {_seconds(call['wallMs'], speed):>7} "
            f"{call['outputTokens']:>6}"
        )
    print(f"group output tokens Σ {sum(c['outputTokens'] for c in calls)}")


_TIMELINE_EVENTS = ("extraction_complete", "enrichment_complete", "complete")


def _tab_label(payload: dict) -> str:
    if payload.get("provisional"):
        return "provisional"
    return "final" if payload.get("final") else "non-final"


def _print_tab_timeline(result: ReplayResult) -> None:
    """Every ``tab_ready`` (id, position, kind) and the milestones between them, at speed 1."""
    print("tab timeline (s at speed 1):")
    for record in result.events:
        offset = _seconds(record.offset_ms, result.speed)
        if record.event == "tab_ready":
            payload = record.payload()
            label = _tab_label(payload)
            print(f"  {offset}  tab_ready {payload.get('id')} @{payload.get('position')} {label}")
        elif record.event in _TIMELINE_EVENTS:
            print(f"  {offset}  {record.event}")
    quiz = [c for c in (result.timing or {}).get("llmCalls", []) if c.get("span") == "enrichment"]
    for call in quiz:
        start, wall = (
            _seconds(call["startMs"], result.speed),
            _seconds(call["wallMs"], result.speed),
        )
        print(f"  quiz call start {start} s, wall {wall} s")


def _configure_logging(verbose: bool) -> None:
    """Pipeline logs only on ``--verbose`` (the summarizer installs its own handlers)."""
    logging.basicConfig(level=logging.INFO)
    if not verbose:
        for handler in logging.getLogger().handlers:
            handler.setLevel(logging.CRITICAL)


def _speed(value: str) -> float:
    try:
        return check_speed(float(value))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "replay").splitlines()[0])
    parser.add_argument("--video", required=True, choices=available_cassettes())
    parser.add_argument("--speed", type=_speed, default=1.0)
    parser.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE)
    parser.add_argument("--verbose", action="store_true", help="show pipeline logs")
    return parser.parse_args()


def _unused_by_design(problem: str, result: ReplayResult) -> bool:
    """The split run answers its groups from the single call, never requesting it."""
    return result.group_calls > 0 and problem == f"unused recording: {EXTRACTION_KEY.label()}"


def main() -> int:
    args = _parse_args()
    _configure_logging(args.verbose)
    cassette = load_cassette(args.video)
    overrides = {"EXTRACTION_PARALLEL_BATCHES": _PROD_PARALLEL_BATCHES}
    result = asyncio.run(run_replay(cassette, speed=args.speed, settings_overrides=overrides))
    print(f"EXTRACTION_PARALLEL={settings.EXTRACTION_PARALLEL}")
    _print_summary(result)
    _print_groups(result)
    _print_tab_timeline(result)
    rows = phase_rows(result, cassette)
    if args.speed > 0:
        print(format_table(rows))
    else:
        print("(phase walls are not comparable at --speed 0)")
    if cassette.recorded.estimated:
        print("note: recorded sub-steps are ESTIMATED for this cassette (see its notes)")
    problems = [p for p in divergences(result) if not _unused_by_design(p, result)]
    if args.speed > 0:
        problems += [
            f"phase outside tolerance: {n}" for n in out_of_tolerance(rows, args.tolerance)
        ]
    for problem in problems:
        print(f"FAIL {problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
