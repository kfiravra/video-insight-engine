"""4.0 (where-limit) — on a chunked run an argument list's where-windows are hints, never a limit.

S15 (1XSmEfCe9-s, 71:22, chunks 0–40:00 and 40:00–1:11:22) re-run 2026-10-22: the plan's
key-ideas brief said "where 5:00–15:27, 24:21–33:00, 35:07–39:41, 40:40–45:23 — expect ~6"
in chunk 2's job, and the call kept 7 key ideas, all ≤ 45:01 (gate 3's windows ran to
1:09:20: 15 key ideas up to 1:09). A chunk's line for ``learning.keyIdeas`` (a talk's Key
Ideas, an interview's Positions) now asks for every one in the chunk's part and names the
plan's windows that touch the part only as where the planner expected most of them; its
count stays the chunk's share of the video by duration. Every other list, and a one-chunk
run, keeps today's bytes. Briefs are the stored plans' (``data/S15/doc.json``,
``data/S02/doc.json``); windows the traces' first markers.
"""

from __future__ import annotations

import pytest

from src.services.pipeline.extraction_chunks import ExtractionChunk
from src.services.pipeline.extraction_group_prompt import GroupJob, job_block, tabs_to_serve
from src.services.pipeline.extraction_prompt import render_tabs_to_serve
from src.services.pipeline.field_groups import ExtractionGroup
from src.services.pipeline.field_requests import FieldRequest
from src.services.pipeline.where_hints import PartWindow, part_window, where_part

# ─── Fixtures (S15 / S02 stored plans) ───

_S15_END = 4282.0  # 1:11:22
_S15_C0 = ExtractionChunk(0, "t", start_s=None, end_s=2402.0, video_end_s=_S15_END)
_S15_C1 = ExtractionChunk(1, "t", start_s=2402.0, end_s=None, video_end_s=_S15_END)
_ONE_CHUNK = ExtractionChunk(0, "[0:00] whole video")
_S15_WHERE = ["5:00-15:27", "24:21-33:00", "35:07-39:41", "40:40-45:23"]

_S15_KEY_IDEAS = {
    "id": "key_ideas",
    "label": "💡 Key Ideas",
    "component": "spot_explorer",
    "dataSource": "learning.keyIdeas",
    "brief": {
        "what": "each claim with its why and verbatim line, kind story or number: Potosí silver",
        "where": _S15_WHERE,
        "expect": 14,
    },
}
_S15_CLAIMS = {
    "id": "claims",
    "label": "Claims",
    "component": "claims_tracker",
    "dataSource": "learning.claims",
    "brief": {"what": "checkable claims", "where": ["53:20-1:08:40"], "expect": 6},
}
_S02_POSITIONS = {
    "id": "positions",
    "label": "💡 Positions",
    "component": "spot_explorer",
    "dataSource": "learning.keyIdeas",
    "brief": {
        "what": "each Tegmark position with why and verbatim line",
        "where": ["2:20-17:40", "20:00-30:00", "42:00-49:20", "58:40-1:05:00", "1:09:00-1:22:00"],
        "expect": 16,
    },
}
_KEY_IDEAS_HEAD = (
    "- 💡 Key Ideas — spot_explorer ← learning.keyIdeas — what: each claim with its why and "
    "verbatim line, kind story or number: Potosí silver — "
)
_TEMPLATE = "<your_job>\n{batch_context}{emit_only}\nTabs you serve:\n{tabs_to_serve}\n</your_job>"


def _group(tab: dict, cap: int) -> ExtractionGroup:
    domain, field = str(tab["dataSource"]).split(".", 1)
    request = FieldRequest(domain, field, "list", "planned", (str(tab["id"]),), cap, 5, 100, False)
    return ExtractionGroup("text2", (request,), False, 500, (str(tab["id"]),))


_KEY_IDEAS_GROUP = _group(_S15_KEY_IDEAS, 32)
_S15_JOB = GroupJob([_S15_KEY_IDEAS, _S15_CLAIMS], ["learning"], "learning")


def _tab_line(chunk: ExtractionChunk, group: ExtractionGroup = _KEY_IDEAS_GROUP) -> str:
    text = job_block(group, chunk, _S15_JOB, _TEMPLATE)["text"]
    return next(line for line in text.splitlines() if line.startswith("- "))


# ─── The brief line of a chunk ───


class TestChunkBriefLine:
    def test_should_ask_for_every_key_idea_in_the_chunk_when_the_plans_windows_end_early_in_it(
        self,
    ) -> None:
        assert _tab_line(_S15_C1) == (
            f"{_KEY_IDEAS_HEAD}every one in this part (the plan expected most at 40:40–45:23)"
            " — expect ~6, cap 32"
        )

    def test_should_keep_the_brief_byte_identical_when_the_run_has_one_chunk(self) -> None:
        assert _tab_line(_ONE_CHUNK) == (
            f"{_KEY_IDEAS_HEAD}where 5:00–15:27, 24:21–33:00, 35:07–39:41, 40:40–45:23"
            " — expect ~14, cap 32"
        )

    def test_should_name_only_the_windows_inside_the_first_chunk_as_hints(self) -> None:
        assert "(the plan expected most at 5:00–15:27, 24:21–33:00, 35:07–39:41)" in _tab_line(
            _S15_C0
        )

    def test_should_keep_the_windows_of_a_list_that_is_not_the_argument(self) -> None:
        line = _tab_line(_S15_C1, _group(_S15_CLAIMS, 20))

        assert line.endswith("— where 53:20–1:08:40 — expect ~3, cap 20")

    def test_should_ask_for_every_position_in_an_interviews_last_chunk(self) -> None:
        line = render_tabs_to_serve([_S02_POSITIONS], ["learning"], 0.14, PartWindow(4272.0, None))

        assert "— every one in this part (the plan expected most at 1:09:00–1:22:00) —" in line

    def test_should_keep_todays_lines_when_no_part_is_given(self) -> None:
        tabs = [_S15_KEY_IDEAS, _S15_CLAIMS]

        assert render_tabs_to_serve(tabs, ["learning"], 0.44, None) == render_tabs_to_serve(
            tabs, ["learning"], 0.44
        )

    def test_should_pass_the_chunks_window_through_the_groups_tab_lines(self) -> None:
        lines = tabs_to_serve(_KEY_IDEAS_GROUP, _S15_JOB, 0.44, PartWindow(2402.0, None))

        assert "every one in this part" in lines


# ─── The chunk's count ───


class TestChunkExpect:
    def test_should_not_shrink_a_chunks_key_idea_expect_to_the_windows_share(self) -> None:
        # The windows put 4:43 of their 28:23 in chunk 2 (~2 of 14); the chunk is 44 % of
        # the video's clock (~6 of 14).
        assert _tab_line(_S15_C1).endswith("expect ~6, cap 32")

    def test_should_keep_the_chunks_counts_summing_to_the_plans_count(self) -> None:
        counts = [int(_tab_line(c).split("expect ~")[1].split(",")[0]) for c in (_S15_C0, _S15_C1)]

        assert sum(counts) == 14


# ─── where_part ───


class TestWherePart:
    def test_should_render_nothing_when_the_brief_has_no_windows(self) -> None:
        assert where_part("learning.keyIdeas", [], PartWindow(2402.0, None)) == ""

    def test_should_ask_for_every_one_without_hints_when_no_window_touches_the_part(
        self,
    ) -> None:
        part = PartWindow(2402.0, None)  # every window ends by 39:41

        assert where_part("learning.keyIdeas", _S15_WHERE[:3], part) == "every one in this part"

    def test_should_name_a_window_that_straddles_the_parts_start(self) -> None:
        part = PartWindow(2361.0, None)  # S01 chunk 2 opens at 39:21

        assert where_part("learning.keyIdeas", ["33:00-47:00"], part) == (
            "every one in this part (the plan expected most at 33:00–47:00)"
        )

    def test_should_leave_out_a_window_that_opens_at_the_parts_end(self) -> None:
        part = PartWindow(None, 2440.0)

        assert where_part("learning.keyIdeas", ["40:40-45:23"], part) == "every one in this part"

    def test_should_name_a_lone_time_inside_the_part_as_a_hint(self) -> None:
        part = PartWindow(2402.0, None)

        assert where_part("learning.keyIdeas", ["50:00"], part) == (
            "every one in this part (the plan expected most at 50:00)"
        )

    @pytest.mark.parametrize("part", [None, PartWindow(2402.0, None)], ids=["one", "chunk"])
    def test_should_keep_todays_where_for_moments(self, part: PartWindow | None) -> None:
        assert where_part("learning.timestamps", ["5:00-50:00"], part) == "where 5:00–50:00"


class TestPartWindow:
    def test_should_give_no_part_when_the_window_is_open_at_both_ends(self) -> None:
        assert part_window(None, None) is None

    def test_should_give_the_first_chunk_a_part_open_at_its_start(self) -> None:
        assert part_window(None, 2402.0) == PartWindow(None, 2402.0)
