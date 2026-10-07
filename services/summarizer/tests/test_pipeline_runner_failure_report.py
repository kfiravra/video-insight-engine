"""Failure reporting for a pipeline run (src.routes.pipeline_failures + runner wiring).

The SSE-direct pipeline path used to swallow unexpected exceptions into an
SSE ``error`` frame with nothing reaching Sentry. The classified branches
(transcript / rate-limit / timeout / provider error) moved out of
``pipeline_runner`` into a table; these tests pin that every class still maps
to the same row code and user-facing text.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import structlog
from litellm.exceptions import APIError, RateLimitError, Timeout

from src.exceptions import TranscriptError
from src.models.schemas import ErrorCode, ProcessingStatus
from src.routes import pipeline_failures as pf
from src.routes import pipeline_runner


class TestReportUnexpectedFailure:
    def setup_method(self) -> None:
        structlog.contextvars.clear_contextvars()

    def teardown_method(self) -> None:
        structlog.contextvars.clear_contextvars()

    def test_should_capture_exception_tagged_sse_direct_when_no_worker_context(self):
        exc = ValueError("boom")
        with patch.object(pf, "capture_exception_with_context") as capture:
            ref = pf.report_unexpected_failure(exc, "vid-1", 12.5)

        capture.assert_called_once()
        assert capture.call_args.args[0] is exc
        tags = capture.call_args.kwargs
        assert tags["videoSummaryId"] == "vid-1"
        assert tags["errorRef"] == ref
        assert tags["outcome"] == "pipeline_failed"
        assert tags["path"] == "sse-direct"
        assert tags["attempt"] is None

    def test_should_tag_worker_path_with_attempt_when_bound(self):
        structlog.contextvars.bind_contextvars(request_id="r1", attempt=2)
        with patch.object(pf, "capture_exception_with_context") as capture:
            pf.report_unexpected_failure(RuntimeError("x"), "vid-2", 1.0)

        tags = capture.call_args.kwargs
        assert tags["path"] == "worker"
        assert tags["attempt"] == "2"

    def test_should_return_short_error_ref(self):
        with patch.object(pf, "capture_exception_with_context"):
            ref = pf.report_unexpected_failure(RuntimeError("x"), "vid-3", 0.0)
        assert isinstance(ref, str) and len(ref) == 8


def _litellm(exc_type: type[Exception]) -> Exception:
    if exc_type is APIError:
        return APIError(status_code=500, message="x", llm_provider="anthropic", model="m")
    return exc_type(message="x", llm_provider="anthropic", model="m")


class TestClassifyRunFailure:
    def test_should_keep_transcript_code_and_text_when_transcript_fails(self) -> None:
        exc = TranscriptError("No captions", ErrorCode.NO_TRANSCRIPT)
        failure = pf.classify_run_failure(exc, "vid", 1.0)
        assert (failure.code, failure.user_message) == (ErrorCode.NO_TRANSCRIPT, str(exc))

    @pytest.mark.parametrize(
        ("exc_type", "code", "message"),
        [
            (
                RateLimitError,
                ErrorCode.RATE_LIMITED,
                "AI service rate limited. Please try again in a moment.",
            ),
            (Timeout, ErrorCode.LLM_ERROR, "Request took too long. Please try again."),
            (APIError, ErrorCode.LLM_ERROR, "AI service error. Please try again."),
        ],
    )
    def test_should_sanitize_message_when_llm_provider_fails(
        self, exc_type: type[Exception], code: ErrorCode, message: str
    ) -> None:
        with patch.object(pf, "capture_exception_with_context") as capture:
            failure = pf.classify_run_failure(_litellm(exc_type), "vid", 1.0)

        assert (failure.code, failure.user_message, capture.called) == (code, message, False)

    def test_should_report_to_sentry_with_ref_when_failure_is_unclassified(self) -> None:
        with patch.object(pf, "capture_exception_with_context") as capture:
            failure = pf.classify_run_failure(ValueError("bug"), "vid", 1.0)

        ref = capture.call_args.kwargs["errorRef"]
        assert (failure.code, failure.user_message) == (
            ErrorCode.UNKNOWN_ERROR,
            f"An unexpected error occurred (ref: {ref}).",
        )


async def test_runner_should_fail_row_and_emit_sanitized_error_when_rate_limited() -> None:
    async def _phases_raise(*_args: object, **_kwargs: object):
        raise _litellm(RateLimitError)
        yield  # pragma: no cover — makes this an async generator

    repository = MagicMock()
    send_status = AsyncMock()
    with (
        patch.object(pipeline_runner.settings, "REDIS_ENABLED", False),
        patch.object(pipeline_runner, "_run_pipeline_phases", new=_phases_raise),
        patch.object(pipeline_runner, "send_video_status", new=send_status),
        patch.object(pipeline_runner, "clear_override", new=MagicMock()),
    ):
        events = [
            ev
            async for ev in pipeline_runner.stream_summarization(
                "vsum_rl", {"youtubeId": "yt_rl", "status": "pending"}, repository, MagicMock()
            )
        ]

    safe_msg = "AI service rate limited. Please try again in a moment."
    status_call = repository.update_status.call_args_list[-1].args
    assert (status_call[1], status_call[3]) == (ProcessingStatus.FAILED, ErrorCode.RATE_LIMITED)
    assert send_status.call_args_list[-1].kwargs["error"] == safe_msg
    assert json.loads(events[-1].removeprefix("data: ")) == {
        "event": "error",
        "message": safe_msg,
        "code": "RATE_LIMITED",
    }
