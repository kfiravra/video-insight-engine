"""Frames phase: vision descriptions survive manifest cache hits.

A manifest cache hit returns frames that exist only in S3 (no local ``path``),
so the vision pass cannot run again. The phase must (1) restore the
descriptions persisted by the fresh run, (2) not burn a vision call on
path-less frames, and (3) persist descriptions after a fresh run so the next
hit has them.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.pipeline.phases import frames as frames_phase

PHASE = "src.services.pipeline.phases.frames"


def _ctx() -> SimpleNamespace:
    video_data = SimpleNamespace(title="t", duration=120, context=None)
    return SimpleNamespace(
        youtube_id="dQw4w9WgXcQ",
        video_data=video_data,
        llm_service=SimpleNamespace(provider=MagicMock()),
        frame_descriptions=[],
        scene_frames_for_assembly=None,
        scene_frames_all=None,
        scene_frames_gallery=None,
        hires_video=None,
    )


def _cached_frames(count: int = 3) -> list[dict]:
    # Manifest hit shape: s3-only, no local path / temp_dir.
    return [
        {"index": i, "s3_key": f"k{i}", "s3_url": f"https://s3/k{i}", "timestamp": float(i * 10)}
        for i in range(count)
    ]


def _local_frames(tmp_path, count: int = 3) -> list[dict]:
    frames = []
    for i in range(count):
        path = tmp_path / f"scene_{i:04d}.jpg"
        path.write_bytes(b"jpg")
        frames.append(
            {"index": i, "path": str(path), "timestamp": float(i * 10), "temp_dir": str(tmp_path)}
        )
    return frames


async def _run(ctx) -> list[str]:
    return [chunk async for chunk in frames_phase.run_phase_frames(ctx)]


def _common_patches(extraction_result: dict):
    ocr_result = (extraction_result, "event: frames\\n\\n", None)
    return (
        patch(f"{PHASE}.settings"),
        patch(f"{PHASE}.extract_scene_keyframes", AsyncMock(return_value=extraction_result)),
        patch(f"{PHASE}.process_scene_frames", AsyncMock(return_value=ocr_result)),
        patch(f"{PHASE}.persist_vision_descriptions", AsyncMock()),
        patch(f"{PHASE}.cleanup_temp_dir", AsyncMock()),
        patch(
            "src.services.media.frame_analyzer.analyze_frames_with_vision",
            AsyncMock(return_value=[{"original_index": 0, "scene_type": "slide"}]),
        ),
    )


def _configure(settings) -> None:
    settings.SCENE_EXTRACTION_ENABLED = True
    settings.FRAME_TIER_ENABLED = False
    settings.FRAME_VISION_ENABLED = True
    settings.FRAME_VISION_MAX_FRAMES = 8
    settings.FRAME_VISION_TIMEOUT = 5.0


async def test_cache_hit_restores_descriptions_and_skips_vision():
    cached = _cached_frames()
    persisted = [{"original_index": 1, "scene_type": "demo", "content": "knife skills"}]
    result = {
        "all_frames": cached,
        "selected_frames": cached,
        "gallery_frames": cached,
        "vision_descriptions": persisted,
    }
    ctx = _ctx()
    s, extract, ocr, persist, cleanup, vision = _common_patches(result)
    with s as settings, extract, ocr, persist as mock_persist, cleanup, vision as mock_vision:
        _configure(settings)
        await _run(ctx)

    assert ctx.frame_descriptions == persisted
    mock_vision.assert_not_awaited()  # nothing local to analyze
    mock_persist.assert_not_awaited()  # already persisted — no rewrite


async def test_cache_hit_without_descriptions_does_not_call_vision():
    cached = _cached_frames()
    result = {"all_frames": cached, "selected_frames": cached, "gallery_frames": cached}
    ctx = _ctx()
    s, extract, ocr, persist, cleanup, vision = _common_patches(result)
    with s as settings, extract, ocr, persist as mock_persist, cleanup, vision as mock_vision:
        _configure(settings)
        await _run(ctx)

    assert ctx.frame_descriptions == []
    mock_vision.assert_not_awaited()
    mock_persist.assert_not_awaited()


async def test_fresh_run_persists_descriptions(tmp_path):
    local = _local_frames(tmp_path)
    result = {"all_frames": local, "selected_frames": local, "gallery_frames": local}
    ctx = _ctx()
    s, extract, ocr, persist, cleanup, vision = _common_patches(result)
    with s as settings, extract, ocr, persist as mock_persist, cleanup, vision as mock_vision:
        _configure(settings)
        await _run(ctx)

    mock_vision.assert_awaited_once()
    assert ctx.frame_descriptions == [{"original_index": 0, "scene_type": "slide"}]
    mock_persist.assert_awaited_once_with("dQw4w9WgXcQ", ctx.frame_descriptions)
