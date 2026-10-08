"""Frames phase: the visual tier is decided at Step 6b from the tier probe (1b.1).

Scene extraction awaits ``resolve_reselect`` just before Step 6b; the frames
phase answers it from the probe (≤ 3 s wait, ``resolve_tier``) or the metadata
rule. HIGH → over-select + vision reselect hook; STANDARD/LOW → none, and LOW
also skips the post-extraction vision pass.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from src.config import settings
from src.models.probe_types import TierProbe
from src.services.pipeline.phases import frames as frames_phase

PHASE = "src.services.pipeline.phases.frames"


def _probe(domain: str, visual_demo: bool = True) -> TierProbe:
    return TierProbe(domain=domain, format="tutorial", has_visual_demo=visual_demo, confidence=0.9)


def _done(result: TierProbe | None) -> asyncio.Future[TierProbe | None]:
    future: asyncio.Future[TierProbe | None] = asyncio.get_running_loop().create_future()
    future.set_result(result)
    return future


def _ctx(task: asyncio.Future[TierProbe | None] | None, category: str = "coding") -> Any:
    context = SimpleNamespace(category=category, display_tags=[])
    return SimpleNamespace(
        youtube_id="dQw4w9WgXcQ",
        video_data=SimpleNamespace(title="A video", duration=120, context=context),
        llm_service=SimpleNamespace(provider=MagicMock()),
        tier_probe_task=task,
        frame_descriptions=[],
        scene_frames_for_assembly=None,
        scene_frames_all=None,
        scene_frames_gallery=None,
        hires_video=None,
        lowres_video=None,
    )


class TestTierGate:
    async def test_should_over_select_with_a_vision_hook_when_the_probe_says_high(self) -> None:
        gate = frames_phase._TierGate(_ctx(_done(_probe("food"))))

        with patch.object(settings, "FRAME_VISION_ENABLED", True):
            overselect, hook = await gate.reselect()

        assert (gate.tier, overselect, hook is not None) == ("high", 40, True)

    async def test_should_skip_the_reselect_when_the_probe_says_standard(self) -> None:
        gate = frames_phase._TierGate(_ctx(_done(_probe("tech"))))

        assert (await gate.reselect(), gate.tier) == ((None, None), "standard")

    async def test_should_skip_the_reselect_when_vision_is_off(self) -> None:
        gate = frames_phase._TierGate(_ctx(_done(_probe("food"))))

        with patch.object(settings, "FRAME_VISION_ENABLED", False):
            assert await gate.reselect() == (None, None)

    async def test_should_let_the_probe_overrule_the_metadata_category(self) -> None:
        gate = frames_phase._TierGate(_ctx(_done(_probe("food", visual_demo=False)), "cooking"))

        await gate.reselect()

        assert gate.tier == "standard"

    async def test_should_use_the_metadata_rule_when_no_probe_was_started(self) -> None:
        gate = frames_phase._TierGate(_ctx(None, category="cooking"))

        await gate.reselect()

        assert gate.tier == "high"

    def test_should_stay_standard_until_step_6b_asks(self) -> None:
        assert frames_phase._TierGate(_ctx(None, category="cooking")).tier == "standard"


def _local_frames(tmp_path: Any) -> list[dict]:
    path = tmp_path / "scene_0000.jpg"
    path.write_bytes(b"jpg")
    return [{"index": 0, "path": str(path), "timestamp": 1.0, "temp_dir": str(tmp_path)}]


async def _run_frames(ctx: Any, tmp_path: Any, tier_enabled: bool = True) -> tuple[Any, AsyncMock]:
    """Drive run_phase_frames with an extractor that asks the Step 6b question."""
    frames = _local_frames(tmp_path)
    result = {"all_frames": frames, "selected_frames": frames, "gallery_frames": frames}
    asked: dict[str, Any] = {}

    async def _extract(*_args: object, resolve_reselect: Any = None, **_kwargs: object) -> dict:
        asked["resolver"] = resolve_reselect
        if resolve_reselect is not None:
            asked["answer"] = await resolve_reselect()
        return result

    vision = AsyncMock(return_value=[])
    with (
        patch(f"{PHASE}.settings") as mock_settings,
        patch(f"{PHASE}.extract_scene_keyframes", _extract),
        patch(f"{PHASE}.process_scene_frames", AsyncMock(return_value=(result, "e"))),
        patch(f"{PHASE}.persist_vision_descriptions", AsyncMock()),
        patch(f"{PHASE}.cleanup_temp_dir", AsyncMock()),
        patch("src.services.media.frame_analyzer.analyze_frames_with_vision", vision),
    ):
        mock_settings.SCENE_EXTRACTION_ENABLED = True
        mock_settings.FRAME_TIER_ENABLED = tier_enabled
        mock_settings.FRAME_VISION_ENABLED = True
        mock_settings.FRAME_VISION_MAX_FRAMES = 8
        mock_settings.FRAME_VISION_TIMEOUT = 5.0
        _ = [chunk async for chunk in frames_phase.run_phase_frames(ctx)]
    return asked, vision


class TestRunPhaseFrames:
    async def test_should_skip_vision_when_the_probe_says_low(self, tmp_path: Any) -> None:
        _asked, vision = await _run_frames(_ctx(_done(_probe("podcast", False))), tmp_path)

        vision.assert_not_awaited()

    async def test_should_run_vision_when_the_probe_says_standard(self, tmp_path: Any) -> None:
        _asked, vision = await _run_frames(_ctx(_done(_probe("tech"))), tmp_path)

        vision.assert_awaited_once()

    async def test_should_ask_no_step_6b_question_when_tiers_are_off(self, tmp_path: Any) -> None:
        asked, _vision = await _run_frames(_ctx(None), tmp_path, tier_enabled=False)

        assert asked["resolver"] is None
