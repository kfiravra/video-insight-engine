"""End-to-end replays of the benchmark cassettes through ``stream_summarization``.

Speed 0 runs every cassette in well under a second each (CI). The fidelity
test replays T1dQhQAm8Tc at 1/20 of its recorded walls (~12 s) and rescales;
set ``REPLAY_REALTIME=1`` to also run it at real speed (~4 min).
"""

from __future__ import annotations

import asyncio
import os
import re

import pytest

from src.services.pipeline import prompt_builder
from src.services.transcription import transcript_chunker
from tests.replay.cassette import Cassette, available_cassettes, load_cassette
from tests.replay.driver import ReplayResult, run_replay
from tests.replay.report import out_of_tolerance, phase_rows

_REFERENCE_VIDEO = "T1dQhQAm8Tc"
_FIDELITY_SPEED = 0.05
_TOP_LEVEL_PHASES = (
    "metadata",
    "transcript_frames",
    "visual_inject",
    "plan",
    "extraction",
    "synthesis_enrichment",
    "assembly",
)
_FRAMES_SUBSTEPS = ("frames.scene_detect", "frames.score_select", "frames.hires", "frames.upload")
_MILESTONES = ("metadataMs", "synthesisCompleteMs", "firstTabReadyMs", "completeMs", "doneMs")
_DONE_COUNTS = re.compile(r"tabs planned=(\d+) assembled=(\d+) emitted=(\d+)")
_VIDEOS = available_cassettes()


@pytest.fixture(scope="module")
def cassettes() -> dict[str, Cassette]:
    return {video_id: load_cassette(video_id) for video_id in _VIDEOS}


@pytest.fixture(scope="module")
def replays(cassettes: dict[str, Cassette]) -> dict[str, ReplayResult]:
    """One speed-0 replay per cassette, shared by the assertions below."""
    return {vid: asyncio.run(run_replay(c, speed=0)) for vid, c in cassettes.items()}


@pytest.mark.parametrize("video_id", _VIDEOS)
class TestReplayAtSpeedZero:
    def test_should_end_with_done_event(self, replays: dict, video_id: str) -> None:
        assert replays[video_id].event_names()[-2:] == ["done", "[DONE]"]

    def test_should_make_zero_network_attempts(self, replays: dict, video_id: str) -> None:
        assert replays[video_id].network_attempts == []

    def test_should_find_every_llm_call_in_cassette(self, replays: dict, video_id: str) -> None:
        assert replays[video_id].llm_misses == []

    def test_should_use_every_recorded_llm_call(self, replays: dict, video_id: str) -> None:
        assert replays[video_id].llm_unused == []

    def test_should_request_recorded_models(self, replays: dict, video_id: str) -> None:
        assert replays[video_id].llm_model_mismatches == []

    def test_should_assemble_recorded_tabs(
        self, replays: dict, cassettes: dict, video_id: str
    ) -> None:
        saved = replays[video_id].saved_result or {}
        assert [t["id"] for t in saved.get("tabs", [])] == cassettes[video_id].recorded.tab_ids

    def test_should_mark_row_completed(self, replays: dict, video_id: str) -> None:
        assert (replays[video_id].saved_result or {}).get("status") == "completed"

    def test_should_index_output_in_qdrant(self, replays: dict, video_id: str) -> None:
        assert replays[video_id].qdrant_stores == [video_id, video_id]


@pytest.mark.parametrize("video_id", _VIDEOS)
class TestPipelineTimingRecord:
    def test_should_persist_every_top_level_phase(self, replays: dict, video_id: str) -> None:
        names = set(replays[video_id].phase_walls())
        assert set(_TOP_LEVEL_PHASES) <= names

    def test_should_stamp_every_milestone(self, replays: dict, video_id: str) -> None:
        milestones = (replays[video_id].timing or {}).get("milestones", {})
        assert set(_MILESTONES) <= set(milestones)

    def test_should_record_one_llm_call_per_cassette_entry(
        self, replays: dict, cassettes: dict, video_id: str
    ) -> None:
        calls = (replays[video_id].timing or {}).get("llmCalls", [])
        assert len(calls) == len(cassettes[video_id].llm)

    def test_should_record_lowres_download(self, replays: dict, video_id: str) -> None:
        downloads = (replays[video_id].timing or {}).get("downloads", [])
        assert [d["kind"] for d in downloads if d["purpose"] == "scene_detect"] == ["lowres"]

    def test_should_count_tabs_as_assembled(
        self, replays: dict, cassettes: dict, video_id: str
    ) -> None:
        counts = (replays[video_id].timing or {}).get("counts", {})
        expected = len(cassettes[video_id].recorded.tab_ids)
        assert (counts.get("tabsAssembled"), counts.get("tabsEmitted")) == (expected, expected)


@pytest.mark.parametrize("video_id", _VIDEOS)
def test_done_line_should_carry_planned_assembled_emitted(
    replays: dict, cassettes: dict, video_id: str
) -> None:
    match = _DONE_COUNTS.search(replays[video_id].done_line or "")
    expected = len(cassettes[video_id].recorded.tab_ids)
    assert match is not None and (int(match[2]), int(match[3])) == (expected, expected)


def test_frames_substeps_should_be_timed_for_standard_tier(replays: dict) -> None:
    names = set(replays[_REFERENCE_VIDEO].phase_walls())
    assert set(_FRAMES_SUBSTEPS) <= names


def test_vision_reselect_should_be_timed_for_high_tier(replays: dict) -> None:
    assert "frames.vision_reselect" in replays["jMq8lEu-of0"].phase_walls()


async def test_reference_replay_should_reproduce_phase_walls_within_ten_percent(
    cassettes: dict,
) -> None:
    cassette = cassettes[_REFERENCE_VIDEO]
    result = await run_replay(cassette, speed=_FIDELITY_SPEED)
    assert out_of_tolerance(phase_rows(result, cassette)) == []


async def test_replay_should_leave_prompt_cache_as_found(cassettes: dict) -> None:
    before = prompt_builder._read_file_cached.cache_info().currsize
    await run_replay(cassettes[_REFERENCE_VIDEO], speed=0)
    assert prompt_builder._read_file_cached.cache_info().currsize == before


async def test_replay_should_leave_lazy_module_globals_as_found(cassettes: dict) -> None:
    before = transcript_chunker._CHAPTER_DETECT_PROMPT
    await run_replay(cassettes[_REFERENCE_VIDEO], speed=0)
    assert transcript_chunker._CHAPTER_DETECT_PROMPT is before


@pytest.mark.skipif(not os.environ.get("REPLAY_REALTIME"), reason="set REPLAY_REALTIME=1 (~4 min)")
async def test_reference_replay_should_reproduce_phase_walls_in_real_time(
    cassettes: dict,
) -> None:
    cassette = cassettes[_REFERENCE_VIDEO]
    result = await run_replay(cassette, speed=1.0)
    assert out_of_tolerance(phase_rows(result, cassette)) == []
