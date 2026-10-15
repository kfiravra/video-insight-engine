"""0b.10 — per-chunk timestamp range validation + hour-aware markers (R10, stage-audit L21).

A chunk's call may stamp an item only inside the chunk's window (a marker-interval
of grace at the edges). S02 (a 1:22:58 talk, three chunks) is the evidence: chunk 3
(``extraction_text3_c2``, window 1:11:11–1:22:58, markers ``[1:18:41]`` …) stamped
keyPoints 32–46 at 71–1300 s — the ``[h:mm:ss]`` markers read without their hour.
``window_stamps`` puts the hour back when that lands the stamp inside the window and
clears a stamp no hour fits; an item that IS a stamp (``learning.timestamps``) is
dropped instead. The one open chunk of a ≤ 40-min video is returned untouched, so
the replay fixtures stay byte-identical.

The stored S02 doc cannot be re-extracted ($0): the fixture below carries the 15
stamps as ``doc.json`` holds them and the chunk window as ``trace.json``'s
``<batch_partial_context>`` states it.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from llm_common.context import llm_feature_var

from src.services.pipeline import extraction_groups
from src.services.pipeline.extraction_chunks import (
    WINDOW_GRACE_S,
    ExtractionChunk,
    restamp,
    window_stamps,
)
from src.services.pipeline.extraction_group_prompt import GroupJob
from src.services.pipeline.extraction_groups import GroupCall, SplitRun, run_group_call
from src.services.pipeline.extractor import (
    _build_batch_context,
    _build_batch_transcript,
    _format_time,
)
from src.services.pipeline.field_groups import ExtractionGroup
from src.services.pipeline.field_requests import FieldRequest
from src.services.transcript.render import format_marker, marker_seconds
from src.services.transcription.transcript_chunker import ChapterChunk

# ─── S02 evidence (dev/active/output-quality/data/S02: doc.json + trace.json) ───

# trace.json, extraction_text3_c2 <batch_partial_context>: "BATCH 3 of 3 … (1:11:11–1:22:58)".
_S02_START = 4271  # 1:11:11
_S02_VIDEO_END = 4978  # 1:22:58
_S02_CHUNK = ExtractionChunk(2, "t", start_s=_S02_START, end_s=None, video_end_s=_S02_VIDEO_END)

# doc.json extraction.learning.keyPoints[32:47]: (stamp as stored, title).
_S02_KEY_POINTS = [
    (71, "Panic doesn't help — even with a real threat"),
    (121, "Remember how huge the upsides are"),
    (161, "Dismissing the risk is not a helpful action"),
    (203, "Upton Sinclair and the CEO problem"),
    (821, "The real choice: dismiss the risks or aim at shared goals"),
    (860, "Start with goals, not risks"),
    (900, "We need a broad, inclusive conversation about the future"),
    (961, "Build AGI that empowers us, not one that overpowers us"),
    (1020, "Machines doing it better doesn't mean we stop doing it"),
    (1041, "Consider hiring teachers and nurses before basic income"),
    (1086, 'AI is the 21st century\'s "going to the moon"'),
    (1141, "Humans are the Masters of Underestimation"),
    (1162, "The Cambridge, Massachusetts analogy"),
    (1262, "Why Elon Musk cares about AI safety"),
    (1300, "No technology means getting taken out by something dumb"),
]
# 71–203 s read as 1:01:11–1:03:23 with the hour back — still before 1:11:11: cleared.
_S02_EXPECTED = [None, None, None, None] + [s + 3600 for s, _ in _S02_KEY_POINTS[4:]]


def _s02_data() -> dict[str, Any]:
    points = [{"emoji": "", "title": t, "detail": "", "timestamp": s} for s, t in _S02_KEY_POINTS]
    return {"learning": {"keyPoints": points}}


# ─── Chunks ───

_OPEN = ExtractionChunk(0, "whole video")
_FIRST = ExtractionChunk(0, "t", start_s=None, end_s=2216.0, video_end_s=4978.0)
_MIDDLE = ExtractionChunk(1, "t", start_s=2216.0, end_s=4271.0, video_end_s=4978.0)


# ─── restamp (the window check) ───


class TestRestamp:
    def test_should_keep_a_stamp_inside_the_window(self) -> None:
        assert restamp(4500, _S02_CHUNK) == 4500

    def test_should_put_the_hour_back_when_that_lands_inside_the_window(self) -> None:
        assert restamp(821, _S02_CHUNK) == 4421

    def test_should_give_none_when_no_hour_fits(self) -> None:
        assert restamp(71, _S02_CHUNK) is None

    def test_should_cap_the_last_chunk_at_the_videos_end(self) -> None:
        # 71 + 2 h = 7271 is "after the start" of an open-ended window — but past the video.
        assert restamp(71, ExtractionChunk(2, "t", start_s=4271.0, video_end_s=4978.0)) is None

    def test_should_restore_two_hours_when_the_window_is_in_the_third_hour(self) -> None:
        chunk = ExtractionChunk(3, "t", start_s=7800.0, end_s=8400.0, video_end_s=9000.0)

        assert restamp(682, chunk) == 7882

    def test_should_keep_a_stamp_a_grace_interval_before_the_window(self) -> None:
        assert restamp(int(_S02_START - WINDOW_GRACE_S), _S02_CHUNK) == _S02_START - 30

    def test_should_not_keep_a_stamp_past_the_grace_before_the_window(self) -> None:
        assert restamp(int(_S02_START - WINDOW_GRACE_S - 1), _S02_CHUNK) is None

    def test_should_keep_a_stamp_a_grace_interval_after_the_window(self) -> None:
        assert restamp(2216 + 29, _FIRST) == 2245

    def test_should_clear_a_first_chunk_stamp_past_its_end(self) -> None:
        assert restamp(4421, _FIRST) is None

    def test_should_keep_a_middle_chunk_stamp_inside_its_window(self) -> None:
        assert restamp(3000, _MIDDLE) == 3000


# ─── window_stamps over a call's data ───


class TestWindowStampsS02:
    def test_should_restamp_or_clear_every_key_point_of_s02_chunk_3(self) -> None:
        placed, _ = window_stamps(_s02_data(), _S02_CHUNK)

        assert [p["timestamp"] for p in placed["learning"]["keyPoints"]] == _S02_EXPECTED

    def test_should_keep_every_key_point_with_its_title(self) -> None:
        placed, _ = window_stamps(_s02_data(), _S02_CHUNK)

        assert [p["title"] for p in placed["learning"]["keyPoints"]] == [
            t for _, t in _S02_KEY_POINTS
        ]

    def test_should_report_eleven_restamped_and_four_cleared(self) -> None:
        _, report = window_stamps(_s02_data(), _S02_CHUNK)

        assert (report.restamped, report.cleared, report.dropped) == (11, 4, {})

    def test_should_leave_the_input_untouched(self) -> None:
        data = _s02_data()

        window_stamps(data, _S02_CHUNK)

        assert [p["timestamp"] for p in data["learning"]["keyPoints"]] == [
            s for s, _ in _S02_KEY_POINTS
        ]

    def test_should_be_deterministic(self) -> None:
        first, _ = window_stamps(_s02_data(), _S02_CHUNK)
        second, _ = window_stamps(_s02_data(), _S02_CHUNK)

        assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


class TestWindowStampsOpenChunk:
    def test_should_return_the_same_object_for_the_one_open_chunk(self) -> None:
        data = _s02_data()

        placed, report = window_stamps(data, _OPEN)

        assert (placed is data, report.changed) == (True, False)

    def test_should_return_the_same_object_when_nothing_moved(self) -> None:
        data = {"learning": {"keyPoints": [{"title": "in", "timestamp": 4500}]}}

        placed, _ = window_stamps(data, _S02_CHUNK)

        assert placed is data


class TestWindowStampsTimestampItems:
    def test_should_drop_a_timestamps_item_no_clock_fits(self) -> None:
        data = {"learning": {"timestamps": [{"time": "1:11", "seconds": 71, "label": "x"}]}}

        placed, _ = window_stamps(data, _S02_CHUNK)

        assert placed["learning"]["timestamps"] == []

    def test_should_count_the_dropped_items_against_the_lists_size(self) -> None:
        items = [
            {"time": "1:11", "seconds": 71, "label": "x"},
            {"time": "1:13:41", "seconds": 4421, "label": "y"},
        ]

        _, report = window_stamps({"learning": {"timestamps": items}}, _S02_CHUNK)

        assert report.dropped == {"learning.timestamps": {"dropped": 1, "total": 2}}

    def test_should_restamp_seconds_from_the_items_clock_when_the_clock_is_inside(self) -> None:
        data = {"learning": {"timestamps": [{"time": "1:11:11", "seconds": 71, "label": "x"}]}}

        placed, _ = window_stamps(data, _S02_CHUNK)

        assert placed["learning"]["timestamps"][0]["seconds"] == 4271

    def test_should_rewrite_the_clock_as_h_mm_ss_when_the_hour_is_put_back(self) -> None:
        data = {"learning": {"timestamps": [{"time": "13:41", "seconds": 821, "label": "x"}]}}

        placed, _ = window_stamps(data, _S02_CHUNK)

        assert placed["learning"]["timestamps"][0] == {
            "time": "1:13:41",
            "seconds": 4421,
            "label": "x",
        }


class TestWindowStampsShapes:
    def test_should_place_an_end_timestamp_too(self) -> None:
        data = {"narrative": {"keyMoments": [{"timestamp": 821, "endTimestamp": 860}]}}

        placed, _ = window_stamps(data, _S02_CHUNK)

        assert placed["narrative"]["keyMoments"][0] == {"timestamp": 4421, "endTimestamp": 4460}

    def test_should_leave_a_missing_stamp_alone(self) -> None:
        data = {"learning": {"keyPoints": [{"title": "no stamp", "timestamp": None}]}}

        placed, _ = window_stamps(data, _S02_CHUNK)

        assert placed is data

    def test_should_leave_scalars_and_plain_lists_alone(self) -> None:
        data = {"learning": {"summary": "s", "takeaways": ["a", "b"], "keyQuestion": "q"}}

        placed, _ = window_stamps(data, _S02_CHUNK)

        assert placed is data

    def test_should_place_a_stamp_on_a_domain_scalar_object(self) -> None:
        data = {"review": {"verdict": {"text": "buy", "timestamp": 1300}}}

        placed, _ = window_stamps(data, _S02_CHUNK)

        assert placed["review"]["verdict"]["timestamp"] == 4900


# ─── Through run_group_call (the chunk's call → outcome) ───

_JOB = GroupJob([], ["learning"], "learning")


def _learning_call(field: str, chunk: ExtractionChunk) -> GroupCall:
    request = FieldRequest("learning", field, "list", "planned", (), 30, 5, 40, False)
    return GroupCall(ExtractionGroup("text3", (request,), False, 200, ()), chunk, _JOB)


def _run() -> SplitRun:
    return SplitRun(
        llm_service=MagicMock(),
        prompt=MagicMock(),
        job=_JOB,
        chunks=(_S02_CHUNK,),
        text_groups=(),
        content_tags=("learning",),
        modifiers=(),
    )


@pytest.fixture
def llm() -> Iterator[AsyncMock]:
    async def answer(*_args: Any, **_kwargs: Any) -> str:
        llm_feature_var.get()
        return answer.reply  # type: ignore[attr-defined]

    answer.reply = "{}"  # type: ignore[attr-defined]
    mock = AsyncMock(side_effect=answer)
    mock.answer = answer
    blocks = [{"type": "text", "text": block} for block in ("rules", "head", "job")]
    with (
        patch.object(extraction_groups, "call_llm_with_retry", mock),
        patch.object(extraction_groups, "build_group_messages", return_value=blocks),
    ):
        yield mock


class TestRunGroupCallWindow:
    async def test_should_land_s02_chunk_3_key_points_inside_its_window(
        self, llm: AsyncMock
    ) -> None:
        llm.answer.reply = json.dumps(_s02_data())

        outcome = await asyncio.create_task(
            run_group_call(_learning_call("keyPoints", _S02_CHUNK), _run())
        )

        assert [p["timestamp"] for p in (outcome.data or {})["learning"]["keyPoints"]] == (
            _S02_EXPECTED
        )

    async def test_should_report_a_dropped_timestamps_item_in_the_outcome(
        self, llm: AsyncMock
    ) -> None:
        items = [{"time": "1:11", "seconds": 71, "label": "x"}, {"seconds": 4421, "label": "y"}]
        llm.answer.reply = json.dumps({"learning": {"timestamps": items}})

        outcome = await asyncio.create_task(
            run_group_call(_learning_call("timestamps", _S02_CHUNK), _run())
        )

        assert outcome.validation.get("dropped") == {
            "learning.timestamps": {"dropped": 1, "total": 2}
        }

    async def test_should_leave_the_one_open_chunks_answer_as_validated(
        self, llm: AsyncMock
    ) -> None:
        llm.answer.reply = json.dumps(_s02_data())

        outcome = await asyncio.create_task(
            run_group_call(_learning_call("keyPoints", _OPEN), _run())
        )

        assert [p["timestamp"] for p in (outcome.data or {})["learning"]["keyPoints"]] == [
            s for s, _ in _S02_KEY_POINTS
        ]


# ─── Hour-aware marker rendering past 1:00:00 ───


def _chapter(start: float, end: float, index: int = 22) -> ChapterChunk:
    return ChapterChunk(
        index=index,
        title="Addressing AI risks",
        start_seconds=start,
        end_seconds=end,
        text=f"{format_marker(start)} panic doesn't help",
        source="description",
        token_estimate=100,
    )


class TestHourAwareMarkers:
    def test_should_render_a_time_past_one_hour_as_h_mm_ss(self) -> None:
        assert _format_time(4282) == "1:11:22"

    def test_should_never_render_minutes_past_59(self) -> None:
        assert "71:22" not in _format_time(4282)

    def test_should_render_a_marker_past_one_hour_as_h_mm_ss(self) -> None:
        assert format_marker(4282) == "[1:11:22]"

    def test_should_parse_an_h_mm_ss_marker_back(self) -> None:
        assert marker_seconds("[1:11:22] text") == [4282]

    def test_should_parse_a_minutes_only_marker_past_59_back_to_the_same_second(self) -> None:
        assert marker_seconds("[71:22] text") == [4282]

    def test_should_head_a_chapter_past_one_hour_with_the_h_mm_ss_clock(self) -> None:
        header = _build_batch_transcript([_chapter(4271, 4978)]).splitlines()[2]

        assert "(1:11:11 - 1:22:58)" in header

    def test_should_state_the_batch_span_past_one_hour_as_h_mm_ss(self) -> None:
        context = _build_batch_context(2, 3, [_chapter(4271, 4978)], 4978)

        assert "(1:11:11–1:22:58)" in context

    def test_should_put_the_chunks_first_marker_on_the_same_clock(self) -> None:
        chunk = _chapter(4271, 4978)

        assert marker_seconds(chunk.text) == [4271]
