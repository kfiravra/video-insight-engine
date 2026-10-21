"""The hero rewrite in the benchmark replays (output-quality 2.2, D1).

With ``HERO_REWRITE_ENABLED`` (the default) every cassette makes ONE
``hero_rewrite`` call in place of the synthesis call — answered by
``hero_synth`` from the recorded memory + synthesis — and the stored doc carries
the hero's meta; with the flag off the run is today's: the synthesis call, no
hero call. Either way the stream uses only events and phase values the contract
already had.
"""

from __future__ import annotations

import asyncio
import re

import pytest

from src.config import settings
from tests.replay.cassette import Cassette, available_cassettes
from tests.replay.conftest import REFERENCE_VIDEO
from tests.replay.driver import ReplayResult, run_replay

_VIDEOS = available_cassettes()
_HERO_SPAN = "hero_rewrite"
_SYNTHESIS_SPAN = "synthesis"
_CLOSING_MARKER = re.compile(r" \[\d+:\d{2}(?::\d{2})?\]$")

pytestmark = pytest.mark.skipif(
    not settings.HERO_REWRITE_ENABLED, reason="the hero path (HERO_REWRITE_ENABLED=true)"
)


@pytest.fixture(scope="module")
def flag_off_reference(cassettes: dict[str, Cassette]) -> ReplayResult:
    """The reference cassette with the hero rewrite off — today's synthesis path."""
    cassette = cassettes[REFERENCE_VIDEO]
    overrides = {"HERO_REWRITE_ENABLED": False}
    return asyncio.run(run_replay(cassette, speed=0, settings_overrides=overrides))


def _spans(result: ReplayResult) -> list[str]:
    return [call["span"] for call in (result.timing or {}).get("llmCalls", [])]


def _meta(result: ReplayResult) -> dict:
    return (result.saved_result or {}).get("meta", {})


def _phase_values(result: ReplayResult) -> set[str]:
    return {r.payload().get("phase", "") for r in result.events if r.event == "phase"}


@pytest.mark.parametrize("video_id", _VIDEOS)
class TestHeroReplay:
    def test_should_make_one_hero_call_and_no_synthesis_call(
        self, replays: dict, video_id: str
    ) -> None:
        spans = _spans(replays[video_id])

        assert (spans.count(_HERO_SPAN), spans.count(_SYNTHESIS_SPAN)) == (1, 0)

    def test_should_store_the_brief_as_the_master_summary(
        self, replays: dict, video_id: str
    ) -> None:
        meta = _meta(replays[video_id])

        assert meta["brief"] and meta["masterSummary"] == meta["brief"]

    def test_should_store_both_lines(self, replays: dict, video_id: str) -> None:
        meta = _meta(replays[video_id])

        assert all(isinstance(meta.get(key), str) and meta[key] for key in ("worthItFor", "skipIf"))

    def test_should_store_every_takeaway_as_a_string_closing_with_its_marker(
        self, replays: dict, video_id: str
    ) -> None:
        takeaways = _meta(replays[video_id])["keyTakeaways"]

        assert takeaways and all(
            isinstance(t, str) and _CLOSING_MARKER.search(t) for t in takeaways
        )

    def test_should_send_the_stored_reading_minutes_on_the_last_superset(
        self, replays: dict, video_id: str
    ) -> None:
        result = replays[video_id]
        events = [r.payload() for r in result.events if r.event == "synthesis_complete"]

        assert events[-1].get("readingMinutes") == _meta(result)["readingMinutes"] >= 1


def test_flag_off_should_call_synthesis_and_never_the_hero(
    flag_off_reference: ReplayResult,
) -> None:
    spans = _spans(flag_off_reference)

    assert (spans.count(_HERO_SPAN), spans.count(_SYNTHESIS_SPAN)) == (0, 1)


def test_flag_off_should_store_no_hero_field(flag_off_reference: ReplayResult) -> None:
    meta = _meta(flag_off_reference)

    assert not {"brief", "worthItFor", "skipIf", "readingMinutes"} & set(meta)


def test_should_emit_no_event_the_flag_off_run_does_not(
    replays: dict, flag_off_reference: ReplayResult
) -> None:
    hero_run = replays[REFERENCE_VIDEO]

    assert set(hero_run.event_names()) <= set(flag_off_reference.event_names())


def test_should_emit_no_phase_value_the_flag_off_run_does_not(
    replays: dict, flag_off_reference: ReplayResult
) -> None:
    assert _phase_values(replays[REFERENCE_VIDEO]) <= _phase_values(flag_off_reference)
