"""The transcript phase records the segments prompt renders read (pipeline-1min 1b).

``ctx.prompt_segments`` follows ``clean_text``: the raw segments, or — when
SponsorBlock cut a sponsor read — the filtered list, so the marked transcript
the plan, memory and extraction read never carries the sponsor read either.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.services.pipeline.phases import transcript as transcript_phase
from src.services.pipeline.pipeline_helpers import TranscriptData
from src.services.video.sponsorblock import SponsorSegment

_SEGMENTS = [
    {"text": "Welcome to the kitchen.", "start": 0.0, "duration": 5.0},
    {"text": "This video is sponsored by Brand.", "start": 5.0, "duration": 5.0},
    {"text": "First, chop the onions.", "start": 10.0, "duration": 5.0},
]
_SPONSOR_READ = SponsorSegment(start_seconds=5.0, end_seconds=10.0, category="sponsor", uuid="u1")


def _ctx() -> SimpleNamespace:
    return SimpleNamespace(
        youtube_id="vid123",
        video_data=SimpleNamespace(context=SimpleNamespace(category="cooking"), duration=600),
        language="en",
        is_rtl=False,
        source_language_code=None,
        clean_text="",
        prompt_segments=[],
        transcript_data=None,
        transcript_trail=None,
    )


async def _run(sponsor_segments: list[SponsorSegment]) -> SimpleNamespace:
    ctx = _ctx()

    def fake_fetch(*_args: object, **_kwargs: object):
        async def _gen():
            yield TranscriptData(
                segments=list(_SEGMENTS),
                raw_text=" ".join(s["text"] for s in _SEGMENTS),
                transcript_type="manual",
                source="ytdlp",
                language="en",
            )

        return _gen()

    with (
        patch.object(transcript_phase, "fetch_transcript", fake_fetch),
        patch.object(transcript_phase.settings, "TRANSCRIPT_CLEANING_ENABLED", False),
        patch.object(
            transcript_phase, "get_sponsor_segments", AsyncMock(return_value=sponsor_segments)
        ),
    ):
        _ = [event async for event in transcript_phase.run_phase_transcript(ctx)]  # type: ignore[arg-type]
    return ctx


async def test_should_keep_every_segment_when_no_sponsor_read_was_cut() -> None:
    ctx = await _run([])

    assert [s["text"] for s in ctx.prompt_segments] == [s["text"] for s in _SEGMENTS]


async def test_should_drop_the_sponsor_read_from_prompt_segments() -> None:
    ctx = await _run([_SPONSOR_READ])

    assert [s["start"] for s in ctx.prompt_segments] == [0.0, 10.0]


async def test_should_drop_the_same_read_from_clean_text() -> None:
    ctx = await _run([_SPONSOR_READ])

    assert "sponsored" not in ctx.clean_text
