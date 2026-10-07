"""Tests for scene detection with the zero-candidate ladder (scene_detect)."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import patch

import pytest

from src.services.media import scene_detect
from src.services.media.scene_detect import (
    LADDER_FLOOR,
    detect_candidate_frames,
    seek_frames,
    uniform_timestamps,
)

_EMPTY_STDERR = "[out#0/image2] Nothing was written into output file, because ...\n"
_SCORE_FILE_RE = re.compile(r"metadata=print:[^,]*?file=([^,:]+)")


class _Proc:
    """The slice of ``asyncio.subprocess.Process`` scene_detect uses."""

    def __init__(self, returncode: int, stderr: str, on_done: Callable[[], object]) -> None:
        self._final = returncode
        self._stderr = stderr.encode()
        self._on_done = on_done
        self.returncode: int | None = None

    async def communicate(self) -> tuple[bytes, bytes]:
        self._on_done()
        self.returncode = self._final
        return b"", self._stderr

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode or -9


@dataclass
class FakeFfmpeg:
    """Scene pass: ``scene`` timestamps written as rung-1 JPEGs, ``floor`` to the side file."""

    scene: list[float] = field(default_factory=list)
    floor: list[float] = field(default_factory=list)
    scene_rc: int | None = None
    scene_timeout: bool = False
    failing_seeks: set[float] = field(default_factory=set)
    seeks: list[float] = field(default_factory=list)
    scene_filters: list[str] = field(default_factory=list)

    async def exec(self, *argv: str, **_: object) -> _Proc:
        if "-ss" in argv:
            return self._seek(list(argv))
        return self._scene_pass(list(argv))

    def _seek(self, argv: list[str]) -> _Proc:
        ts = float(argv[argv.index("-ss") + 1])
        self.seeks.append(ts)
        ok = ts not in self.failing_seeks
        out = Path(argv[-1])
        return _Proc(0 if ok else 234, "", lambda: out.write_bytes(b"jpg") if ok else None)

    def _scene_pass(self, argv: list[str]) -> _Proc:
        video_filter = argv[argv.index("-vf") + 1]
        self.scene_filters.append(video_filter)
        frames_dir = Path(argv[-4]).parent
        match = _SCORE_FILE_RE.search(video_filter)

        def write() -> None:
            if self.scene_timeout:
                raise asyncio.TimeoutError
            for i, _ in enumerate(self.scene):
                (frames_dir / f"scene_{i + 1:04d}.jpg").write_bytes(b"jpg")
            if match:
                Path(match.group(1)).write_text(
                    "".join(f"frame:{i} pts:{i} pts_time:{t}\n" for i, t in enumerate(self.floor))
                )

        stderr = "".join(f"n:{i} pts_time:{t}\n" for i, t in enumerate(self.scene))
        rc = self.scene_rc if self.scene_rc is not None else (0 if self.scene else 234)
        return _Proc(rc, stderr or _EMPTY_STDERR, write)


@pytest.fixture
def frames_dir(tmp_path: Path) -> Path:
    path = tmp_path / "frames"
    path.mkdir()
    return path


async def _detect(fake: FakeFfmpeg, frames_dir: Path, duration: int | None) -> list[dict]:
    with patch("asyncio.create_subprocess_exec", side_effect=fake.exec):
        return await detect_candidate_frames(
            frames_dir.parent / "v.mp4",
            frames_dir,
            duration_seconds=duration,
            temp_dir=str(frames_dir.parent),
            threshold=0.3,
        )


def _stamps(frames: list[dict]) -> list[float]:
    return [f["timestamp"] for f in frames]


class TestZeroCandidateLadder:
    async def test_should_keep_scene_frames_when_detection_finds_enough(self, frames_dir):
        fake = FakeFfmpeg(scene=[float(t) for t in range(10, 140, 10)])

        frames = await _detect(fake, frames_dir, duration=600)

        assert (_stamps(frames), fake.seeks) == (fake.scene, [])

    async def test_should_seek_floor_rung_when_threshold_finds_nothing(self, frames_dir):
        fake = FakeFfmpeg(floor=[12.5, 40.0, 75.25])

        frames = await _detect(fake, frames_dir, duration=100)

        assert _stamps(frames) == [12.5, 40.0, 75.25]

    async def test_should_sample_uniformly_when_both_rungs_are_empty(self, frames_dir):
        fake = FakeFfmpeg()

        frames = await _detect(fake, frames_dir, duration=1127)

        assert _stamps(frames) == uniform_timestamps(1127)

    async def test_should_sample_uniformly_when_detection_times_out(self, frames_dir):
        fake = FakeFfmpeg(scene=[5.0, 9.0], scene_timeout=True)

        frames = await _detect(fake, frames_dir, duration=300)

        assert _stamps(frames) == uniform_timestamps(300)

    async def test_should_discard_frames_of_a_failed_detection_run(self, frames_dir):
        fake = FakeFfmpeg(scene=[5.0, 9.0], floor=[5.0, 7.0, 9.0], scene_rc=1)

        frames = await _detect(fake, frames_dir, duration=300)

        assert _stamps(frames) == uniform_timestamps(300)

    async def test_should_treat_empty_output_exit_as_zero_candidates(self, frames_dir):
        fake = FakeFfmpeg(floor=[30.0], scene_rc=234)

        frames = await _detect(fake, frames_dir, duration=90)

        assert _stamps(frames) == [30.0]

    async def test_should_supplement_sparse_detection_on_long_videos(self, frames_dir):
        fake = FakeFfmpeg(scene=[100.0, 200.0])

        frames = await _detect(fake, frames_dir, duration=600)

        assert len(frames) == 2 + len(uniform_timestamps(600))

    async def test_should_not_supplement_sparse_detection_on_short_videos(self, frames_dir):
        fake = FakeFfmpeg(scene=[20.0, 50.0])

        frames = await _detect(fake, frames_dir, duration=100)

        assert _stamps(frames) == [20.0, 50.0]

    async def test_should_keep_frame_indexes_unique_across_rungs(self, frames_dir):
        fake = FakeFfmpeg(floor=[100.0, 400.0])

        frames = await _detect(fake, frames_dir, duration=600)

        indexes = [f["index"] for f in frames]
        assert len(indexes) == len(set(indexes))

    async def test_should_return_nothing_when_ffmpeg_is_missing(self, frames_dir):
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
            frames = await detect_candidate_frames(
                frames_dir.parent / "v.mp4",
                frames_dir,
                duration_seconds=600,
                temp_dir=str(frames_dir.parent),
                threshold=0.3,
            )

        assert frames == []

    async def test_should_detect_both_rungs_in_one_decode(self, frames_dir):
        fake = FakeFfmpeg(floor=[12.0])

        await _detect(fake, frames_dir, duration=100)

        assert len(fake.scene_filters) == 1


class TestSceneFilter:
    def test_should_write_only_threshold_frames_and_log_floor_scores(self, tmp_path):
        vf = scene_detect._scene_filter(0.3, tmp_path / "s.txt")

        assert vf.startswith(f"select='gt(scene,{LADDER_FLOOR:.4f})'") and (
            "metadata=select:key=lavfi.scene_score:value=0.3000:function=greater" in vf
        )

    def test_should_skip_floor_rung_when_threshold_is_already_below_it(self, tmp_path):
        vf = scene_detect._scene_filter(0.1, tmp_path / "s.txt")

        assert "metadata" not in vf


class TestUniformTimestamps:
    def test_should_cap_long_videos_at_thirty_samples(self):
        assert len(uniform_timestamps(1127)) == 30

    def test_should_never_sample_the_first_frame(self):
        assert min(uniform_timestamps(1127)) > 0

    def test_should_space_short_videos_at_least_fifteen_seconds_apart(self):
        assert uniform_timestamps(60) == [7.0, 22.0, 37.0, 52.0]

    def test_should_sample_whole_seconds_for_the_hires_refiner(self):
        assert all(t == int(t) for t in uniform_timestamps(1001))


class TestSeekFrames:
    async def test_should_drop_failed_seeks_and_number_from_the_offset(self, frames_dir):
        fake = FakeFfmpeg(failing_seeks={20.0})

        with patch("asyncio.create_subprocess_exec", side_effect=fake.exec):
            frames = await seek_frames(
                frames_dir.parent / "v.mp4",
                frames_dir,
                [30.0, 10.0, 20.0],
                prefix="interval",
                index_offset=5,
                temp_dir="t",
            )

        assert [(f["index"], f["timestamp"]) for f in frames] == [(6, 10.0), (5, 30.0)]
