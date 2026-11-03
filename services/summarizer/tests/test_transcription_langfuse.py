"""Tests for the Langfuse generation of a transcription call (Whisper / Gemini).

Runs the real Langfuse v4 SDK against memory (``langfuse_capture``):
- the generation opened at the call's start is finished by the usage emit, with cost
- a failure before the call still shows as one ERROR generation
- a cancelled call (the fetcher's timeout) ends its generation as a WARNING instead
  of leaving it open, where it would never be exported
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from llm_common.observability.testing import LangfuseCapture

from src.services.observability import run_trace
from src.services.transcription import gemini_transcriber
from src.services.transcription.usage import (
    cancel_transcription_generation,
    emit_transcription_usage,
    open_transcription_generation,
)
from tests.test_gemini_transcriber import _make_mock_genai_client


@pytest.fixture(autouse=True)
def _quiet_ledger():
    with patch("src.services.transcription.usage.record_manual_usage", MagicMock()):
        yield


class TestTranscriptionGeneration:
    async def test_should_finish_the_open_generation_with_cost_when_the_call_succeeds(
        self, langfuse_capture: LangfuseCapture
    ) -> None:
        async with run_trace(name="pipeline:v"):
            handle = open_transcription_generation(provider="openai", model="whisper-1")
            await asyncio.sleep(0.01)
            emit_transcription_usage(
                provider="openai",
                model="whisper-1",
                feature="summarize:transcript:whisper",
                audio_seconds=120.0,
                duration_ms=10,
                handle=handle,
            )

        (gen,) = langfuse_capture.generations("transcription:openai")
        assert gen.level is None
        assert gen.cost is not None and gen.cost["total"] > 0
        assert gen.metadata["audioSeconds"] == 120.0

    async def test_should_log_one_error_generation_when_the_call_failed_before_it_opened(
        self, langfuse_capture: LangfuseCapture
    ) -> None:
        async with run_trace(name="pipeline:v"):
            emit_transcription_usage(
                provider="google",
                model="gemini-2.5-flash-lite",
                feature="summarize:transcript:gemini",
                success=False,
                error=OSError("download failed"),
            )

        (gen,) = langfuse_capture.generations("transcription:google")
        assert (gen.level, gen.attr("status_message")) == ("ERROR", "OSError: download failed")

    async def test_should_end_the_generation_as_a_warning_when_cancelled(
        self, langfuse_capture: LangfuseCapture
    ) -> None:
        async with run_trace(name="pipeline:v"):
            cancel_transcription_generation(
                open_transcription_generation(provider="openai", model="whisper-1")
            )

        (gen,) = langfuse_capture.generations("transcription:openai")
        assert (gen.level, gen.attr("status_message")) == ("WARNING", "cancelled")


@patch("src.services.transcription.gemini_transcriber.settings.GEMINI_API_KEY", "test-key")
class TestGeminiCancelled:
    async def test_should_export_the_generation_when_the_fetcher_times_the_call_out(
        self, tmp_path: Path, langfuse_capture: LangfuseCapture
    ) -> None:
        audio = tmp_path / "a.webm"
        audio.write_bytes(b"fake audio")
        client = _make_mock_genai_client("[]")

        async def _never_answers(**_kwargs: object) -> None:
            await asyncio.Event().wait()

        client.aio.models.generate_content = _never_answers

        with (
            patch.object(gemini_transcriber.genai, "Client", return_value=client),
            patch.object(gemini_transcriber, "_download_audio_raw_sync", return_value=audio),
        ):
            async with run_trace(name="pipeline:v"):
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(gemini_transcriber.transcribe_with_gemini("vid"), 0.2)

        (gen,) = langfuse_capture.generations("transcription:google")
        assert (gen.level, gen.attr("status_message")) == ("WARNING", "cancelled")
