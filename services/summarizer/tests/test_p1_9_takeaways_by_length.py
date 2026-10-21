"""Kfir 2026-10-21: the hero carries the top 3 / 5 / 7 claims by video length.

content-model §9 sizing table — up to 15 min → 3, up to 45 min → 5, longer → 7.
The memory stage writes the hero preview, so it asks for that many and keeps that many.
"""

from __future__ import annotations

import pytest

from src.models.memory_types import MemoryInput
from src.services.pipeline.memory import build_memory_prompt, repair_takeaways, takeaway_count


def _input(duration: int) -> MemoryInput:
    return MemoryInput(
        title="t", channel="c", description="d", duration=duration, transcript="[0:01] hi"
    )


class TestTakeawayCount:
    @pytest.mark.parametrize(
        ("duration", "count"), [(600, 3), (900, 3), (1001, 5), (2700, 5), (2701, 7), (3787, 7)]
    )
    def test_should_follow_the_sizing_table_when_the_length_is_known(
        self, duration: int, count: int
    ) -> None:
        assert takeaway_count(duration) == count

    def test_should_ask_for_five_when_the_length_is_unknown(self) -> None:
        assert takeaway_count(None) == 5


class TestPromptAsksForTheCount:
    def test_should_ask_for_seven_claims_when_the_video_runs_an_hour(self) -> None:
        assert "the 7 claims that matter most" in build_memory_prompt(_input(3787))

    def test_should_ask_for_three_claims_when_the_video_is_short(self) -> None:
        assert "the 3 claims that matter most" in build_memory_prompt(_input(600))

    def test_should_leave_no_takeaway_placeholder_when_rendered(self) -> None:
        assert "{takeaway_count}" not in build_memory_prompt(_input(1800))


class TestRepairKeepsTheCount:
    def test_should_keep_seven_when_an_hour_long_video_returns_eight(self) -> None:
        takeaways = repair_takeaways([f"claim {i} [1:0{i}]" for i in range(8)], 3787)

        assert len(takeaways) == 7

    def test_should_keep_three_when_a_short_video_returns_five(self) -> None:
        takeaways = repair_takeaways([f"claim {i} [0:1{i}]" for i in range(5)], 600)

        assert len(takeaways) == 3
