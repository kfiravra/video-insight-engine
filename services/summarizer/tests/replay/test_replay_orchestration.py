"""Phase-1 orchestration, timed on the shared 1/20-speed T1dQhQAm8Tc replay.

1a.1: the metadata phase is one ``extract_info`` + ``validate_duration``; the
caption fetch, description analysis and both video downloads start in the
background from there (the t=0 group) instead of serially inside it.
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
