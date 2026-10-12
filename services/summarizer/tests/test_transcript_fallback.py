"""Garbage captions → the audio path, via the tier probe (pipeline-1min 2.5).

The text branch runs as production code — the transcript phase (first pass and
audio pass), the probe task and its parser, the fallback gates — with only the
boundaries faked: the caption fetch chain, Whisper, SponsorBlock, the probe's
LLM call and the plan/memory readers. The fake probe LLM answers
``transcript_usable: false`` only for windows holding a garbage marker. The
readers' side (frames, the order against translation) is in
``test_transcript_fallback_readers.py``.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config import settings
from src.exceptions import TranscriptError
from src.models.pipeline_types import PlanResult
from src.models.probe_types import TierProbe
from src.models.schemas import ErrorCode
from src.services.pipeline import pipeline_timing, tier_probe
from src.services.pipeline.context import PipelineContext
from src.services.pipeline.phases import text as text_phase
from src.services.pipeline.phases import transcript as transcript_phase
from src.services.pipeline.phases.probe import cancel_tier_probe, start_tier_probe
from src.services.pipeline.pipeline_helpers import TranscriptData, TranscriptTrail
from src.services.pipeline.pipeline_timing import PipelineTimingRecorder, start_run_timing
from src.services.transcription import transcript_fetcher
from src.services.video.sponsorblock import SponsorSegment
from src.services.video.youtube import VideoContext, VideoData

# TNhaISOUy6Q (Fireship, English speech) is tagged `pl`; its only ASR is
# `pl-orig`, Polish recognition of English speech (first 25 segments).
GARBAGE_PL_ASR = (
    "z UE Crocs i Spears abroad for PSP free and promoting and Windows Phone software or "
    "Damage to the same Excellent condition and other Games free free Saints Row the third "
    "Edition overhaul duplicate-1 duplicate-1 d Władek pole Dancing with application with "
    "download fast-paced download the Free Virtual DJ Home Free no download Metin2 xenoxmt2 "
    "sp3srodawlkp msvcp100dll ciebie will and unicode want2work With The Beatles revolver "
    "connection2nd Best of their High english4matura dcd791nt"
)
GARBAGE_MARKER = "msvcp100dll"
WHISPER_SPEECH = (
    "React hooks let a function component keep state. useState returns the value and a "
    "setter, and useEffect runs after every render unless you pass a dependency array."
)
ENGLISH_CAPTIONS = (
    "Today we build a to-do app with React. First create the component, then add a list "
    "of items and a button that appends a new item when you click it."
)
# EqaMrzd9nZU-shaped: Hebrew speech, Hebrew `iw` captions.
HEBREW_CAPTIONS = (
    "שלום וברוכים הבאים לשידור המחזק. היום נדבר על כוח הבחירה החופשית "
    "ועל החשיבות של פתיחת הלב, התפילה והאמונה בדרך אל הגאולה."
)
_PLAN = PlanResult.model_validate(
    {"contentTags": ["tech"], "primaryTag": "tech", "userGoal": "Learn hooks", "confidence": 0.9}
)


def _segments(text: str, words_per_segment: int = 10) -> list[dict[str, Any]]:
    words = text.split()
    return [
        {"text": " ".join(words[i : i + words_per_segment]), "start": i * 0.5, "duration": 4.0}
        for i in range(0, len(words), words_per_segment)
    ]


def _transcript(text: str, source: str = "ytdlp", language: str | None = "pl") -> TranscriptData:
    kind = {"whisper": "whisper", "s3": "cached-ytdlp"}.get(source, "auto-generated")
    return TranscriptData(
        segments=_segments(text),
        raw_text=text,
        transcript_type=kind,
        source=source,
        language=language,
    )


def _ctx(category: str = "coding", duration: int = 600) -> PipelineContext:
    ctx = PipelineContext(
        video_summary_id="vsid",
        youtube_id="TNhaISOUy6Q",
        entry={},
        repository=MagicMock(),
        llm_service=MagicMock(),
        timer=MagicMock(),
    )
    context = VideoContext("Science & Technology", category, ["react"], ["react"])
    ctx.video_data = VideoData(
        video_id="TNhaISOUy6Q",
        title="React Hooks in 100 seconds",
        channel="Fireship",
        duration=duration,
        thumbnail_url=None,
        description="",
        context=context,
    )
    return ctx


class _ProbeLLM:
    """The probe's LLM boundary: ``transcript_usable`` false iff a marker is in the windows."""

    def __init__(self, *markers: str, domain: str = "tech", order: list[str] | None = None) -> None:
        self.markers = markers or (GARBAGE_MARKER,)
        self.domain = domain
        self.order = order if order is not None else []
        self.prompts: list[str] = []

    async def __call__(self, _service: object, prompt: str, **_kwargs: object) -> str:
        self.prompts.append(prompt)
        self.order.append("probe")
        windows = prompt.split("<transcript_windows>")[1]
        usable = not any(marker in windows for marker in self.markers)
        answer = {"domain": self.domain, "format": "tutorial", "has_visual_demo": True}
        return json.dumps({**answer, "confidence": 0.9, "transcript_usable": usable})


class _Readers:
    """Plan + memory stand-ins recording what the plan saw."""

    def __init__(self) -> None:
        self.plan_probe: TierProbe | None = None
        self.plan_transcript = ""

    async def plan(self, ctx: PipelineContext) -> AsyncGenerator[str, None]:
        self.plan_probe, self.plan_transcript = ctx.probe, ctx.prompt_transcript
        ctx.plan_result = _PLAN
        yield "data: triage_complete\n\n"

    async def memory(self, _ctx: PipelineContext) -> AsyncGenerator[str, None]:
        return
        yield  # pragma: no cover — makes this an async generator


def _whisper_ok() -> AsyncMock:
    return AsyncMock(return_value=(_transcript(WHISPER_SPEECH, "whisper", "en"), None))


def _whisper_failed() -> AsyncMock:
    return AsyncMock(return_value=(None, TranscriptError("w", ErrorCode.UNKNOWN_ERROR)))


@dataclass
class Fakes:
    """Every faked boundary of one text-branch run (``gemini`` set = a Gemini key)."""

    probe_llm: _ProbeLLM = field(default_factory=_ProbeLLM)
    whisper: AsyncMock = field(default_factory=_whisper_ok)
    gemini: AsyncMock | None = None
    sponsors: AsyncMock = field(default_factory=lambda: AsyncMock(return_value=[]))
    readers: _Readers = field(default_factory=_Readers)
    whisper_enabled: bool = True


def _first_pass(data: TranscriptData, origin: str | None = None) -> Any:
    def fetch(*_args: object, trail: TranscriptTrail, **_kwargs: object) -> Any:
        async def _gen() -> AsyncGenerator[TranscriptData, None]:
            trail.origin = origin
            data.trail = trail
            yield data

        return _gen()

    return fetch


@pytest.fixture
def recorder() -> Iterator[PipelineTimingRecorder]:
    token = pipeline_timing._recorder_var.set(None)
    try:
        yield start_run_timing()
    finally:
        pipeline_timing._recorder_var.reset(token)


async def run_text_branch(
    ctx: PipelineContext, first: TranscriptData, fakes: Fakes, origin: str | None = None
) -> list[str]:
    """Drive the text branch from probe start (as phase 2 does) to the plan."""
    gemini = fakes.gemini or AsyncMock(return_value=None)
    with (
        patch.object(settings, "WHISPER_ENABLED", fakes.whisper_enabled),
        patch.object(settings, "WHISPER_MAX_DURATION_MINUTES", 60),
        patch.object(settings, "GEMINI_API_KEY", "fake-key" if fakes.gemini else ""),
        patch.object(settings, "TRANSCRIPT_CLEANING_ENABLED", False),
        patch.object(transcript_phase, "fetch_transcript", _first_pass(first, origin)),
        patch.object(transcript_phase, "get_sponsor_segments", fakes.sponsors),
        patch.object(transcript_fetcher, "_try_whisper_transcription", fakes.whisper),
        patch.object(transcript_fetcher, "_try_gemini_transcription", gemini),
        patch.object(tier_probe, "call_llm_with_retry", fakes.probe_llm),
        patch.object(tier_probe, "load_prompt_text", lambda path: Path(path).read_text()),
        patch.object(text_phase, "run_phase_plan", fakes.readers.plan),
        patch.object(text_phase, "run_phase_memory", fakes.readers.memory),
    ):
        start_tier_probe(ctx)
        try:
            return [event async for event in text_phase.run_phase_text(ctx)]
        finally:
            cancel_tier_probe(ctx)
            await asyncio.gather(ctx.tier_probe_task, return_exceptions=True)


def _source(ctx: PipelineContext) -> str | None:
    return ctx.transcript_data.source if ctx.transcript_data else None


def _attempted(ctx: PipelineContext) -> list[str] | None:
    return ctx.transcript_trail.attempted if ctx.transcript_trail else None


def _phases(events: list[str]) -> list[str]:
    return [json.loads(e[6:])["phase"] for e in events if '"event": "phase"' in e]


class TestGarbageCaptions:
    @pytest.mark.parametrize(
        ("source", "origin"), [("ytdlp", None), ("api", None), ("proxy", None), ("s3", "ytdlp")]
    )
    async def test_should_replace_a_caption_transcript_with_whisper_when_judged_unusable(
        self, source: str, origin: str | None
    ) -> None:
        ctx = _ctx()

        await run_text_branch(ctx, _transcript(GARBAGE_PL_ASR, source), Fakes(), origin)

        assert (_source(ctx), _attempted(ctx)) == ("whisper", [f"{source}-unusable"])

    async def test_should_take_the_gemini_path_when_only_gemini_is_allowed(self) -> None:
        ctx = _ctx()
        gemini = AsyncMock(return_value=_transcript(WHISPER_SPEECH, "gemini", "en"))

        fakes = Fakes(gemini=gemini, whisper_enabled=False)
        await run_text_branch(ctx, _transcript(GARBAGE_PL_ASR), fakes)

        assert (_source(ctx), fakes.whisper.await_count) == ("gemini", 0)

    async def test_should_probe_the_whisper_text_a_second_time(self) -> None:
        fakes = Fakes()

        await run_text_branch(_ctx(), _transcript(GARBAGE_PL_ASR), fakes)

        prompts = fakes.probe_llm.prompts
        assert len(prompts) == 2 and "useEffect" in prompts[1]

    async def test_should_plan_from_the_second_probe(self) -> None:
        fakes = Fakes()

        await run_text_branch(_ctx(), _transcript(GARBAGE_PL_ASR), fakes)

        probe = fakes.readers.plan_probe
        assert probe is not None and probe.transcript_usable is True

    async def test_should_plan_from_the_whisper_transcript(self) -> None:
        fakes = Fakes()

        await run_text_branch(_ctx(), _transcript(GARBAGE_PL_ASR), fakes)

        seen = fakes.readers.plan_transcript
        assert "useState" in seen and "Crocs" not in seen

    async def test_should_take_the_language_of_the_whisper_transcript(self) -> None:
        ctx = _ctx()

        await run_text_branch(ctx, _transcript(GARBAGE_PL_ASR), Fakes())

        assert ctx.source_language_code is None

    async def test_should_cut_the_sponsor_read_from_the_whisper_text(self) -> None:
        read = SponsorSegment(start_seconds=5.0, end_seconds=9.0, category="sponsor", uuid="u1")
        fakes = Fakes(sponsors=AsyncMock(return_value=[read]))

        await run_text_branch(_ctx(), _transcript(GARBAGE_PL_ASR), fakes)

        assert "useEffect" not in fakes.readers.plan_transcript

    async def test_should_ask_sponsorblock_once_for_both_passes(self) -> None:
        read = SponsorSegment(start_seconds=5.0, end_seconds=9.0, category="sponsor", uuid="u1")
        fakes = Fakes(sponsors=AsyncMock(return_value=[read]))

        await run_text_branch(_ctx(), _transcript(GARBAGE_PL_ASR), fakes)

        assert fakes.sponsors.await_count == 1

    async def test_should_time_the_audio_pass_as_its_own_step(
        self, recorder: PipelineTimingRecorder
    ) -> None:
        await run_text_branch(_ctx(), _transcript(GARBAGE_PL_ASR), Fakes())

        assert "transcript_audio" in [phase["name"] for phase in recorder.phases]

    async def test_should_stream_the_audio_layers_phase_events(self) -> None:
        events = await run_text_branch(_ctx(), _transcript(GARBAGE_PL_ASR), Fakes())

        assert _phases(events) == ["audio_transcription", "whisper_transcription"]


class TestUsableTranscripts:
    @pytest.mark.parametrize(
        ("text", "language"),
        [(HEBREW_CAPTIONS, "he"), (ENGLISH_CAPTIONS, "en")],
        ids=["hebrew-captions", "english-captions"],
    )
    async def test_should_keep_the_captions_and_probe_once_when_the_probe_says_usable(
        self, text: str, language: str
    ) -> None:
        fakes = Fakes()

        await run_text_branch(_ctx(), _transcript(text, language=language), fakes)

        assert (len(fakes.probe_llm.prompts), fakes.whisper.await_count) == (1, 0)

    async def test_should_not_fall_back_when_the_probe_gave_no_answer(self) -> None:
        fakes = Fakes()

        with patch.object(tier_probe, "parse_tier_probe", return_value=None):
            await run_text_branch(_ctx(), _transcript(GARBAGE_PL_ASR), fakes)

        fakes.whisper.assert_not_awaited()


class TestNoFallback:
    @pytest.mark.parametrize(
        ("category", "source", "origin"),
        [("music", "ytdlp", None), ("coding", "whisper", None), ("coding", "s3", "whisper")],
    )
    async def test_should_keep_the_transcript_and_record_the_unusable_answer(
        self, category: str, source: str, origin: str | None
    ) -> None:
        ctx, fakes = _ctx(category=category), Fakes()

        await run_text_branch(ctx, _transcript(GARBAGE_PL_ASR, source), fakes, origin)

        assert (fakes.whisper.await_count, _attempted(ctx)) == (0, [f"{source}-unusable"])

    async def test_should_plan_without_a_probe_when_no_fallback_runs(self) -> None:
        fakes = Fakes()

        await run_text_branch(_ctx(category="music"), _transcript(GARBAGE_PL_ASR), fakes)

        assert fakes.readers.plan_probe is None

    async def test_should_not_start_the_audio_pass_when_no_audio_layer_is_allowed(self) -> None:
        fakes = Fakes(whisper_enabled=False)

        events = await run_text_branch(_ctx(), _transcript(GARBAGE_PL_ASR), fakes)

        assert _phases(events) == []

    async def test_should_not_start_the_audio_pass_over_the_audio_duration_cap(self) -> None:
        ctx = _ctx(duration=2 * 3600)

        events = await run_text_branch(ctx, _transcript(GARBAGE_PL_ASR), Fakes())

        assert _phases(events) == []

    async def test_should_keep_the_captions_when_the_audio_pass_fails(self) -> None:
        ctx = _ctx()

        await run_text_branch(ctx, _transcript(GARBAGE_PL_ASR), Fakes(whisper=_whisper_failed()))

        assert (_source(ctx), _attempted(ctx)) == ("ytdlp", ["ytdlp-unusable", "whisper"])

    async def test_should_not_probe_again_when_the_audio_pass_fails(self) -> None:
        fakes = Fakes(whisper=_whisper_failed())

        await run_text_branch(_ctx(), _transcript(GARBAGE_PL_ASR), fakes)

        assert (len(fakes.probe_llm.prompts), fakes.readers.plan_probe) == (1, None)

    async def test_should_not_loop_when_the_second_probe_is_unusable_too(self) -> None:
        fakes = Fakes(probe_llm=_ProbeLLM(GARBAGE_MARKER, "useEffect"))

        await run_text_branch(_ctx(), _transcript(GARBAGE_PL_ASR), fakes)

        assert (len(fakes.probe_llm.prompts), fakes.whisper.await_count) == (2, 1)

    async def test_should_plan_without_a_probe_when_the_second_probe_is_unusable_too(
        self,
    ) -> None:
        ctx, fakes = _ctx(), Fakes(probe_llm=_ProbeLLM(GARBAGE_MARKER, "useEffect"))

        await run_text_branch(ctx, _transcript(GARBAGE_PL_ASR), fakes)

        assert (fakes.readers.plan_probe, _attempted(ctx)) == (
            None,
            ["ytdlp-unusable", "whisper-unusable"],
        )
