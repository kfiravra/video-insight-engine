"""3.4 — a long video's chunk is told the clock its markers read (R10, stage-audit L21).

S02's last chunk (window 1:11:11–1:22:58, markers ``[1:18:41]`` …) stamped keyPoints
32–46 at 71–1300 s: the ``[h:mm:ss]`` markers read without their hour. 0b.10's
``window_stamps`` repairs that after the fact; the prompt now states it up front — one
``Clock:`` line in ``<batch_partial_context>`` naming the part's first marker in
seconds. It rides in the job (block 3) on both chunked paths, so the cached blocks 1 / 2
and every one-batch prompt (the ≤ 40-min replay) keep their bytes.

The vision call's frame labels read the same hour-aware clock (``format_clock``): a
frame at 4282 s was labelled ``71:22``, a clock no transcript marker uses.
"""

from __future__ import annotations

import re

from src.services.media.frame_analyzer import _frame_label
from src.services.pipeline.extraction_chunks import ExtractionChunk, _batch_chunks
from src.services.pipeline.extraction_group_prompt import GroupJob, build_group_messages
from src.services.pipeline.extraction_prompt import (
    ExtractionPromptInput,
    batch_clock_line,
    build_extraction_prompt,
)
from src.services.pipeline.extractor import _build_batch_context
from src.services.pipeline.field_groups import ExtractionGroup
from src.services.pipeline.field_requests import FieldRequest
from src.services.transcript.render import format_marker, marker_seconds
from src.services.transcription.transcript_chunker import ChapterChunk

# trace.json, S02 <batch_partial_context>: 0:00–36:56, 36:56–1:11:11, 1:11:11–1:22:58.
_S02_EDGES = (0, 2216, 4271, 4978)
_CLOCK = re.compile(r"^Clock: .*first marker \[([\d:]+)\] is (\d+)\.$", re.MULTILINE)


def _chapter(start: float, end: float, index: int = 0) -> ChapterChunk:
    return ChapterChunk(
        index=index,
        title=f"part {index + 1}",
        start_seconds=float(start),
        end_seconds=float(end),
        text=f"{format_marker(start)} words",
        source="description",
        token_estimate=100,
    )


def _s02_chunks() -> list[ExtractionChunk]:
    edges = list(zip(_S02_EDGES, _S02_EDGES[1:]))
    return _batch_chunks([[_chapter(a, b, k)] for k, (a, b) in enumerate(edges)])


def _clock(context: str) -> tuple[str, int]:
    found = _CLOCK.search(context)
    assert found is not None, context
    return found.group(1), int(found.group(2))


# ─── The clock line ───


class TestBatchClockLine:
    def test_should_name_the_first_marker_past_one_hour_in_seconds(self) -> None:
        assert _clock(batch_clock_line(4271)) == ("1:11:11", 4271)

    def test_should_name_a_first_marker_before_one_hour_in_m_ss(self) -> None:
        assert _clock(batch_clock_line(2216)) == ("36:56", 2216)

    def test_should_read_back_the_seconds_it_names_from_its_marker(self) -> None:
        marker, seconds = _clock(batch_clock_line(4271.9))

        assert marker_seconds(f"[{marker}]") == [seconds]

    def test_should_state_both_marker_clocks(self) -> None:
        assert "markers read [m:ss] before 1:00:00 and [h:mm:ss] after" in batch_clock_line(0)


class TestBatchContextClock:
    def test_should_state_the_clock_of_a_chunk_past_one_hour(self) -> None:
        context = _s02_chunks()[2].batch_context

        assert _clock(context) == ("1:11:11", 4271)

    def test_should_state_each_chunks_own_first_marker(self) -> None:
        firsts = [_clock(chunk.batch_context)[1] for chunk in _s02_chunks()]

        assert firsts == [0, 2216, 4271]

    def test_should_state_the_clock_on_the_flag_off_chunked_path(self) -> None:
        context = _build_batch_context(2, 3, [_chapter(4271, 4978)], 4978.0)

        assert _clock(context) == ("1:11:11", 4271)

    def test_should_read_the_first_marker_of_a_force_split_chunk_not_its_start(self) -> None:
        chunk = ChapterChunk(
            index=2,
            title="part",
            start_seconds=4260.0,
            end_seconds=4978.0,
            text="the end of a block [1:11:11] words",
            source="force_split",
            token_estimate=100,
        )

        assert _clock(_build_batch_context(2, 3, [chunk], 4978.0))[1] == 4271

    def test_should_add_nothing_for_a_single_batch(self) -> None:
        assert _build_batch_context(0, 1, [_chapter(0, 600)], 600.0) == ""

    def test_should_keep_the_clock_out_of_the_cached_blocks(self) -> None:
        tab = {"id": "k", "label": "Key", "component": "spot_explorer"}
        tab["dataSource"] = "learning.keyIdeas"
        field = FieldRequest("learning", "keyIdeas", "list", "planned", ("k",), 32, 5, 100, False)
        group = ExtractionGroup("text1", (field,), False, 500, ("k",))
        prompt = build_extraction_prompt(
            ExtractionPromptInput(["learning"], [], "rules", primary_tag="learning", tabs=[tab])
        )
        job = GroupJob([tab], ["learning"], "learning")

        blocks = build_group_messages(prompt, group, _s02_chunks()[2], job)

        assert ["Clock:" in block["text"] for block in blocks] == [False, False, True]


# ─── Frame labels (vision) ───


class TestFrameLabel:
    def test_should_label_a_frame_past_one_hour_on_the_h_mm_ss_clock(self) -> None:
        assert _frame_label(3, 4282) == "Frame 3 (at 1:11:22):"

    def test_should_label_a_frame_before_one_hour_as_today(self) -> None:
        assert _frame_label(0, 725) == "Frame 0 (at 12:05):"

    def test_should_floor_a_fractional_timestamp(self) -> None:
        assert _frame_label(1, 3599.9) == "Frame 1 (at 59:59):"

    def test_should_label_a_frame_at_the_hour_with_the_hour(self) -> None:
        assert _frame_label(2, 3600) == "Frame 2 (at 1:00:00):"
