"""Tests for the deferred hi-res pass (media/hires_upgrade.py) and its run wiring
(phases/frames.py): frames-done never waits for the 720p file (g4 §8)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.routes.pipeline_orchestration import _release_run_media
from src.services.media import hires_upgrade
from src.services.media.hires_refiner import HIRES_SUFFIX
from src.services.media.hires_upgrade import (
    HiresUpgrade,
    UpgradeFrame,
    deferred_upgrade,
    upgrade_frames,
)
from src.services.media.image_dedup import compute_ahash
from src.services.media.scene_extractor import mark_manifest_hires
from src.services.pipeline.phases import frames as frames_phase
from tests.replay.media_fakes import fake_jpeg

VIDEO_ID = "dQw4w9WgXcQ"


def _upgrade(seeds: tuple[int, ...] = (1, 2, 3)) -> HiresUpgrade:
    """Uploaded frames whose 360p images are fake_jpeg(seed) — what vision described."""
    return HiresUpgrade(
        VIDEO_ID,
        tuple(
            UpgradeFrame(
                i,
                float(10 * (i + 1)),
                f"videos/{VIDEO_ID}/scenes/scene_{i:04d}.jpg",
                compute_ahash(fake_jpeg(seed)),
            )
            for i, seed in enumerate(seeds)
        ),
    )


def _refiner(seeds: list[int]):
    """refine_selected_frames stand-in: writes one 720p JPEG per frame (by seed)."""

    async def refine(_video_id: str, frames: list[dict], _hires_video: object) -> int:
        for frame, seed in zip(frames, seeds, strict=False):
            hires = Path(f"{frame['path']}{HIRES_SUFFIX}")
            hires.write_bytes(fake_jpeg(seed))
            frame["path"] = str(hires)
        return min(len(frames), len(seeds))

    return refine


@pytest.fixture
def s3():
    with patch.object(hires_upgrade, "s3_client") as client:
        client.put_bytes = AsyncMock()
        yield client


@pytest.fixture
def mark():
    with patch("src.services.media.scene_extractor.mark_manifest_hires", AsyncMock()) as marker:
        yield marker


class TestUpgradeFrames:
    async def test_should_overwrite_each_frames_s3_object_with_its_720p_image(self, s3, mark):
        with patch.object(hires_upgrade, "refine_selected_frames", _refiner([1, 2, 3])):
            written = await upgrade_frames(_upgrade(), MagicMock())

        keys = sorted(call.args[0] for call in s3.put_bytes.await_args_list)
        assert (written, keys) == (3, [f.s3_key for f in _upgrade().frames])

    async def test_should_keep_the_360p_object_of_a_duplicate_720p_image(self, s3, mark):
        with patch.object(hires_upgrade, "refine_selected_frames", _refiner([7, 7, 8])):
            written = await upgrade_frames(_upgrade((7, 7, 8)), MagicMock())

        assert (written, s3.put_bytes.await_count) == (2, 2)

    async def test_should_keep_the_360p_object_when_the_seek_landed_on_another_shot(self, s3, mark):
        """int(timestamp) can seek to the shot before the cut vision described."""
        with patch.object(hires_upgrade, "refine_selected_frames", _refiner([1, 99, 3])):
            written = await upgrade_frames(_upgrade((1, 2, 3)), MagicMock())

        keys = sorted(call.args[0] for call in s3.put_bytes.await_args_list)
        assert (written, keys) == (2, [_upgrade().frames[0].s3_key, _upgrade().frames[2].s3_key])

    async def test_should_never_replace_a_frame_whose_360p_image_was_unreadable(self, s3, mark):
        upgrade = HiresUpgrade(VIDEO_ID, (UpgradeFrame(0, 10.0, "k0", None),))
        with patch.object(hires_upgrade, "refine_selected_frames", _refiner([1])):
            assert await upgrade_frames(upgrade, MagicMock()) == 0

    async def test_should_record_the_upgraded_count_on_the_manifest(self, s3, mark):
        with patch.object(hires_upgrade, "refine_selected_frames", _refiner([1, 2, 3])):
            await upgrade_frames(_upgrade(), MagicMock())

        mark.assert_awaited_once_with(VIDEO_ID, 3)

    async def test_should_leave_the_360p_frames_when_the_720p_file_never_lands(self, s3, mark):
        with patch.object(hires_upgrade, "refine_selected_frames", AsyncMock(return_value=0)):
            written = await upgrade_frames(_upgrade(), MagicMock())

        assert (written, s3.put_bytes.await_count, mark.await_count) == (0, 0, 0)

    async def test_should_count_only_the_frames_s3_accepted(self, s3, mark):
        s3.put_bytes.side_effect = [None, OSError("S3 down"), None]
        with patch.object(hires_upgrade, "refine_selected_frames", _refiner([1, 2, 3])):
            written = await upgrade_frames(_upgrade(), MagicMock())

        assert written == 2

    async def test_should_never_raise_when_refinement_fails(self, s3, mark):
        with patch.object(
            hires_upgrade, "refine_selected_frames", AsyncMock(side_effect=RuntimeError("boom"))
        ):
            assert await upgrade_frames(_upgrade(), MagicMock()) == 0

    async def test_should_remove_its_temp_dir(self, s3, mark, tmp_path, monkeypatch):
        made: list[str] = []
        real = hires_upgrade.tempfile.mkdtemp

        def mkdtemp(prefix: str) -> str:
            made.append(real(prefix=prefix, dir=tmp_path))
            return made[-1]

        monkeypatch.setattr(hires_upgrade.tempfile, "mkdtemp", mkdtemp)
        with patch.object(hires_upgrade, "refine_selected_frames", _refiner([1, 2, 3])):
            await upgrade_frames(_upgrade(), MagicMock())

        assert Path(made[0]).exists() is False


class TestDeferredUpgrade:
    async def test_should_take_every_uploaded_frame_with_an_s3_key(self, tmp_path):
        frame = tmp_path / "scene_0004.jpg"
        frame.write_bytes(fake_jpeg(4))
        uploaded = [
            {"index": 4, "timestamp": 12.5, "s3_key": "k4", "path": str(frame)},
            {"index": 5, "timestamp": 30.0, "path": str(frame)},
        ]

        upgrade = await deferred_upgrade(VIDEO_ID, uploaded)

        assert upgrade.frames == (UpgradeFrame(4, 12.5, "k4", compute_ahash(fake_jpeg(4))),)


class TestMarkManifestHires:
    async def test_should_keep_the_vision_descriptions(self):
        manifest = {"version": 2, "hiresCount": 0, "visionDescriptions": [{"frame_index": 0}]}
        with patch("src.services.media.scene_extractor.s3_client") as client:
            client.get_json = AsyncMock(return_value=manifest)
            client.put_json = AsyncMock()
            await mark_manifest_hires(VIDEO_ID, 9)

        assert client.put_json.await_args is not None
        written = client.put_json.await_args.args[1]
        assert (written["hiresCount"], written["visionDescriptions"]) == (9, [{"frame_index": 0}])


def _ctx(task: asyncio.Task[Any] | None = None) -> Any:
    return SimpleNamespace(youtube_id=VIDEO_ID, hires_video=MagicMock(), hires_upgrade=task)


class TestRunWiring:
    async def test_should_start_the_pass_in_the_background(self):
        ctx = _ctx()
        with patch.object(frames_phase, "upgrade_frames", AsyncMock(return_value=3)):
            frames_phase.start_hires_upgrade(ctx, _upgrade())
            assert await ctx.hires_upgrade == 3

    async def test_should_not_start_without_frames(self):
        ctx = _ctx()

        frames_phase.start_hires_upgrade(ctx, HiresUpgrade(VIDEO_ID, ()))

        assert ctx.hires_upgrade is None

    async def test_should_report_a_running_pass_as_pending(self):
        gate = asyncio.Event()
        ctx = _ctx(asyncio.create_task(gate.wait()))

        assert frames_phase.hires_upgrade_pending(ctx) is True
        gate.set()
        await frames_phase.finish_hires_upgrade(ctx, wait=True)

    async def test_should_let_a_finished_run_wait_for_the_pass(self):
        gate = asyncio.Event()
        task = asyncio.create_task(gate.wait())
        ctx = _ctx(task)
        asyncio.get_running_loop().call_later(0.01, gate.set)

        await frames_phase.finish_hires_upgrade(ctx, wait=True)

        assert (task.done(), task.cancelled(), ctx.hires_upgrade) == (True, False, None)

    async def test_should_cancel_the_pass_of_a_failed_run(self):
        task = asyncio.create_task(asyncio.sleep(60))
        ctx = _ctx(task)

        await frames_phase.finish_hires_upgrade(ctx, wait=False)

        assert task.cancelled() is True

    async def test_should_cancel_the_pass_when_the_run_itself_is_cancelled(self):
        task = asyncio.create_task(asyncio.sleep(60))
        waiter = asyncio.create_task(frames_phase.finish_hires_upgrade(_ctx(task), wait=True))
        await asyncio.sleep(0)

        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter

        assert task.cancelled() is True

    async def test_should_close_the_720p_file_only_after_the_pass_ended(self):
        order: list[str] = []
        gate = asyncio.Event()

        async def the_pass() -> int:
            await gate.wait()
            order.append("pass-ended")
            return 1

        ctx = _ctx(asyncio.create_task(the_pass()))
        ctx.lowres_video = None
        ctx.hires_video.close = AsyncMock(side_effect=lambda: order.append("720p-closed"))
        asyncio.get_running_loop().call_later(0.01, gate.set)
        with (
            patch("src.services.pipeline.phases.metadata.release_background_work"),
            patch("src.services.pipeline.phases.probe.cancel_tier_probe"),
        ):
            await _release_run_media(ctx, True)

        assert order == ["pass-ended", "720p-closed"]

    async def test_should_record_that_the_run_gave_up_on_the_pass(self, monkeypatch):
        monkeypatch.setattr(frames_phase, "HIRES_UPGRADE_WAIT_S", 0.01)
        ctx = _ctx(asyncio.create_task(asyncio.sleep(60)))
        ctx.hires_upgrade_abandoned = False

        await frames_phase.finish_hires_upgrade(ctx, wait=True)

        assert ctx.hires_upgrade_abandoned is True

    async def test_should_cancel_a_pass_that_outlives_the_wait(self, monkeypatch):
        monkeypatch.setattr(frames_phase, "HIRES_UPGRADE_WAIT_S", 0.01)
        task = asyncio.create_task(asyncio.sleep(60))

        await frames_phase.finish_hires_upgrade(_ctx(task), wait=True)

        assert task.cancelled() is True
