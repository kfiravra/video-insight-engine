"""Tests for "YouTube is rate-limiting, retrying" (pipeline/youtube_retry.py) — hotfix 4.1 (2).

A blocked proxy exit during the metadata extract or the caption fetch reaches the
client as one SSE ``phase`` event while the rotation runs, instead of nothing until
the failure.
"""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from src.services.media import download_utils
from src.services.pipeline import youtube_retry
from src.services.pipeline.phases import metadata
from src.services.pipeline.youtube_retry import (
    METADATA_RETRY_PHASE,
    TRANSCRIPT_RETRY_PHASE,
    RetryNotice,
    notify_exit_blocked,
    relay_retry_notice,
    with_retry_notice,
)


def _phases(chunks: list[str]) -> list[str]:
    out = []
    for chunk in chunks:
        payload = json.loads(chunk.removeprefix("data: ").strip())
        out.append(payload.get("phase") or payload.get("event"))
    return out


async def _relay(task: asyncio.Future, notice: RetryNotice, phase: str) -> list[str]:
    return [chunk async for chunk in relay_retry_notice(task, notice, phase)]


class TestRelay:
    async def test_should_announce_a_blocked_exit_once_while_the_fetch_runs(self):
        notice = RetryNotice()
        gate = asyncio.Event()

        async def fetch() -> str:
            notice.mark_blocked()
            notice.mark_blocked()
            await gate.wait()
            return "ok"

        task = asyncio.ensure_future(fetch())
        asyncio.get_running_loop().call_later(0.01, gate.set)

        assert _phases(await _relay(task, notice, METADATA_RETRY_PHASE)) == [METADATA_RETRY_PHASE]

    async def test_should_stay_silent_when_no_exit_was_blocked(self):
        notice = RetryNotice()
        task = asyncio.ensure_future(asyncio.sleep(0, result="ok"))

        assert await _relay(task, notice, METADATA_RETRY_PHASE) == []

    async def test_should_leave_the_fetch_error_to_the_caller(self):
        notice = RetryNotice()

        async def fetch() -> None:
            notice.mark_blocked()
            await asyncio.sleep(0.02)  # the next exits, also blocked
            raise RuntimeError("every exit blocked")

        task = asyncio.ensure_future(fetch())
        chunks = await _relay(task, notice, METADATA_RETRY_PHASE)

        assert _phases(chunks) == [METADATA_RETRY_PHASE]
        with pytest.raises(RuntimeError):
            await task

    async def test_should_hear_a_block_from_the_fetchs_worker_thread(self):
        notice = RetryNotice()
        gate = asyncio.Event()

        async def fetch() -> None:
            await asyncio.to_thread(notify_exit_blocked)
            await gate.wait()

        task = asyncio.ensure_future(with_retry_notice(notice, fetch()))
        asyncio.get_running_loop().call_later(0.05, gate.set)

        assert _phases(await _relay(task, notice, TRANSCRIPT_RETRY_PHASE)) == [
            TRANSCRIPT_RETRY_PHASE
        ]

    def test_should_ignore_a_block_outside_a_fetch(self):
        assert notify_exit_blocked() is None  # no notice in this context: nothing to tell

    async def test_should_drop_a_block_after_the_runs_loop_closed(self):
        notice = RetryNotice()
        loop = asyncio.new_event_loop()
        loop.close()
        notice._loop = loop  # the run is over; its worker thread still reports

        assert await asyncio.to_thread(_notify_in, notice) is None


class TestRotationSignal:
    async def test_should_set_the_notice_on_every_blocked_exit(self, monkeypatch):
        monkeypatch.setattr(download_utils, "record_blocked_exit", lambda _url: None)
        monkeypatch.setattr(download_utils, "record_working_exit", lambda *_a: None)
        notice = RetryNotice()

        def attempt(url: str) -> str:
            if url != "exit-3":
                raise RuntimeError("bot check")
            time.sleep(0.05)  # a real extract takes seconds through the proxy
            return "ok"

        def rotate() -> str:
            return download_utils.try_proxy_exits(
                ["exit-1", "exit-2", "exit-3"], attempt, lambda _e: True, "Metadata extract"
            )

        async def fetch() -> str:
            return await asyncio.to_thread(rotate)

        task = asyncio.ensure_future(with_retry_notice(notice, fetch()))
        chunks = await _relay(task, notice, METADATA_RETRY_PHASE)

        assert (await task, _phases(chunks)) == ("ok", [METADATA_RETRY_PHASE])


class TestPhases:
    async def test_should_announce_the_retry_before_the_metadata_event(self, monkeypatch):
        from tests.test_phase_metadata import _ctx, _run, _World

        world = _World(duration=600, cached=False)
        answer = world.extract.return_value

        async def blocked_then_served(_video_id: str):
            notify_exit_blocked()
            await asyncio.sleep(0.01)
            return answer

        world.extract.side_effect = blocked_then_served
        ctx = _ctx()
        chunks = await _run(ctx, world)
        world.release.set()

        assert _phases(chunks)[:2] == [METADATA_RETRY_PHASE, "metadata"]
        await ctx.description_task

    async def test_should_relay_a_caption_retry_before_the_transcript_phase(self):
        notice = RetryNotice()
        gate = asyncio.Event()

        async def captions() -> None:
            await asyncio.to_thread(notify_exit_blocked)
            await gate.wait()

        ctx = SimpleNamespace(
            caption_task=asyncio.ensure_future(with_retry_notice(notice, captions())),
            caption_retry_notice=notice,
        )

        async def run_phase_transcript(_ctx):
            yield 'data: {"event": "phase", "phase": "transcript"}\n\n'

        asyncio.get_running_loop().call_later(0.05, gate.set)
        chunks = [c async for c in metadata.after_captions(run_phase_transcript)(ctx)]  # type: ignore[arg-type]

        assert _phases(chunks) == [TRANSCRIPT_RETRY_PHASE, "transcript"]


def _notify_in(notice: RetryNotice) -> None:
    youtube_retry._notice_var.set(notice)
    return notify_exit_blocked()


class TestCaptionApi:
    async def test_should_relay_a_blocked_exit_during_the_caption_api_fetch(self):
        """The caption API rotates inside the transcript phase, not the caption task."""
        from unittest.mock import patch

        from src.services.transcription import transcript_fetcher
        from src.services.transcription.transcript_fetcher import fetch_transcript
        from src.services.pipeline.pipeline_helpers import TranscriptTrail
        from tests.test_transcript_fetcher import _drain as drain_chain
        from tests.test_transcript_fetcher import _video_data_without_captions

        async def caption(_youtube_id: str, **_kwargs: object):
            await asyncio.to_thread(notify_exit_blocked)
            await asyncio.sleep(0.05)
            return [{"text": "hi", "start": 0.0, "duration": 1.0}], "hi", "manual", "en"

        with (
            patch.object(transcript_fetcher.S3Client, "is_available", return_value=False),
            patch.object(transcript_fetcher.settings, "YOUTUBE_PROXY_URL", None),
            patch.object(transcript_fetcher, "get_transcript", new=caption),
        ):
            items = await drain_chain(
                fetch_transcript(
                    "vid123",
                    _video_data_without_captions(),
                    duration=300,
                    is_music=False,
                    trail=TranscriptTrail(),
                )
            )

        assert TRANSCRIPT_RETRY_PHASE in _phases([i for i in items if isinstance(i, str)])


class TestCancellation:
    async def test_should_cancel_the_extract_when_the_phase_is_cancelled(self):
        from tests.test_phase_metadata import _ctx, _patches, _World

        world = _World(duration=600, cached=False)
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def stuck(_video_id: str):
            started.set()
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        world.extract.side_effect = stuck
        patches = _patches(world)
        for patch_ in patches:
            patch_.start()
        try:
            run = asyncio.ensure_future(_drain(metadata.run_phase_metadata(_ctx())))  # type: ignore[arg-type]
            await started.wait()
            run.cancel()
            with pytest.raises(asyncio.CancelledError):
                await run
            await asyncio.wait_for(cancelled.wait(), timeout=1)
        finally:
            for patch_ in patches:
                patch_.stop()

        assert cancelled.is_set()


async def _drain(gen) -> list[str]:
    return [chunk async for chunk in gen]
