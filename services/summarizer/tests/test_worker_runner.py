"""Tests for the worker runner — job handling, retry/DLQ, lock semantics."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.worker.payload import VideoJobPayload
from src.worker.runner import (
    MAX_RETRY_BACKOFF_SECONDS,
    JobOutcome,
    WorkerRunner,
    retry_backoff_seconds,
)

VALID_PAYLOAD_DICT = {
    "videoSummaryId": "abc123def456789012345678",
    "youtubeId": "dQw4w9WgXcQ",
    "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "userId": "user-1",
    "plan": "free",
    "priority": 1,
    "providers": None,
    "bypassCache": False,
    "requestId": "req-1",
    "attempt": 1,
    "createdAt": "2026-05-18T12:00:00Z",
}


def _make_message(body: bytes, headers: dict | None = None):
    """Create a mock aio-pika message with ack/nack/reject hooks."""
    msg = MagicMock()
    msg.body = body
    msg.headers = headers or {}
    msg.ack = AsyncMock()
    msg.nack = AsyncMock()
    msg.reject = AsyncMock()
    return msg


@pytest.fixture
def fake_pipeline():
    """Stub for the pipeline driver — records calls and resolves immediately."""

    async def runner(payload: VideoJobPayload) -> None:  # pragma: no cover -- monkeypatched
        pass

    return AsyncMock(side_effect=runner)


@pytest.fixture
def fake_republish():
    """Stub for the retry-republish path."""
    return AsyncMock()


class TestProcessMessage:
    """`WorkerRunner.process_message` is the heart of the consumer loop."""

    async def test_tags_the_sentry_transaction_with_the_plan(
        self, fake_pipeline, fake_republish, monkeypatch
    ):
        transaction = MagicMock()
        monkeypatch.setattr("src.worker.runner.pipeline_stage_transaction", transaction)
        runner = WorkerRunner(run_pipeline=fake_pipeline, republish=fake_republish, max_retries=3)
        paid = {**VALID_PAYLOAD_DICT, "plan": "paid", "priority": 5}

        await runner.process_message(_make_message(json.dumps(paid).encode("utf-8")))

        assert transaction.call_args.kwargs["plan"] == "paid"

    async def test_acks_on_success(self, fake_pipeline, fake_republish):
        runner = WorkerRunner(
            run_pipeline=fake_pipeline,
            republish=fake_republish,
            max_retries=3,
        )
        message = _make_message(json.dumps(VALID_PAYLOAD_DICT).encode("utf-8"))

        outcome = await runner.process_message(message)

        assert outcome == JobOutcome.SUCCESS
        message.ack.assert_awaited_once()
        message.nack.assert_not_called()
        fake_pipeline.assert_awaited_once()
        fake_republish.assert_not_called()

    async def test_rejects_invalid_payload_to_dlq(self, fake_pipeline, fake_republish):
        runner = WorkerRunner(
            run_pipeline=fake_pipeline,
            republish=fake_republish,
            max_retries=3,
        )
        bad = {**VALID_PAYLOAD_DICT, "youtubeId": "tooshort"}
        message = _make_message(json.dumps(bad).encode("utf-8"))

        outcome = await runner.process_message(message)

        assert outcome == JobOutcome.PERMANENT_FAILURE
        message.reject.assert_awaited_once_with(requeue=False)
        fake_pipeline.assert_not_called()

    async def test_republishes_on_transient_failure(self, fake_pipeline, fake_republish):
        fake_pipeline.side_effect = RuntimeError("LLM timeout")
        runner = WorkerRunner(
            run_pipeline=fake_pipeline,
            republish=fake_republish,
            max_retries=3,
        )
        message = _make_message(json.dumps(VALID_PAYLOAD_DICT).encode("utf-8"))

        outcome = await runner.process_message(message)

        assert outcome == JobOutcome.RETRIED
        fake_republish.assert_awaited_once()
        # republish receives the payload with attempt incremented to 2
        forwarded: VideoJobPayload = fake_republish.await_args.args[0]
        assert forwarded.attempt == 2
        message.ack.assert_awaited_once()  # original is acked, retry is a new message

    async def test_sends_to_dlq_after_max_attempts(self, fake_pipeline, fake_republish):
        fake_pipeline.side_effect = RuntimeError("LLM timeout")
        runner = WorkerRunner(
            run_pipeline=fake_pipeline,
            republish=fake_republish,
            max_retries=3,
        )
        # Already on attempt 3 — next failure is permanent.
        attempt3 = {**VALID_PAYLOAD_DICT, "attempt": 3}
        message = _make_message(json.dumps(attempt3).encode("utf-8"))

        outcome = await runner.process_message(message)

        assert outcome == JobOutcome.PERMANENT_FAILURE
        fake_republish.assert_not_called()
        message.reject.assert_awaited_once_with(requeue=False)

    async def test_rejects_garbage_body_without_calling_pipeline(
        self, fake_pipeline, fake_republish
    ):
        runner = WorkerRunner(
            run_pipeline=fake_pipeline,
            republish=fake_republish,
            max_retries=3,
        )
        message = _make_message(b"\xff\xfe not-utf8")
        outcome = await runner.process_message(message)
        assert outcome == JobOutcome.PERMANENT_FAILURE
        fake_pipeline.assert_not_called()
        message.reject.assert_awaited_once_with(requeue=False)

    async def test_retry_preserves_priority_and_request_id(self, fake_pipeline, fake_republish):
        # Hardening check: republished payload must keep plan/priority/requestId
        # so a paid run's retry doesn't drop to free priority.
        fake_pipeline.side_effect = RuntimeError("transient")
        runner = WorkerRunner(
            run_pipeline=fake_pipeline,
            republish=fake_republish,
            max_retries=3,
        )
        paid = {**VALID_PAYLOAD_DICT, "plan": "paid", "priority": 5, "requestId": "req-keep"}
        message = _make_message(json.dumps(paid).encode("utf-8"))

        outcome = await runner.process_message(message)
        assert outcome == JobOutcome.RETRIED
        forwarded: VideoJobPayload = fake_republish.await_args.args[0]
        assert forwarded.plan == "paid"
        assert forwarded.priority == 5
        assert forwarded.request_id == "req-keep"
        assert forwarded.attempt == 2


def _payload(attempt: int) -> bytes:
    return json.dumps({**VALID_PAYLOAD_DICT, "attempt": attempt}).encode("utf-8")


def _message(body: bytes):
    return _make_message(body)


class TestRetryBackoff:
    """WORKER_RETRY_BACKOFF_SECONDS was dead config — retries republished instantly."""

    @pytest.mark.parametrize(
        ("base", "attempt", "expected"),
        [
            (5.0, 1, 5.0),
            (5.0, 2, 10.0),
            (5.0, 3, 20.0),
            (0.0, 2, 0.0),
            (-1.0, 2, 0.0),
            (100.0, 4, MAX_RETRY_BACKOFF_SECONDS),
        ],
    )
    def test_should_double_per_attempt_and_cap(self, base, attempt, expected):
        assert retry_backoff_seconds(base, attempt) == expected

    async def test_should_wait_before_republish_on_retry(self, fake_pipeline, fake_republish):
        fake_pipeline.side_effect = RuntimeError("transient")
        sleep = AsyncMock()
        order: list[str] = []
        sleep.side_effect = lambda s: order.append(f"sleep:{s}")
        fake_republish.side_effect = lambda _p: order.append("republish")
        runner = WorkerRunner(
            run_pipeline=fake_pipeline,
            republish=fake_republish,
            max_retries=3,
            retry_backoff_seconds=5.0,
            sleep=sleep,
        )
        msg = _message(_payload(attempt=2))

        outcome = await runner.process_message(msg)

        assert outcome == JobOutcome.RETRIED
        assert order == ["sleep:10.0", "republish"]
        msg.ack.assert_awaited_once()

    async def test_should_not_wait_when_backoff_disabled(self, fake_pipeline, fake_republish):
        fake_pipeline.side_effect = RuntimeError("transient")
        sleep = AsyncMock()
        runner = WorkerRunner(
            run_pipeline=fake_pipeline,
            republish=fake_republish,
            max_retries=3,
            retry_backoff_seconds=0.0,
            sleep=sleep,
        )

        await runner.process_message(_message(_payload(attempt=1)))

        sleep.assert_not_awaited()
        fake_republish.assert_awaited_once()

    async def test_should_not_wait_before_dlq(self, fake_pipeline, fake_republish):
        fake_pipeline.side_effect = RuntimeError("permanent")
        sleep = AsyncMock()
        runner = WorkerRunner(
            run_pipeline=fake_pipeline,
            republish=fake_republish,
            max_retries=3,
            retry_backoff_seconds=5.0,
            sleep=sleep,
        )
        msg = _message(_payload(attempt=3))

        outcome = await runner.process_message(msg)

        assert outcome == JobOutcome.PERMANENT_FAILURE
        sleep.assert_not_awaited()
        msg.reject.assert_awaited_once_with(requeue=False)
