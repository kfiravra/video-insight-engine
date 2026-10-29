"""Failing-test anchor for the worker runner. Full behaviour tested in
tests/test_worker_runner.py — that file exercises process_message under
success, validation, retry, and DLQ paths."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock


def test_runner_class_exists_and_processes_a_message() -> None:
    """Smoke test: a valid message acks; this fails before runner.py exists."""
    from src.worker.runner import WorkerRunner, JobOutcome

    assert WorkerRunner is not None
    assert JobOutcome.SUCCESS.value == "success"
    assert JobOutcome.RETRIED.value == "retried"
    assert JobOutcome.PERMANENT_FAILURE.value == "permanent_failure"

    payload = {
        "videoSummaryId": "abc123def456789012345678",
        "youtubeId": "dQw4w9WgXcQ",
        "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "userId": "u",
        "plan": "free",
        "priority": 1,
        "providers": None,
        "bypassCache": False,
        "requestId": "r",
        "attempt": 1,
        "createdAt": "2026-05-18T12:00:00Z",
    }
    msg = MagicMock()
    msg.body = json.dumps(payload).encode("utf-8")
    msg.headers = {}
    msg.ack = AsyncMock()
    msg.nack = AsyncMock()
    msg.reject = AsyncMock()
    pipeline = AsyncMock()
    republish = AsyncMock()

    import asyncio

    runner = WorkerRunner(run_pipeline=pipeline, republish=republish, max_retries=3)
    outcome = asyncio.run(runner.process_message(msg))
    assert outcome == JobOutcome.SUCCESS
    msg.ack.assert_awaited_once()
