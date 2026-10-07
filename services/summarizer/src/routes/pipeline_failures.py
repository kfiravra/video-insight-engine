"""Failure classification for a pipeline run (:mod:`src.routes.pipeline_runner`).

Maps the exception that ended a run to the error code stored on the row and
the message shown to the user. Transcript failures carry their own code and
user-safe text; LLM provider failures get fixed, sanitized text (raw LiteLLM
strings leak provider/model/endpoint internals); anything else is an
unclassified bug and goes to Sentry with a short ref the user can quote.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

import structlog
from litellm.exceptions import APIError as LitellmAPIError
from litellm.exceptions import RateLimitError
from litellm.exceptions import Timeout as LitellmTimeout
from llm_common.sentry_init import capture_exception_with_context

from src.exceptions import TranscriptError
from src.models.schemas import ErrorCode

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunFailure:
    """How a failed run is reported: row error code + user-facing message."""

    code: ErrorCode
    user_message: str


# Checked in order — (exception, log label, error code, user message, log level).
_LLM_FAILURES: tuple[tuple[type[Exception], str, ErrorCode, str, int], ...] = (
    (
        RateLimitError,
        "RateLimitError",
        ErrorCode.RATE_LIMITED,
        "AI service rate limited. Please try again in a moment.",
        logging.WARNING,
    ),
    (
        LitellmTimeout,
        "Timeout",
        ErrorCode.LLM_ERROR,
        "Request took too long. Please try again.",
        logging.WARNING,
    ),
    (
        LitellmAPIError,
        "APIError",
        ErrorCode.LLM_ERROR,
        "AI service error. Please try again.",
        logging.ERROR,
    ),
)


def report_unexpected_failure(exc: Exception, video_summary_id: str, elapsed: float) -> str:
    """Log + Sentry-capture an unclassified pipeline exception; return its ref.

    The classified branches (transcript, rate-limit, timeout, provider error)
    are operational and stay log-only. This branch is where real bugs land —
    and until now it was the one place they were swallowed into an SSE
    ``error`` frame with no exception reaching Sentry. The SSE-direct path
    (dev override / SSE client winning the producer lock) has no other
    capture point; under the worker the DLQ capture in ``runner.py`` only
    sees a synthetic RuntimeError, so this is also where the real traceback
    comes from. ``attempt`` is bound by the worker only, so its presence
    tells the two paths apart in Sentry.
    """
    error_ref = str(uuid.uuid4())[:8]
    logger.error(
        "[pipeline] FAILED video_id=%s error=%s ref=%s total=%.1fs",
        video_summary_id,
        type(exc).__name__,
        error_ref,
        elapsed,
        exc_info=True,
    )
    bound = structlog.contextvars.get_contextvars()
    attempt = bound.get("attempt") if isinstance(bound, dict) else None
    capture_exception_with_context(
        exc,
        videoSummaryId=video_summary_id,
        errorRef=error_ref,
        outcome="pipeline_failed",
        path="worker" if attempt is not None else "sse-direct",
        attempt=str(attempt) if attempt is not None else None,
    )
    return error_ref


def classify_run_failure(exc: Exception, video_summary_id: str, elapsed: float) -> RunFailure:
    """Log the failure at the severity its class deserves and say how to report it."""
    if isinstance(exc, TranscriptError):
        logger.info(
            "[pipeline] FAILED video_id=%s error=TranscriptError total=%.1fs",
            video_summary_id,
            elapsed,
        )
        return RunFailure(exc.code, str(exc))
    for exc_type, label, code, user_message, level in _LLM_FAILURES:
        if isinstance(exc, exc_type):
            logger.log(
                level,
                "[pipeline] FAILED video_id=%s error=%s total=%.1fs",
                video_summary_id,
                label,
                elapsed,
            )
            return RunFailure(code, user_message)
    error_ref = report_unexpected_failure(exc, video_summary_id, elapsed)
    return RunFailure(ErrorCode.UNKNOWN_ERROR, f"An unexpected error occurred (ref: {error_ref}).")
