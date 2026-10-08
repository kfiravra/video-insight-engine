"""Phase-1 orchestration, timed on the shared 1/20-speed T1dQhQAm8Tc replay.

1a.1: the metadata phase is one ``extract_info`` + ``validate_duration``; the
caption fetch, description analysis and both video downloads start in the
background from there (the t=0 group) instead of serially inside it.

1b: the text branch runs inside phase 2 — the tier probe at transcript-ready,
then plan ∥ memory from probe-done — so the readers no longer wait for frames.
"""

from __future__ import annotations

from tests.replay.conftest import SCALED_SPEED
from tests.replay.driver import ReplayResult

_METADATA_BUDGET_MS = 6000
_DESCRIPTION_SPAN = "description_analysis"


def _phase(result: ReplayResult, name: str) -> dict:
    return next(p for p in (result.timing or {}).get("phases", []) if p["name"] == name)


def _rescaled(ms: int) -> float:
    return ms / SCALED_SPEED


def test_metadata_phase_should_take_at_most_six_seconds(scaled_reference: ReplayResult) -> None:
    assert _rescaled(_phase(scaled_reference, "metadata")["wallMs"]) <= _METADATA_BUDGET_MS


def test_description_analysis_should_outlive_the_metadata_phase(
    scaled_reference: ReplayResult,
) -> None:
    """The description LLM call ends after metadata did — it is no longer awaited there."""
    call = next(
        c for c in (scaled_reference.timing or {})["llmCalls"] if c["span"] == _DESCRIPTION_SPAN
    )
    assert call["startMs"] + call["wallMs"] > _phase(scaled_reference, "metadata")["endMs"]


def test_downloads_should_start_when_metadata_ends(scaled_reference: ReplayResult) -> None:
    """Both video downloads start right after validate_duration, not inside frames."""
    metadata_end = _phase(scaled_reference, "metadata")["endMs"]
    starts = [d["startMs"] for d in (scaled_reference.timing or {})["downloads"]]
    assert len(starts) == 2 and all(abs(s - metadata_end) <= 50 for s in starts)


def _llm_call(result: ReplayResult, span: str) -> dict:
    return next(c for c in (result.timing or {})["llmCalls"] if c["span"] == span)


def test_plan_should_start_before_frames_are_done(scaled_reference: ReplayResult) -> None:
    assert _phase(scaled_reference, "plan")["startMs"] < _phase(scaled_reference, "frames")["endMs"]


def test_plan_and_memory_should_start_when_the_probe_answered(
    scaled_reference: ReplayResult,
) -> None:
    probe = _llm_call(scaled_reference, "tier_probe")
    probe_done = probe["startMs"] + probe["wallMs"]
    starts = [_phase(scaled_reference, name)["startMs"] for name in ("plan", "memory")]
    assert all(0 <= start - probe_done <= 50 for start in starts)


def test_memory_should_run_alongside_the_plan(scaled_reference: ReplayResult) -> None:
    plan, memory = _phase(scaled_reference, "plan"), _phase(scaled_reference, "memory")
    assert memory["startMs"] < plan["endMs"] and plan["startMs"] < memory["endMs"]


def test_extraction_should_wait_for_frames(scaled_reference: ReplayResult) -> None:
    """Phase 1 keeps ONE extraction call, fed the frame annotations (until 1c.2)."""
    frames_end = _phase(scaled_reference, "frames")["endMs"]
    assert _phase(scaled_reference, "extraction")["startMs"] >= frames_end


def test_hero_should_land_at_memory_done_before_frames(scaled_reference: ReplayResult) -> None:
    """1b.5: the first ``synthesis_complete`` (memory's tldr + takeaways) is the hero."""
    hero_ms = (scaled_reference.timing or {})["milestones"]["synthesisCompleteMs"]
    memory_end = _phase(scaled_reference, "memory")["endMs"]
    assert memory_end <= hero_ms < _phase(scaled_reference, "frames")["endMs"]


def test_synthesis_complete_should_arrive_twice(scaled_reference: ReplayResult) -> None:
    """Early {tldr, keyTakeaways} at memory-done, the full superset at synthesis-done."""
    assert scaled_reference.event_names().count("synthesis_complete") == 2
