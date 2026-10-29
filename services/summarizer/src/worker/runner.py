"""RabbitMQ-driven worker for the video summarization pipeline.

The runner separates **process_message** (pure async function, easy to test)
from the broker/loop concerns. Tests inject stubs for ``run_pipeline`` and
``republish``; the entrypoint (``__main__``) wires the real pipeline and an
aio-pika publisher.

Retry policy (matches docs/INFRASTRUCTURE.md):
- Validation error → reject(requeue=False) → DLX → DLQ
- Pipeline error & ``attempt < max_retries`` → republish with attempt+1,
  ack the original (treated as handled — the retry is a new message)
- Pipeline error & ``attempt >= max_retries`` → reject(requeue=False) → DLQ
"""

from __future__ import annotations

import asyncio
import enum
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from llm_common.sentry_init import (
    capture_exception_with_context,
    pipeline_stage_transaction,
)
from pydantic import ValidationError

from src.worker.payload import VideoJobPayload

logger = logging.getLogger(__name__)


class JobOutcome(enum.Enum):
    """What happened to a single message — mostly for tests/observability."""

    SUCCESS = "success"
    RETRIED = "retried"
    PERMANENT_FAILURE = "permanent_failure"


PipelineDriver = Callable[[VideoJobPayload], Awaitable[None]]
RepublishHook = Callable[[VideoJobPayload], Awaitable[None]]
SleepHook = Callable[[float], Awaitable[None]]

# Ceiling on one retry delay so a misconfigured base can't park a consumer
# slot for minutes (the message stays unacked — and thus redeliverable —
# for the whole wait).
MAX_RETRY_BACKOFF_SECONDS = 120.0


def retry_backoff_seconds(base_seconds: float, attempt: int) -> float:
    """Exponential delay before republishing: base × 2^(attempt-1), capped.

    ``attempt`` is the attempt that just failed (1-based), so the first retry
    waits ``base``, the second ``2·base``, … Zero/negative base disables
    the wait entirely.
    """
    if base_seconds <= 0:
        return 0.0
    return min(base_seconds * (2 ** max(attempt - 1, 0)), MAX_RETRY_BACKOFF_SECONDS)


class WorkerRunner:
    """Drives messages through the pipeline with retry + DLQ semantics."""

    def __init__(
        self,
        run_pipeline: PipelineDriver,
        republish: RepublishHook,
        max_retries: int,
        retry_backoff_seconds: float = 0.0,
        sleep: SleepHook = asyncio.sleep,
    ) -> None:
        self._run_pipeline = run_pipeline
        self._republish = republish
        self._max_retries = max_retries
        self._retry_backoff_seconds = retry_backoff_seconds
        self._sleep = sleep

    async def process_message(self, message: Any) -> JobOutcome:
        """Handle a single aio-pika IncomingMessage.

        Always exits with the message in a terminal state (acked or rejected).
        Returns the outcome so the loop can update metrics.
        """
        try:
            data = json.loads(message.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            logger.error("worker_payload_decode_failed: %s", e)
            await message.reject(requeue=False)
            return JobOutcome.PERMANENT_FAILURE

        try:
            payload = VideoJobPayload.model_validate(data)
        except ValidationError as e:
            logger.error("worker_payload_invalid: %s", e.errors())
            await message.reject(requeue=False)
            return JobOutcome.PERMANENT_FAILURE

        # Bind ids on the structlog contextvar stack so every log line emitted
        # during pipeline execution (including code that doesn't have a payload
        # in scope) is tagged with the request_id. Cleared in `finally` so the
        # next job on this worker starts with an empty context.
        structlog.contextvars.bind_contextvars(
            request_id=payload.request_id,
            video_summary_id=payload.video_summary_id,
            youtube_id=payload.youtube_id,
            user_id=payload.user_id,
            attempt=payload.attempt,
        )

        try:
            # One transaction per job — sampled per SENTRY_TRACES_SAMPLE_RATE.
            # Let pipeline exceptions bubble out of the with-block so Sentry's
            # transaction ``__exit__`` records ``internal_error`` status on the
            # trace (matching the actual outcome); then catch outside the with
            # to dispatch retry vs. DLQ. ``_handle_failure`` still emits the
            # Sentry *event* only for terminal failures, so retries don't
            # generate alert noise.
            # When Sentry is disabled the context manager is a no-op.
            pipeline_exc: Exception | None = None
            try:
                with pipeline_stage_transaction(
                    "worker.process_message",
                    videoSummaryId=payload.video_summary_id,
                    youtubeId=payload.youtube_id,
                    plan=payload.plan,
                ):
                    await self._run_pipeline(payload)
            except Exception as exc:
                pipeline_exc = exc

            if pipeline_exc is not None:
                return await self._handle_failure(message, payload, pipeline_exc)

            await message.ack()
            logger.info(
                "worker_job_done video=%s attempt=%d request=%s",
                payload.video_summary_id,
                payload.attempt,
                payload.request_id,
            )
            return JobOutcome.SUCCESS
        finally:
            structlog.contextvars.clear_contextvars()

    async def _handle_failure(
        self,
        message: Any,
        payload: VideoJobPayload,
        exc: Exception,
    ) -> JobOutcome:
        if payload.attempt >= self._max_retries:
            logger.error(
                "worker_job_dlq video=%s attempt=%d max=%d error=%s",
                payload.video_summary_id,
                payload.attempt,
                self._max_retries,
                exc,
                exc_info=exc,
            )
            # Terminal failure — fan the exception out to Sentry so the
            # operator sees a single event per dead-lettered job (rather than
            # one per retry, which would be alert noise).
            capture_exception_with_context(
                exc,
                videoSummaryId=payload.video_summary_id,
                youtubeId=payload.youtube_id,
                requestId=payload.request_id,
                attempt=str(payload.attempt),
                outcome="dlq",
            )
            await message.reject(requeue=False)
            return JobOutcome.PERMANENT_FAILURE

        retry_payload = payload.model_copy(update={"attempt": payload.attempt + 1})
        delay = retry_backoff_seconds(self._retry_backoff_seconds, payload.attempt)
        logger.warning(
            "worker_job_retry video=%s attempt=%d->next=%d backoff_s=%.1f error=%s",
            payload.video_summary_id,
            payload.attempt,
            retry_payload.attempt,
            delay,
            exc,
        )
        # Wait BEFORE republishing, with the original still unacked: a worker
        # that dies mid-wait loses nothing (the broker redelivers), and an
        # upstream outage gets breathing room instead of 3 instant hits.
        if delay > 0:
            await self._sleep(delay)
        await self._republish(retry_payload)
        await message.ack()
        return JobOutcome.RETRIED
