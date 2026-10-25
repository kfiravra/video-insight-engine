"""3.3 — a long video's chunk expects its share of each planned count (stage-audit L19).

Every chunk of a chunked run used to read the whole video's ``expect ~N`` and each
aimed at it: S01 (two chunks, 0–39:21 and 39:21–1:03:07) extracted 44 key ideas for
expect 16, S02 (three chunks) 60 for expect 20. A chunk's job now expects
``round(min(expect, cap) × share)`` (≥ 1), ``share`` = its window over the video's
clock, and its ``<batch_partial_context>`` says the counts are the part's share and the
caps hold per part. One chunk (share 1.0) keeps today's bytes; the
``EXTRACTION_PARALLEL=false`` chunked path keeps today's counts sentence (its
context differs only by 3.4's ``Clock:`` line).

The residual tail loss in assembly: an itinerary-style spot list with at least as
many sections as its cap kept the FIRST ``cap`` sections; it keeps an even pick now —
first and last — so a long video's closing section survives.
"""

from __future__ import annotations

import re

import pytest

from src.services.pipeline.assembly.core import _cap_spots_with_sections
from src.services.pipeline.extraction_chunks import ExtractionChunk, _batch_chunks
from src.services.pipeline.extraction_group_prompt import GroupJob, job_block, tabs_to_serve
from src.services.pipeline.extraction_prompt import render_tabs_to_serve
from src.services.pipeline.extractor import _build_batch_context
from src.services.pipeline.field_groups import ExtractionGroup
from src.services.pipeline.field_requests import FieldRequest
from src.services.transcript.render import format_marker
from src.services.transcription.transcript_chunker import ChapterChunk

# ─── Fixtures (S01 / S02: data/S0{1,2}/doc.json triage tabs, trace.json batch spans) ───

_S01_EDGES = (0, 2361, 3787)  # 0:00 – 39:21 – 1:03:07
_S02_EDGES = (0, 2216, 4271, 4978)  # 0:00 – 36:56 – 1:11:11 – 1:22:58

_KEY_IDEAS_TAB = {
    "id": "key_ideas",
    "label": "Key Ideas",
    "component": "spot_explorer",
    "dataSource": "learning.keyIdeas",
    "brief": {"what": "each claim with his why", "expect": 16},
}
_ASKS_TAB = {
    "id": "asks",
    "label": "What He Asked",
    "component": "checklist",
    "dataSource": "learning.takeaways",
    "brief": {"what": "only explicit asks", "expect": 4},
}
_MOMENTS_TAB = {
    "id": "moments",
    "label": "Moments",
    "component": "moment_track",
    "dataSource": "learning.timestamps",
    "brief": {"what": "one beat per five minutes", "expect": 12},
}
_TABS = [_KEY_IDEAS_TAB, _ASKS_TAB, _MOMENTS_TAB]
_EXPECT = re.compile(r"expect ~(\d+)")


def _field(tab: dict, cap: int) -> FieldRequest:
    domain, field = str(tab["dataSource"]).split(".", 1)
    return FieldRequest(domain, field, "list", "planned", (str(tab["id"]),), cap, 5, 100, False)


_TEXT1 = ExtractionGroup(
    "text1",
    (_field(_KEY_IDEAS_TAB, 32), _field(_ASKS_TAB, 12)),
    False,
    500,
    ("key_ideas", "asks"),
)
_JOB = GroupJob(_TABS, ["learning"], "learning")
# The bytes HEAD (34089cc8) renders for _TEXT1's tabs: a one-chunk run keeps them.
_TODAYS_TEXT1_LINES = (
    "Tabs you serve:\n"
    "- Key Ideas — spot_explorer ← learning.keyIdeas — what: each claim with his why"
    " — expect ~16, cap 32\n"
    "- What He Asked — checklist ← learning.takeaways — what: only explicit asks"
    " — expect ~4, cap 12\n"
)
_TEMPLATE = "<your_job>\n{batch_context}{emit_only}\nTabs you serve:\n{tabs_to_serve}\n</your_job>"


def _chunks(edges: tuple[int, ...]) -> list[ExtractionChunk]:
    """The run's chunks as ``plan_chunks`` cuts them: one chapter batch per window."""
    batches = [
        [
            ChapterChunk(
                index=k,
                title=f"part {k + 1}",
                start_seconds=float(start),
                end_seconds=float(end),
                text=f"{format_marker(start)} words",
                source="description",
                token_estimate=100,
            )
        ]
        for k, (start, end) in enumerate(zip(edges, edges[1:]))
    ]
    return _batch_chunks(batches)


def _expects(text: str) -> list[int]:
    return [int(n) for n in _EXPECT.findall(text)]


# ─── ExtractionChunk.share ───


class TestChunkShare:
    def test_should_give_the_whole_video_when_the_chunk_is_open(self) -> None:
        assert ExtractionChunk(0, "whole video").share == 1.0

    def test_should_give_the_whole_video_when_there_is_no_video_end(self) -> None:
        assert ExtractionChunk(1, "t", start_s=600.0, end_s=1200.0).share == 1.0

    def test_should_give_each_s01_window_its_part_of_the_clock(self) -> None:
        shares = [round(chunk.share, 2) for chunk in _chunks(_S01_EDGES)]

        assert shares == [0.62, 0.38]

    def test_should_add_the_s02_windows_up_to_the_whole_video(self) -> None:
        assert sum(chunk.share for chunk in _chunks(_S02_EDGES)) == pytest.approx(1.0)

    def test_should_never_exceed_the_whole_video_when_a_window_overruns_the_end(self) -> None:
        chunk = ExtractionChunk(1, "t", start_s=None, end_s=5000.0, video_end_s=4978.0)

        assert chunk.share == 1.0


# ─── The job's expected counts ───


class TestScaledExpect:
    def test_should_render_todays_bytes_when_the_share_is_the_whole_video(self) -> None:
        assert render_tabs_to_serve(_TABS, ["learning"], 1.0) == render_tabs_to_serve(
            _TABS, ["learning"]
        )

    def test_should_expect_the_chunks_share_of_the_count(self) -> None:
        line = render_tabs_to_serve([_KEY_IDEAS_TAB], ["learning"], 0.62)

        assert line.endswith("expect ~10, cap 32")

    def test_should_keep_the_cap_per_call_when_the_count_is_scaled(self) -> None:
        line = render_tabs_to_serve([_MOMENTS_TAB], ["learning"], 0.38)

        assert line.endswith("expect ~5, cap 60")

    def test_should_scale_the_count_after_holding_it_to_the_cap(self) -> None:
        tab = {**_ASKS_TAB, "brief": {"what": "asks", "expect": 40}}

        assert render_tabs_to_serve([tab], ["learning"], 0.5).endswith("expect ~6, cap 12")

    def test_should_expect_at_least_one_when_the_share_is_small(self) -> None:
        tab = {**_ASKS_TAB, "brief": {"what": "asks", "expect": 2}}

        assert render_tabs_to_serve([tab], ["learning"], 0.1).endswith("expect ~1, cap 12")

    def test_should_scale_a_count_whose_source_has_no_registry_entry(self) -> None:
        tab = {**_ASKS_TAB, "dataSource": "learning.notAField", "brief": {"expect": 10}}

        assert render_tabs_to_serve([tab], ["learning"], 0.3).endswith("expect ~3")

    def test_should_leave_an_object_field_without_a_count(self) -> None:
        tab = {**_ASKS_TAB, "dataSource": "review.verdict", "brief": {"expect": 1}}

        assert render_tabs_to_serve([tab], ["review"], 0.3).endswith("one object")

    def test_should_pass_the_share_through_the_groups_tab_lines(self) -> None:
        assert _expects(tabs_to_serve(_TEXT1, _JOB, 0.38)) == [6, 2]


class TestJobBlockPerChunk:
    def test_should_send_todays_tab_lines_for_the_one_chunk_of_a_short_video(self) -> None:
        block = job_block(_TEXT1, ExtractionChunk(0, "[0:00] hi"), _JOB, _TEMPLATE)

        assert _TODAYS_TEXT1_LINES in block["text"]

    @pytest.mark.parametrize("edges", [_S01_EDGES, _S02_EDGES], ids=["S01", "S02"])
    def test_should_sum_the_chunks_counts_to_the_whole_videos_within_one(
        self, edges: tuple[int, ...]
    ) -> None:
        per_chunk = [
            _expects(job_block(_TEXT1, chunk, _JOB, _TEMPLATE)["text"]) for chunk in _chunks(edges)
        ]

        totals = [sum(counts) for counts in zip(*per_chunk)]
        assert all(abs(total - whole) <= 1 for total, whole in zip(totals, [16, 4]))

    def test_should_expect_less_of_the_short_last_chunk_than_of_the_first(self) -> None:
        first, _, last = _chunks(_S02_EDGES)

        assert (
            _expects(job_block(_TEXT1, last, _JOB, _TEMPLATE)["text"])[0]
            < _expects(job_block(_TEXT1, first, _JOB, _TEMPLATE)["text"])[0]
        )


# ─── The batch context's counts sentence ───


def _chapter(start: float, end: float) -> ChapterChunk:
    return ChapterChunk(
        index=1,
        title="part",
        start_seconds=start,
        end_seconds=end,
        text=f"{format_marker(start)} words",
        source="description",
        token_estimate=100,
    )


class TestBatchContextCounts:
    def test_should_say_the_counts_are_the_parts_share_on_the_split_path(self) -> None:
        context = _chunks(_S01_EDGES)[1].batch_context

        assert "each expected count is this part's share and each cap holds per part" in context

    def test_should_keep_todays_sentence_on_the_flag_off_chunked_path(self) -> None:
        context = _build_batch_context(1, 2, [_chapter(2361, 3787)], 3787.0)

        assert "The briefs, expected counts and caps below describe the whole video:" in context

    def test_should_still_forbid_padding_when_the_counts_are_scaled(self) -> None:
        context = _build_batch_context(1, 2, [_chapter(2361, 3787)], 3787.0, counts_scaled=True)

        assert "never pad toward a count" in context

    def test_should_add_no_context_for_a_single_batch(self) -> None:
        assert _build_batch_context(0, 1, [_chapter(0, 600)], 600.0, counts_scaled=True) == ""


# ─── Assembly: more sections than the cap ───


def _one_spot_sections(n: int) -> dict:
    spots = [{"name": f"S{i}", "description": "d", "timestamp": i * 180} for i in range(n)]
    sections = [{"label": f"Part {i}", "spotIndices": [i]} for i in range(n)]
    return {"spots": spots, "sections": sections}


class TestSectionCapKeepsTheTail:
    def test_should_keep_the_last_section_when_sections_outnumber_the_cap(self) -> None:
        props = _one_spot_sections(30)

        _cap_spots_with_sections(props, props["spots"], 24)

        assert props["sections"][-1]["label"] == "Part 29"

    def test_should_keep_the_first_section_when_sections_outnumber_the_cap(self) -> None:
        props = _one_spot_sections(30)

        _cap_spots_with_sections(props, props["spots"], 24)

        assert props["sections"][0]["label"] == "Part 0"

    def test_should_ship_cap_sections_of_one_spot_each(self) -> None:
        props = _one_spot_sections(30)

        _cap_spots_with_sections(props, props["spots"], 24)

        assert [len(s["spotIndices"]) for s in props["sections"]] == [1] * 24

    def test_should_point_every_kept_section_at_its_own_spot(self) -> None:
        props = _one_spot_sections(30)

        _cap_spots_with_sections(props, props["spots"], 24)

        names = [props["spots"][s["spotIndices"][0]]["name"] for s in props["sections"]]
        assert names == [f"S{s['label'].split()[1]}" for s in props["sections"]]

    def test_should_keep_every_section_when_they_equal_the_cap(self) -> None:
        props = _one_spot_sections(24)

        _cap_spots_with_sections(props, props["spots"], 24)

        assert [s["label"] for s in props["sections"]] == [f"Part {i}" for i in range(24)]
