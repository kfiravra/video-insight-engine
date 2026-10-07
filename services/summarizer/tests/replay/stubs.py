"""Stubs for every non-LLM external the pipeline touches during a replay.

Each stub sleeps the cassette's recorded wall time for its step (scaled by
the replay speed) and returns recorded data, so the real orchestration and
phase logic run unchanged around it. Stubs sit at the highest seam that keeps
the phase's own logic live:

* metadata — ``youtube.extract_video_data`` (yt-dlp info + caption fetch);
  the description LLM is NOT stubbed here, it replays through the fake LLM.
* transcript — caption runs keep the real ``fetch_transcript`` chain (S3 off,
  yt-dlp subtitles on the VideoData) behind a recorded delay; Whisper/Gemini/
  S3-sourced runs get a stub chain that yields the recorded segments under
  the recorded source label. SponsorBlock → none.
* frames — ``extract_scene_keyframes`` (download, scene detect, score, the
  HIGH-tier reselect hook — which runs the REAL vision call —, hi-res, S3
  upload) records the same ``frames.*`` sub-steps as the real one;
  ``process_scene_frames`` (OCR + presign) and ``persist_vision_descriptions``
  (S3 manifest) are canned. The STANDARD vision call in ``phases/frames.py``
  runs for real against tiny placeholder JPEGs.
* assembly — moment-frame fill (720p re-download + ffmpeg + S3), Qdrant
  stores (embeddings), the API status callback; Redis and S3 are switched off
  by the driver.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
import time
from collections.abc import AsyncGenerator, Awaitable, Callable, Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

from src.services.media.s3_client import S3Client
from src.services.pipeline.pipeline_helpers import TranscriptData, TranscriptTrail, sse_event
from src.services.pipeline.pipeline_timing import mark_step, record_download
from src.services.video.youtube import Chapter, SubtitleSegment, VideoContext, VideoData
from tests.replay.cassette import Cassette, FrameRecord

# Smallest byte string the vision path will base64 — the fake LLM never looks at it.
_PLACEHOLDER_JPEG = b"\xff\xd8\xff\xd9"
_REPLAY_URL_BASE = "https://replay.invalid/"
_CAPTION_SOURCE = "ytdlp"

ReselectHook = Callable[[list[dict]], Awaitable[list[dict]]]
FetchChain = Callable[..., AsyncGenerator[Any, None]]


class ReplayStubs:
    """Recorded-duration stand-ins bound to one cassette and speed."""

    def __init__(self, cassette: Cassette, speed: float) -> None:
        self.cassette = cassette
        self.speed = speed
        self.qdrant_stores: list[str] = []
        self._temp_dirs: list[str] = []

    async def sleep(self, key: str) -> None:
        seconds = self.cassette.sleeps.get(key, 0.0) * self.speed
        if seconds > 0:
            await asyncio.sleep(seconds)

    async def _timed(self, key: str, step: str) -> None:
        started = time.monotonic()
        await self.sleep(key)
        mark_step(step, started)

    # ─── Metadata + transcript ───

    def _subtitles(self) -> list[SubtitleSegment]:
        if self.cassette.transcript_source != _CAPTION_SOURCE:
            return []
        return [
            SubtitleSegment(text=text, start=start / 1000, duration=(end - start) / 1000)
            for start, end, text in self.cassette.segments
        ]

    def video_data(self) -> VideoData:
        spec = self.cassette.video
        return VideoData(
            video_id=self.cassette.video_id,
            title=spec.title,
            channel=spec.channel,
            duration=spec.duration,
            thumbnail_url=spec.thumbnail_url,
            description=spec.description,
            chapters=[Chapter(c["start"], c["end"], c["title"]) for c in spec.chapters],
            subtitles=self._subtitles(),
            context=VideoContext(
                youtube_category=spec.youtube_category,
                category=spec.category,
                tags=list(spec.tags),
                display_tags=list(spec.tags)[:6],
            ),
            language=spec.language,
            caption_track=spec.caption_track,
            caption_lang=spec.caption_lang,
        )

    async def extract_video_data(self, youtube_id: str) -> VideoData:
        await self.sleep("metadata")
        return self.video_data()

    def transcript_chain(self, real_fetch: FetchChain) -> FetchChain:
        """Caption runs: real chain after the recorded delay; others: recorded data."""

        async def fetch(*args: Any, **kwargs: Any) -> AsyncGenerator[Any, None]:
            await self.sleep("transcript")
            if self.cassette.transcript_source == _CAPTION_SOURCE:
                async for item in real_fetch(*args, **kwargs):
                    yield item
                return
            yield self._recorded_transcript(kwargs.get("trail"))

        return fetch

    def _recorded_transcript(self, trail: TranscriptTrail | None) -> TranscriptData:
        segments = [
            {"text": text, "start": start / 1000, "duration": (end - start) / 1000}
            for start, end, text in self.cassette.segments
        ]
        data = TranscriptData(
            segments=segments,
            raw_text=" ".join(s["text"] for s in segments),
            transcript_type=self.cassette.transcript_source,
            source=self.cassette.transcript_source,
            language=self.cassette.video.language,
        )
        data.trail = trail or TranscriptTrail()
        return data

    # ─── Frames ───

    def _frame(self, temp_dir: str, record: FrameRecord) -> dict[str, Any]:
        filename = f"scene_{record.index:04d}.jpg"
        path = Path(temp_dir) / filename
        path.write_bytes(_PLACEHOLDER_JPEG)
        return {
            "index": record.index,
            "filename": filename,
            "path": str(path),
            "timestamp": record.timestamp,
            "temp_dir": temp_dir,
            "total_score": record.total_score,
            "s3_key": f"videos/{self.cassette.video_id}/scenes-v3/{filename}",
        }

    async def _reselect(
        self, hook: ReselectHook, candidates: list[dict[str, Any]], selected: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Run the real hook (HIGH vision call), then keep the recorded selection.

        The hook's presenter filter + floor decide which candidates survive in
        the real extractor; the cassette already holds what survived, so the
        recorded list is returned to keep downstream counts identical.
        """
        started = time.monotonic()
        try:
            await hook(candidates)
        finally:
            mark_step("frames.vision_reselect", started)
        return selected

    async def _download_and_detect(self) -> None:
        await self.sleep("manifestCheck")
        download_started = time.monotonic()
        await self.sleep("lowresDownload")
        record_download(
            kind="lowres",
            purpose="scene_detect",
            start_monotonic=download_started,
            path=None,
            ok=True,
        )
        await self._timed("sceneDetect", "frames.scene_detect")

    async def extract_scene_keyframes(
        self,
        video_id: str,
        scene_threshold: float | None = None,
        max_frames: int | None = None,
        duration_seconds: int | None = None,
        overselect_count: int | None = None,
        reselect_hook: ReselectHook | None = None,
    ) -> dict[str, Any]:
        await self._download_and_detect()
        frames = self.cassette.frames
        if frames.mode == "zero":
            return {"all_frames": [], "selected_frames": [], "gallery_frames": []}

        temp_dir = tempfile.mkdtemp(prefix=f"vie-replay-{video_id}-")
        self._temp_dirs.append(temp_dir)
        selected = [self._frame(temp_dir, f) for f in frames.selected]
        await self._timed("scoreSelect", "frames.score_select")
        all_frames = selected
        if overselect_count and reselect_hook is not None and frames.candidates:
            all_frames = [self._frame(temp_dir, f) for f in frames.candidates]
            selected = await self._reselect(reselect_hook, all_frames, selected)
        await self._timed("hires", "frames.hires")
        await self._timed("upload", "frames.upload")
        gallery_ids = set(frames.gallery_indices)
        gallery = [f for f in selected if f["index"] in gallery_ids]
        return {"all_frames": all_frames, "selected_frames": selected, "gallery_frames": gallery}

    async def process_scene_frames(
        self, extraction_result: dict[str, Any], youtube_id: str, clean_text: str | None = None
    ) -> tuple[dict[str, Any], str, str | None]:
        await self.sleep("ocr")

        def enrich(frame: dict[str, Any]) -> dict[str, Any]:
            url = _REPLAY_URL_BASE + frame["s3_key"]
            return {**frame, "s3_url": url, "ocr_text": None, "text_density": 0.0}

        result = {key: [enrich(f) for f in frames] for key, frames in extraction_result.items()}
        payload = [
            {
                "index": f["index"],
                "timestamp": f["timestamp"],
                "url": f["s3_url"],
                "s3Key": f["s3_key"],
                "ocrText": None,
                "textDensity": 0.0,
            }
            for f in result.get("selected_frames", [])
        ]
        return result, sse_event("frames", {"videoId": youtube_id, "frames": payload}), None

    async def persist_vision_descriptions(self, video_id: str, descriptions: list[dict]) -> None:
        await self.sleep("persistVision")

    def derive_tier(self, category: str | None, title: str, tags: list[str] | None) -> str:
        return self.cassette.tier

    # ─── Assembly ───

    async def fill_moment_frames(self, tabs: list[dict], youtube_id: str) -> int:
        started = time.monotonic()
        await self.sleep("momentFill")
        record_download(
            kind="720p", purpose="moment_fill", start_monotonic=started, path=None, ok=True
        )
        return 0

    async def store_chunks(self, youtube_id: str, *args: Any, **kwargs: Any) -> None:
        """Qdrant store (embeddings + upsert) — background task in assembly."""
        self.qdrant_stores.append(youtube_id)
        await self.sleep("qdrantStore")

    def cleanup(self) -> None:
        for temp_dir in self._temp_dirs:
            shutil.rmtree(temp_dir, ignore_errors=True)


async def _no_status(*args: Any, **kwargs: Any) -> None:
    return None


def _no_status_background(*args: Any, **kwargs: Any) -> None:
    return None


async def _no_sponsors(youtube_id: str) -> list[Any]:
    return []


def _patch_targets(stubs: ReplayStubs) -> dict[str, Any]:
    from src.services.pipeline.phases import transcript as transcript_phase

    frames_mod = "src.services.pipeline.phases.frames"
    assembly_mod = "src.services.pipeline.phases.assembly"
    return {
        "src.services.video.youtube.extract_video_data": stubs.extract_video_data,
        f"{transcript_phase.__name__}.fetch_transcript": stubs.transcript_chain(
            transcript_phase.fetch_transcript
        ),
        f"{transcript_phase.__name__}.get_sponsor_segments": _no_sponsors,
        f"{frames_mod}.extract_scene_keyframes": stubs.extract_scene_keyframes,
        f"{frames_mod}.process_scene_frames": stubs.process_scene_frames,
        f"{frames_mod}.persist_vision_descriptions": stubs.persist_vision_descriptions,
        f"{frames_mod}.derive_tier": stubs.derive_tier,
        f"{assembly_mod}.fill_moment_frames": stubs.fill_moment_frames,
        f"{assembly_mod}.store_transcript_chunks": stubs.store_chunks,
        f"{assembly_mod}.store_default_output_chunks": stubs.store_chunks,
        # The seam itself too, whatever binding assembly ends up calling.
        "src.services.vector.store.store_transcript_chunks": stubs.store_chunks,
        "src.services.vector.store.store_default_output_chunks": stubs.store_chunks,
        f"{assembly_mod}.send_video_status_background": _no_status_background,
        "src.routes.pipeline_runner.send_video_status": _no_status,
        "src.services.observability.langfuse_client._client": None,
    }


@contextmanager
def external_stubs(cassette: Cassette, speed: float) -> Iterator[ReplayStubs]:
    """Patch every non-LLM external for the duration of one replay."""
    stubs = ReplayStubs(cassette, speed)
    with ExitStack() as stack:
        for target, replacement in _patch_targets(stubs).items():
            stack.enter_context(patch(target, replacement))
        stack.enter_context(patch.object(S3Client, "is_available", staticmethod(lambda: False)))
        try:
            yield stubs
        finally:
            stubs.cleanup()
