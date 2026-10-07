"""Tests for the adaptive frame-effort tier (media/visual_tier.py)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from unittest.mock import patch

import pytest

from src.models.probe_types import TierProbe
from src.services.media import visual_tier
from src.services.media.visual_tier import (
    PROBE_WAIT_CAP_SECONDS,
    await_probe,
    derive_tier,
    metadata_tier,
    resolve_tier,
    tier_settings,
)

# The shipped table plus the reveal-format list the probe rule reads.
_CFG = {
    "highDomains": ["travel", "food", "fitness", "project", "sport"],
    "lowDomains": ["podcast", "news"],
    "highTitleKeywords": ["unboxing", "opening", "pulls", "box break", "haul"],
    "highFormats": ["unboxing"],
}


def _probe(domain: str, fmt: str = "commentary", visual: bool = False) -> TierProbe:
    return TierProbe(domain=domain, format=fmt, has_visual_demo=visual, confidence=0.9)


@pytest.fixture
def tier_table() -> Iterator[None]:
    with patch.object(visual_tier, "visual_criticality_config", return_value=_CFG):
        yield


class TestMetadataTier:
    def test_unboxing_title_is_high_regardless_of_category(self):
        assert metadata_tier(None, "ONE PIECE OP-17 UNBOXING!") == "high"
        assert metadata_tier("standard", "Massive Box Break tonight") == "high"

    def test_tag_keyword_hits_count(self):
        assert metadata_tier(None, "Ep 12", ["pack opening", "pulls"]) == "high"

    def test_high_domain_from_category(self):
        assert metadata_tier("cooking", "Perfect pasta") == "high"
        assert metadata_tier("travel", "My trip") == "high"

    def test_low_domain_without_keywords(self):
        assert metadata_tier("podcast", "Long chat") == "low"

    def test_low_domain_with_visual_keyword_stays_high(self):
        assert metadata_tier("podcast", "Podcast merch unboxing") == "high"

    def test_default_standard(self):
        assert metadata_tier(None, "Some lecture") == "standard"
        assert metadata_tier("coding", "Refactoring tips") == "standard"

    def test_missing_config_degrades_to_standard(self):
        with patch.object(visual_tier, "visual_criticality_config", return_value={}):
            assert metadata_tier("cooking", "unboxing time") == "standard"


class TestDeriveTierFromProbe:
    @pytest.mark.usefixtures("tier_table")
    @pytest.mark.parametrize(
        ("probe", "title", "expected"),
        [
            (_probe("food", "tutorial", True), "Birria tacos", "high"),
            (_probe("travel", "vlog", True), "10 days in Vietnam", "high"),
            (_probe("food", "documentary", False), "The history of pasta", "standard"),
            (_probe("travel", "commentary", False), "Why I stopped flying", "standard"),
            (_probe("gaming", "unboxing", True), "OP-17 booster box", "high"),
            (_probe("review", "unboxing", False), "New phone day", "high"),
            (_probe("gaming", "commentary", False), "OP-17 set verdict", "standard"),
            (_probe("tech", "tutorial", True), "React hooks in 10 minutes", "standard"),
            (_probe("science", "lecture", True), "How CRISPR works", "standard"),
            (_probe("podcast", "interview", False), "Long chat with a founder", "low"),
            (_probe("news", "news", False), "Election night recap", "low"),
            (_probe("news", "news", True), "Flood footage from the delta", "standard"),
            (_probe("podcast", "podcast", False), "Podcast merch unboxing", "high"),
            (_probe("learning", "lecture", False), "Pack OPENING stream", "high"),
        ],
    )
    def test_should_pick_tier_from_probe_and_title(
        self, probe: TierProbe, title: str, expected: str
    ) -> None:
        assert derive_tier(probe, title) == expected

    @pytest.mark.usefixtures("tier_table")
    def test_should_ignore_category_and_tags_when_the_probe_answered(self) -> None:
        tier = derive_tier(_probe("tech"), "Refactoring", ["box break"], category="cooking")
        assert tier == "standard"

    def test_should_degrade_to_standard_when_config_is_missing(self) -> None:
        with patch.object(visual_tier, "visual_criticality_config", return_value={}):
            assert derive_tier(_probe("food", "tutorial", True), "unboxing") == "standard"


class TestDeriveTierWithoutProbe:
    def test_should_apply_the_metadata_rule_when_probe_is_none(self) -> None:
        assert derive_tier(None, "Perfect pasta", category="cooking") == "high"

    def test_should_count_tag_keywords_in_the_fallback(self) -> None:
        assert derive_tier(None, "Ep 12", ["pack opening"]) == "high"

    def test_should_keep_the_pre_probe_call_shape_working(self) -> None:
        assert derive_tier("podcast", "Long chat") == "low"


async def _answer_after(seconds: float, probe: TierProbe | None) -> TierProbe | None:
    await asyncio.sleep(seconds)
    return probe


async def _failing_probe() -> TierProbe | None:
    raise RuntimeError("provider down")


@pytest.mark.usefixtures("tier_table")
class TestResolveTier:
    async def test_should_use_the_probe_when_it_lands_within_the_cap(self) -> None:
        task = asyncio.create_task(_answer_after(0.01, _probe("food", "tutorial", True)))
        tier = await resolve_tier(task, "Dinner", category="coding", cap_seconds=1.0)
        assert tier == "high"

    async def test_should_fall_back_to_metadata_when_the_probe_is_late(self) -> None:
        task = asyncio.create_task(_answer_after(1.0, _probe("food", "tutorial", True)))
        tier = await resolve_tier(task, "Dinner", category="coding", cap_seconds=0.05)
        task.cancel()
        assert tier == "standard"

    async def test_should_stop_waiting_at_the_cap(self) -> None:
        task = asyncio.create_task(_answer_after(5.0, None))
        started = time.monotonic()
        await resolve_tier(task, "Dinner", cap_seconds=0.05)
        elapsed = time.monotonic() - started
        task.cancel()
        assert elapsed < 0.5

    async def test_should_fall_back_to_metadata_when_the_probe_returned_none(self) -> None:
        task = asyncio.create_task(_answer_after(0.0, None))
        assert await resolve_tier(task, "Long chat", category="podcast") == "low"

    async def test_should_fall_back_to_metadata_when_the_probe_task_raised(self) -> None:
        task = asyncio.create_task(_failing_probe())
        assert await resolve_tier(task, "Perfect pasta", category="cooking") == "high"

    async def test_should_fall_back_to_metadata_when_no_probe_was_started(self) -> None:
        assert await resolve_tier(None, "Ep 12", tags=["pulls"]) == "high"

    async def test_should_default_the_cap_to_three_seconds(self) -> None:
        assert PROBE_WAIT_CAP_SECONDS == 3.0


class TestAwaitProbe:
    async def test_should_leave_a_late_probe_running_for_the_plan(self) -> None:
        answer = _probe("food", "tutorial", True)
        task = asyncio.create_task(_answer_after(0.1, answer))
        assert await await_probe(task, cap_seconds=0.01) is None
        assert await task == answer

    async def test_should_return_none_for_a_cancelled_probe(self) -> None:
        task = asyncio.create_task(_answer_after(1.0, None))
        task.cancel()
        assert await await_probe(task, cap_seconds=0.5) is None


class TestTierSettings:
    def test_high_knobs(self):
        knobs = tier_settings("high")
        assert knobs["overselect"] == 40
        assert knobs["visionMax"] == 40
        assert knobs["keep"] == 25

    def test_low_disables_vision(self):
        assert tier_settings("low")["visionMax"] == 0
