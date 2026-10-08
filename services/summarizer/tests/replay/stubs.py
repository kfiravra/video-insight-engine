"""Stubs for the non-LLM externals a replay does NOT run as production code.

Media orchestration (scene extraction, 720p prefetch/refiner, moment-frame
fill, tier decision) runs for real on top of the I/O fakes in
``media_fakes``. What stays stubbed here, at the highest seam that keeps the
phase logic live (each one is "not provable by replay" until moved down):

* metadata — ``youtube.extract_video_data`` (yt-dlp ``extract_info``) and
  ``youtube.fetch_video_captions`` (the deferred caption fetch); the recorded
  ``metadata`` sleep is split between them (see ``_EXTRACT_INFO_SECONDS``).
  The description LLM is NOT stubbed, it replays through the fake LLM.
* transcript — caption runs keep the real ``fetch_transcript`` chain (S3
  miss, then the yt-dlp subtitles on the VideoData) behind a recorded delay;
  Whisper/Gemini/S3-sourced runs get a stub chain yielding the recorded
  segments under the recorded source label. SponsorBlock → none.
* OCR — ``process_scene_frames`` (tesseract over every frame + presigned
  URLs) is canned: no OCR text, recorded ``ocr`` sleep.
* Qdrant — ``store_transcript_chunks``/``store_default_output_chunks``/
  ``store_visual_chunks`` (embeddings + upsert, background tasks in assembly).
* the API status callback (``send_video_status`` / ``..._background``).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable, Iterator
from contextlib import ExitStack, contextmanager
from typing import Any
from unittest.mock import patch

from src.services.pipeline.pipeline_helpers import TranscriptData, TranscriptTrail, sse_event
from src.services.video.youtube import Chapter, SubtitleSegment, VideoContext, VideoData
from tests.replay.cassette import Cassette
from tests.replay.media_fakes import MediaFakes, media_fakes

_REPLAY_URL_BASE = "https://replay.invalid/"
_CAPTION_SOURCE = "ytdlp"
# The cassettes record extract_info + caption fetch as one ``metadata`` sleep.
# T1dQhQAm8Tc's prod worker log split its 12.2 s as yt-dlp info 5.0 s + the
# caption fetch (a 429 and its retry), so every replay splits it there: info
# up to 5.0 s, captions the rest.
_EXTRACT_INFO_SECONDS = 5.0

FetchChain = Callable[..., AsyncGenerator[Any, None]]


class ReplayStubs:
    """Recorded-duration stand-ins bound to one cassette and speed."""

    def __init__(self, cassette: Cassette, speed: float, media: MediaFakes) -> None:
        self.cassette = cassette
        self.speed = speed
        self.media = media
        self.qdrant_stores: list[str] = []

    async def sleep(self, key: str) -> None:
        await self._sleep_recorded(self.cassette.sleeps.get(key, 0.0))

    async def _sleep_recorded(self, recorded_seconds: float) -> None:
        seconds = recorded_seconds * self.speed
        if seconds > 0:
            await asyncio.sleep(seconds)

    def _metadata_split(self) -> tuple[float, float]:
        """(extract_info, caption fetch) seconds of the recorded metadata sleep."""
        total = self.cassette.sleeps.get("metadata", 0.0)
        info = min(total, _EXTRACT_INFO_SECONDS)
        return info, total - info

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
        info_seconds, _ = self._metadata_split()
        await self._sleep_recorded(info_seconds)
        data = self.video_data()
        data.caption_url = _REPLAY_URL_BASE + "timedtext" if data.subtitles else None
        data.subtitles = []
        return data

    async def fetch_video_captions(self, video_data: VideoData) -> None:
        _, caption_seconds = self._metadata_split()
        await self._sleep_recorded(caption_seconds)
        video_data.subtitles = self._subtitles()
        video_data.caption_url = None

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

    # ─── OCR (frames phase) ───

    async def process_scene_frames(
        self, extraction_result: dict[str, Any], youtube_id: str
    ) -> tuple[dict[str, Any], str]:
        await self.sleep("ocr")

        def enrich(frame: dict[str, Any]) -> dict[str, Any]:
            url = _REPLAY_URL_BASE + frame["s3_key"] if frame.get("s3_key") else None
            return {**frame, "s3_url": url, "ocr_text": None, "text_density": 0.0}

        result = {key: [enrich(f) for f in frames] for key, frames in extraction_result.items()}
        payload = [
            {
                "index": f["index"],
                "timestamp": f["timestamp"],
                "url": f["s3_url"],
                "s3Key": f.get("s3_key"),
                "ocrText": None,
                "textDensity": 0.0,
            }
            for f in result.get("selected_frames", [])
        ]
        return result, sse_event("frames", {"videoId": youtube_id, "frames": payload})

    # ─── Assembly ───

    async def store_chunks(self, youtube_id: str, *args: Any, **kwargs: Any) -> None:
        """Qdrant store (embeddings + upsert) — background task in assembly."""
        self.qdrant_stores.append(youtube_id)
        await self.sleep("qdrantStore")


async def _no_status(*args: Any, **kwargs: Any) -> None:
    return None


def _no_status_background(*args: Any, **kwargs: Any) -> None:
    return None


async def _no_sponsors(youtube_id: str) -> list[Any]:
    return []


def _patch_targets(stubs: ReplayStubs) -> dict[str, Any]:
    from src.services.pipeline.phases import transcript as transcript_phase

    assembly_mod = "src.services.pipeline.phases.assembly"
    store_mod = "src.services.vector.store"
    return {
        "src.services.video.youtube.extract_video_data": stubs.extract_video_data,
        "src.services.video.youtube.fetch_video_captions": stubs.fetch_video_captions,
        f"{transcript_phase.__name__}.fetch_transcript": stubs.transcript_chain(
            transcript_phase.fetch_transcript
        ),
        f"{transcript_phase.__name__}.get_sponsor_segments": _no_sponsors,
        "src.services.pipeline.phases.frames.process_scene_frames": stubs.process_scene_frames,
        f"{assembly_mod}.store_transcript_chunks": stubs.store_chunks,
        f"{assembly_mod}.store_default_output_chunks": stubs.store_chunks,
        f"{assembly_mod}.store_visual_chunks": stubs.store_chunks,
        # The seam itself too, whatever binding assembly ends up calling.
        f"{store_mod}.store_transcript_chunks": stubs.store_chunks,
        f"{store_mod}.store_default_output_chunks": stubs.store_chunks,
        f"{store_mod}.store_visual_chunks": stubs.store_chunks,
        f"{assembly_mod}.send_video_status_background": _no_status_background,
        "src.routes.pipeline_runner.send_video_status": _no_status,
        "src.services.observability.langfuse_client._client": None,
    }


@contextmanager
def external_stubs(cassette: Cassette, speed: float) -> Iterator[ReplayStubs]:
    """Patch every external for one replay: media fakes + the stubs above."""
    with ExitStack() as stack:
        media = stack.enter_context(media_fakes(cassette, speed))
        stubs = ReplayStubs(cassette, speed, media)
        for target, replacement in _patch_targets(stubs).items():
            stack.enter_context(patch(target, replacement))
        yield stubs
