"""``pipeline.timing`` seams for Whisper/Gemini transcription (G0 review p0.1 #2/#3).

Transcription calls the provider SDKs directly, so neither ``record_llm_call``
nor the LiteLLM failure path sees them; the audio downloads raise before the
success-path ``record_download``. These tests pin the hooks that close both gaps.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

from src.exceptions import TranscriptError
from src.models.schemas import ErrorCode
from src.services.pipeline import pipeline_timing
from src.services.pipeline.pipeline_timing import PipelineTimingRecorder, start_run_timing
from src.services.transcription import gemini_transcriber, whisper_transcriber
from src.services.transcription.usage import emit_transcription_usage

VIDEO_ID = "dQw4w9WgXcQ"


@pytest.fixture
def recorder() -> Iterator[PipelineTimingRecorder]:
    token = pipeline_timing._recorder_var.set(None)
    try:
        yield start_run_timing()
    finally:
        pipeline_timing._recorder_var.reset(token)


@pytest.fixture
def quiet_usage_sinks() -> Iterator[None]:
    """Keep the ledger + Langfuse emits off the network; timing is under test."""
    with (
        patch("src.services.transcription.usage.record_manual_usage", MagicMock()),
        patch("src.services.transcription.usage.log_generation", MagicMock()),
    ):
        yield


@pytest.mark.usefixtures("quiet_usage_sinks")
class TestTranscriptionCalls:
    def test_should_count_whisper_cost_in_timing_cost_when_call_succeeds(
        self, recorder: PipelineTimingRecorder
    ) -> None:
        emit_transcription_usage(
            provider="openai",
            model="whisper-1",
            feature="summarize:transcript:whisper",
            audio_seconds=600.0,
            duration_ms=4200,
        )

        doc = recorder.to_document(tabs_planned=0, tabs_assembled=0)
        call = doc["llmCalls"][0]
        assert (call["span"], call["feature"], call["wallMs"], doc["costUsd"] > 0) == (
            "transcription:openai",
            "summarize:transcript:whisper",
            4200,
            True,
        )

    def test_should_carry_gemini_tokens_when_call_succeeds(
        self, recorder: PipelineTimingRecorder
    ) -> None:
        emit_transcription_usage(
            provider="google",
            model="gemini-2.5-flash-lite",
            feature="summarize:transcript:gemini",
            tokens_in=50_000,
            tokens_out=4_000,
        )

        call = recorder.llm_calls[0]
        assert (call["inputTokens"], call["outputTokens"]) == (50_000, 4_000)

    def test_should_record_failure_with_exception_class_when_call_fails(
        self, recorder: PipelineTimingRecorder
    ) -> None:
        emit_transcription_usage(
            provider="openai",
            model="whisper-1",
            feature="summarize:transcript:whisper",
            success=False,
            error=TranscriptError("boom", ErrorCode.DOWNLOAD_ERROR),
        )

        assert (recorder.llm_calls, [f["error"] for f in recorder.llm_failures]) == (
            [],
            ["TranscriptError"],
        )

    def test_should_be_noop_outside_a_run(self) -> None:
        token = pipeline_timing._recorder_var.set(None)
        try:
            emit_transcription_usage(
                provider="openai", model="whisper-1", feature="summarize:transcript:whisper"
            )
            assert pipeline_timing.current_timing() is None
        finally:
            pipeline_timing._recorder_var.reset(token)

    async def test_whisper_fallback_should_land_in_llm_calls_with_wall_time(
        self, recorder: PipelineTimingRecorder, tmp_path: Path
    ) -> None:
        audio_path = tmp_path / "v.mp3"
        audio_path.write_bytes(b"fake audio")
        result = {"text": "hi", "segments": [], "duration": 120.0}
        with (
            patch.object(whisper_transcriber, "_download_audio_sync", return_value=audio_path),
            patch.object(whisper_transcriber, "_transcribe_sync", return_value=result),
        ):
            await whisper_transcriber.transcribe_with_whisper(VIDEO_ID)

        assert [(c["span"], c["wallMs"] >= 0) for c in recorder.llm_calls] == [
            ("transcription:openai", True)
        ]

    async def test_whisper_fallback_should_record_failure_class_when_it_raises(
        self, recorder: PipelineTimingRecorder
    ) -> None:
        boom = TranscriptError("blocked", ErrorCode.DOWNLOAD_ERROR)
        with (
            patch.object(whisper_transcriber, "_download_audio_sync", side_effect=boom),
            pytest.raises(TranscriptError),
        ):
            await whisper_transcriber.transcribe_with_whisper(VIDEO_ID)

        assert [f["error"] for f in recorder.llm_failures] == ["TranscriptError"]


class TestFailedAudioDownload:
    @pytest.mark.parametrize(
        ("module", "download_fn", "purpose"),
        [
            (whisper_transcriber, "_download_audio_sync", "whisper"),
            (gemini_transcriber, "_download_audio_raw_sync", "gemini"),
        ],
    )
    def test_should_record_failed_download_when_ytdlp_gives_up(
        self,
        recorder: PipelineTimingRecorder,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        module: ModuleType,
        download_fn: str,
        purpose: str,
    ) -> None:
        monkeypatch.setattr(module, "TEMP_DIR", tmp_path)
        boom = TranscriptError("Failed to download audio", ErrorCode.VIDEO_UNAVAILABLE)
        monkeypatch.setattr(module, "download_youtube_audio", MagicMock(side_effect=boom))

        with pytest.raises(TranscriptError):
            getattr(module, download_fn)(VIDEO_ID)

        assert [(d["kind"], d["purpose"], d["ok"], d["bytes"]) for d in recorder.downloads] == [
            ("audio", purpose, False, None)
        ]
