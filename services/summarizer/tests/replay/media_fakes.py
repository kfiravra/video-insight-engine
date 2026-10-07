"""Fakes for the media I/O primitives, under the REAL frame orchestration.

Scene extraction (``scene_extractor``), the 720p prefetch/refiner
(``hires_prefetch``/``hires_refiner``/``local_video``), the moment-frame fill
and the tier decision run as production code. Only what they call out to is
faked here:

* ``asyncio.create_subprocess_exec`` — yt-dlp downloads (low-res pass 1 and
  every 720p download, sized files at the recorded size), ``--get-url``
  lookups (refused and reported: the stream-URL pass is gone), ffmpeg scene
  detection (recorded frames + ``pts_time`` lines + the ladder's score side
  file; a ``zero`` cassette exits like ffmpeg 7 on an empty output), ffmpeg
  ``-ss`` seeks (distinct decodable JPEGs). Anything else is refused and
  reported.
* ``S3Client`` — an in-memory store (manifests, frame PUTs, presigned URLs).
* ``frame_scorer.score_all_frames``/``select_frames`` — CPU-bound scoring:
  recorded scores and the recorded local selection.

Every download goes through the real ``record_download`` call sites, so
``pipeline.timing.downloads`` shows what the code under test really fetched.
"""

from __future__ import annotations

import asyncio
import io
import random
import re
import time
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any
from unittest.mock import patch

from PIL import Image

from src.services.media.s3_client import S3Client
from tests.replay.cassette import Cassette, DownloadSpec, FrameRecord

_REPLAY_URL_BASE = "https://replay.invalid/"
_LOWRES_FORMAT = "worstvideo"
_HIRES_FORMAT = "height<=720"
_SCENE_FILTER = "select='gt(scene"
_SCORE_FILE_RE = re.compile(r"metadata=print:[^,]*?file=([^,:]+)")
# What ffmpeg 7 prints and returns when the scene pass writes no frame at all.
_EMPTY_OUTPUT_STDERR = (
    "[out#0/image2] Nothing was written into output file, because at least one of its "
    "streams received no packets.\nConversion failed!"
)
_EMPTY_OUTPUT_RC = 234
_JPEG_SIZE = (320, 180)


@lru_cache(maxsize=512)
def fake_jpeg(seed: int) -> bytes:
    """A decodable, non-black JPEG whose aHash differs per seed (>5 KB)."""
    rng = random.Random(seed)
    blocks = Image.frombytes("L", (8, 8), bytes(rng.randrange(40, 256) for _ in range(64)))
    noise = Image.frombytes("L", _JPEG_SIZE, rng.randbytes(_JPEG_SIZE[0] * _JPEG_SIZE[1]))
    image = Image.blend(blocks.resize(_JPEG_SIZE, Image.Resampling.NEAREST), noise, 0.15)
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


def _write_sized(path: Path, size_bytes: int | None) -> None:
    """A placeholder video of the recorded size (sparse); 0 bytes = size not recorded."""
    with path.open("wb") as handle:
        handle.truncate(size_bytes or 0)


class FakeProcess:
    """The slice of ``asyncio.subprocess.Process`` the pipeline uses."""

    def __init__(
        self,
        seconds: float,
        *,
        stdout: bytes = b"",
        stderr: bytes = b"",
        returncode: int = 0,
        on_done: Callable[[], object] | None = None,
    ) -> None:
        self._seconds = seconds
        self._result = (stdout, stderr)
        self._final_rc = returncode
        self._on_done = on_done
        self.returncode: int | None = None

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
        if self.returncode is not None:
            return b"", b""
        if self._seconds > 0:
            await asyncio.sleep(self._seconds)
        if self._on_done is not None:
            self._on_done()
        self.returncode = self._final_rc
        return self._result

    async def wait(self) -> int:
        if self.returncode is None:
            self.returncode = -9
        return self.returncode

    def kill(self) -> None:
        self.returncode = -9


def _detected_timestamps(records: list[FrameRecord], count: int, duration: int) -> list[float]:
    """Timestamps for every detected frame: recorded where known, interpolated between."""
    known = {r.index: r.timestamp for r in records}
    stamps: list[float] = []
    for index in range(count):
        if index in known:
            stamps.append(known[index])
            continue
        before = max((i for i in known if i < index), default=None)
        after = min((i for i in known if i > index), default=None)
        lo_i, lo_t = (before, known[before]) if before is not None else (-1, 0.0)
        hi_i, hi_t = (after, known[after]) if after is not None else (count, float(duration))
        stamps.append(round(lo_t + (hi_t - lo_t) * (index - lo_i) / (hi_i - lo_i), 3))
    return stamps


def _arg_after(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1] if flag in argv else ""


def _score_file(video_filter: str) -> Path | None:
    """The ladder's side file (``metadata=print:...:file=<path>``), if requested."""
    match = _SCORE_FILE_RE.search(video_filter)
    return Path(match.group(1)) if match else None


class MediaFakes:
    """Recorded-duration media primitives bound to one cassette and speed."""

    def __init__(self, cassette: Cassette, speed: float) -> None:
        self.cassette = cassette
        self.speed = speed
        self.s3_objects: dict[str, Any] = {}
        self.unexpected_commands: list[str] = []
        self._hires_calls = 0
        frames = cassette.frames
        records = frames.candidates or frames.selected
        self._scores = {r.index: r.total_score for r in records}
        self._timestamps = _detected_timestamps(
            [*frames.selected, *frames.candidates], frames.detected_count, cassette.video.duration
        )

    def _seconds(self, key: str) -> float:
        return self.cassette.sleeps.get(key, 0.0) * self.speed

    # ─── Subprocesses ───

    async def create_subprocess_exec(self, program: str, *args: Any, **kwargs: Any) -> FakeProcess:
        argv = [str(program), *(str(a) for a in args)]
        handlers = {"yt-dlp": self._ytdlp, "ffmpeg": self._ffmpeg}
        handler = handlers.get(Path(argv[0]).name)
        if handler is None:
            self.unexpected_commands.append(" ".join(argv[:3]))
            raise FileNotFoundError(f"replay: no fake for {argv[0]}")
        return handler(argv)

    def _download(self, spec: DownloadSpec, output: str) -> FakeProcess:
        return FakeProcess(
            spec.seconds * self.speed,
            on_done=lambda: _write_sized(Path(output), spec.size_bytes),
        )

    def _ytdlp(self, argv: list[str]) -> FakeProcess:
        fmt = _arg_after(argv, "-f") or _arg_after(argv, "--format")
        if "--get-url" in argv:
            # Every hi-res seek reads the run's local 720p file since 1a.2: a
            # stream-URL lookup is a regression, not a path with timings.
            self.unexpected_commands.append("yt-dlp --get-url")
            return FakeProcess(0.0, returncode=1)
        if fmt.startswith(_LOWRES_FORMAT):
            return self._download(self.cassette.downloads.lowres, _arg_after(argv, "-o"))
        if _HIRES_FORMAT in fmt:
            return self._download(self._next_hires(), _arg_after(argv, "-o"))
        self.unexpected_commands.append(f"yt-dlp -f {fmt}")
        return FakeProcess(0.0, returncode=1)

    def _next_hires(self) -> DownloadSpec:
        plan = self.cassette.downloads.hires or [DownloadSpec(0.0, None)]
        spec = plan[min(self._hires_calls, len(plan) - 1)]
        self._hires_calls += 1
        return spec

    def _ffmpeg(self, argv: list[str]) -> FakeProcess:
        video_filter = _arg_after(argv, "-vf")
        if video_filter.startswith(_SCENE_FILTER):
            return self._scene_detect(argv[-4], _score_file(video_filter))
        if "-ss" in argv:
            # Hi-res refiner, moment fill and the ladder's seeks (rung 2 and
            # uniform sampling) — a distinct decodable JPEG per second.
            seed = 1_000_000 + int(float(_arg_after(argv, "-ss")))
            return FakeProcess(
                self._seconds("frameSeek"),
                on_done=lambda: Path(argv[-1]).write_bytes(fake_jpeg(seed)),
            )
        self.unexpected_commands.append(f"ffmpeg -vf {video_filter}")
        return FakeProcess(0.0, returncode=1)

    def _scene_detect(self, output_pattern: str, score_file: Path | None) -> FakeProcess:
        """Recorded rung-1 frames; a ``zero`` run below every threshold.

        The recorded frames sit above the threshold, so they are also the
        floor rung's lines. ffmpeg >= 7 exits non-zero when nothing was written.
        """
        frames_dir = Path(output_pattern).parent
        stamps = self._timestamps if self.cassette.frames.mode != "zero" else []

        def write_frames() -> None:
            for index in range(len(stamps)):
                (frames_dir / f"scene_{index + 1:04d}.jpg").write_bytes(fake_jpeg(index))
            if score_file is not None:
                score_file.write_text(
                    "".join(
                        f"frame:{i} pts:{i} pts_time:{ts:.6f}\nlavfi.scene_score=0.5\n"
                        for i, ts in enumerate(stamps)
                    )
                )

        stderr = "\n".join(
            f"[Parsed_showinfo_1 @ 0x0] n:{i} pts:{i} pts_time:{ts:.6f}"
            for i, ts in enumerate(stamps)
        )
        if not stamps:
            stderr = _EMPTY_OUTPUT_STDERR
        return FakeProcess(
            self._seconds("sceneDetect"),
            stderr=stderr.encode(),
            returncode=0 if stamps else _EMPTY_OUTPUT_RC,
            on_done=write_frames,
        )

    # ─── Frame scoring (CPU-bound; runs in a worker thread) ───

    def score_all_frames(self, frames: list[dict]) -> list[dict]:
        time.sleep(self._seconds("scoreSelect"))
        for frame in frames:
            frame["total_score"] = self._scores.get(frame["index"], 0.0)
        return frames

    def select_frames(
        self, scored_frames: list[dict], duration_seconds: int | None
    ) -> tuple[list[dict], list[dict]]:
        """The recorded local selection (pre-reselect for HIGH runs)."""
        wanted = {r.index for r in self.cassette.frames.selected}
        gallery_ids = set(self.cassette.frames.gallery_indices)
        selected = sorted(
            (f for f in scored_frames if f["index"] in wanted), key=lambda f: f["timestamp"]
        )
        return selected, [f for f in selected if f["index"] in gallery_ids]

    # ─── S3 ───

    async def _s3_wait(self, key: str, bytes_put: bool = False) -> None:
        scene_frame_put = bytes_put and "/scenes" in key
        seconds = self._seconds("sceneUpload" if scene_frame_put else "s3Op")
        if seconds > 0:
            await asyncio.sleep(seconds)

    async def s3_get_json(self, _client: S3Client, key: str) -> dict[str, Any] | None:
        await self._s3_wait(key)
        value = self.s3_objects.get(key)
        return value if isinstance(value, dict) else None

    async def s3_put_json(self, _client: S3Client, key: str, data: dict[str, Any]) -> None:
        await self._s3_wait(key)
        self.s3_objects[key] = data

    async def s3_put_bytes(
        self, _client: S3Client, key: str, data: bytes, content_type: str | None = None
    ) -> None:
        await self._s3_wait(key, bytes_put=True)
        self.s3_objects[key] = data

    async def s3_exists(self, _client: S3Client, key: str) -> bool:
        await self._s3_wait(key)
        return key in self.s3_objects

    def s3_presign(self, _client: S3Client, key: str, expires_in: int | None = None) -> str:
        return _REPLAY_URL_BASE + key

    def scene_manifest(self) -> dict[str, Any] | None:
        """The frame manifest the real extractor wrote (its uploaded selection)."""
        key = f"videos/{self.cassette.video_id}/scenes-v3/manifest.json"
        value = self.s3_objects.get(key)
        return value if isinstance(value, dict) else None


async def _no_bucket_check(_client: S3Client) -> None:
    return None


@contextmanager
def media_fakes(cassette: Cassette, speed: float) -> Iterator[MediaFakes]:
    """Install the subprocess, S3 and scorer fakes for one replay."""
    fakes = MediaFakes(cassette, speed)
    s3_methods: dict[str, Any] = {
        "get_json": fakes.s3_get_json,
        "put_json": fakes.s3_put_json,
        "put_bytes": fakes.s3_put_bytes,
        "exists": fakes.s3_exists,
        "generate_presigned_url": fakes.s3_presign,
        "ensure_bucket_exists": _no_bucket_check,
    }
    with ExitStack() as stack:
        stack.enter_context(patch("asyncio.create_subprocess_exec", fakes.create_subprocess_exec))
        scorer = "src.services.media.frame_scorer"
        stack.enter_context(patch(f"{scorer}.score_all_frames", fakes.score_all_frames))
        stack.enter_context(patch(f"{scorer}.select_frames", fakes.select_frames))
        for name, method in s3_methods.items():
            stack.enter_context(patch.object(S3Client, name, _unbound(method)))
        stack.enter_context(patch.object(S3Client, "is_available", staticmethod(lambda: True)))
        yield fakes


def _unbound(method: Callable[..., Any]) -> Callable[..., Any]:
    """Class-level stand-in: the instance arrives as the first argument."""

    def call(self: S3Client, *args: Any, **kwargs: Any) -> Any:
        return method(self, *args, **kwargs)

    return call
