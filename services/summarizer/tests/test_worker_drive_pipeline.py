"""Regression tests for the worker's pipeline driver.

The bug fixed here: ``drive_pipeline`` previously called
``get_video_repository()`` and ``get_llm_service()`` with no arguments, which
explodes outside of FastAPI's dependency-injection machinery (those functions
expect their dependencies as ``Annotated[..., Depends(...)]`` parameters). The
surface was only ever hit when a job actually drove the pipeline — which the
existing runner tests never did, since they mock the pipeline callable.

These tests exercise the *construction* path inside ``drive_pipeline`` to
guarantee we don't regress.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pymongo.errors import AutoReconnect

from src.config import settings
from src.worker import pipeline as worker_pipeline
from src.worker.payload import ProviderConfig as WorkerProviderConfig
from src.worker.payload import VideoJobPayload

VALID_PAYLOAD = VideoJobPayload(
    videoSummaryId="abc123def456789012345678",
    youtubeId="dQw4w9WgXcQ",
    url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    userId="user-1",
    tier="free",
    priority=1,
    providers=None,
    bypassCache=False,
    requestId="req-1",
    attempt=1,
    createdAt="2026-05-18T12:00:00Z",
)


def _patch_common(repo_return_value: dict | None = None):
    """Return a list of patches for the common dependencies of drive_pipeline."""
    mock_client = MagicMock()
    mock_db = MagicMock()
    mock_client.get_default_database.return_value = mock_db
    mock_repo = MagicMock()
    mock_repo.get_video_summary.return_value = (
        {"_id": "v", "youtubeId": "y"} if repo_return_value is None else repo_return_value
    )

    return mock_client, mock_db, mock_repo


@pytest.mark.asyncio
async def test_drive_pipeline_constructs_repository_without_di():
    """Worker must build its own repository — FastAPI's Depends() doesn't run here."""
    mock_client, mock_db, mock_repo = _patch_common()

    with (
        patch.object(worker_pipeline, "get_mongo_client", return_value=mock_client),
        patch.object(
            worker_pipeline, "MongoDBVideoRepository", return_value=mock_repo
        ) as repo_ctor,
        patch.object(worker_pipeline, "get_llm_provider", return_value=MagicMock()),
        patch.object(worker_pipeline, "LLMService", return_value=MagicMock()),
        patch.object(
            worker_pipeline.pipeline_event_stream,
            "acquire_lock",
            new=AsyncMock(return_value=True),
        ),
        patch("src.routes.pipeline_broker.produce_to_broker", new=AsyncMock()),
        patch.object(worker_pipeline, "clear_override"),
    ):
        await worker_pipeline.drive_pipeline(VALID_PAYLOAD)

    # The whole point of the regression: repository is built with the
    # database, not via a no-arg FastAPI DI call.
    repo_ctor.assert_called_once_with(mock_db)


@pytest.mark.asyncio
async def test_drive_pipeline_uses_default_llm_provider_when_payload_has_none():
    """When payload.providers is None the worker reuses the cached provider."""
    mock_client, _, mock_repo = _patch_common()
    cached_provider = MagicMock(name="cached-llm-provider")

    with (
        patch.object(worker_pipeline, "get_mongo_client", return_value=mock_client),
        patch.object(worker_pipeline, "MongoDBVideoRepository", return_value=mock_repo),
        patch.object(worker_pipeline, "get_llm_provider", return_value=cached_provider) as gp,
        patch.object(worker_pipeline, "create_llm_provider") as cp,
        patch.object(worker_pipeline, "LLMService") as llm_ctor,
        patch.object(
            worker_pipeline.pipeline_event_stream,
            "acquire_lock",
            new=AsyncMock(return_value=True),
        ),
        patch("src.routes.pipeline_broker.produce_to_broker", new=AsyncMock()),
        patch.object(worker_pipeline, "clear_override"),
    ):
        await worker_pipeline.drive_pipeline(VALID_PAYLOAD)

    gp.assert_called_once()
    cp.assert_not_called()
    llm_ctor.assert_called_once_with(cached_provider)


@pytest.mark.asyncio
async def test_drive_pipeline_uses_custom_provider_when_payload_specifies_one():
    """When payload.providers is set the worker builds a per-job provider."""
    payload_with_providers = VALID_PAYLOAD.model_copy(
        update={"providers": WorkerProviderConfig(default="openai")},
    )
    mock_client, _, mock_repo = _patch_common()
    custom_provider = MagicMock(name="custom-llm-provider")

    with (
        patch.object(worker_pipeline, "get_mongo_client", return_value=mock_client),
        patch.object(worker_pipeline, "MongoDBVideoRepository", return_value=mock_repo),
        patch.object(worker_pipeline, "get_llm_provider") as gp,
        patch.object(worker_pipeline, "create_llm_provider", return_value=custom_provider) as cp,
        patch.object(worker_pipeline, "LLMService") as llm_ctor,
        patch.object(
            worker_pipeline.pipeline_event_stream,
            "acquire_lock",
            new=AsyncMock(return_value=True),
        ),
        patch("src.routes.pipeline_broker.produce_to_broker", new=AsyncMock()),
        patch.object(worker_pipeline, "clear_override"),
    ):
        await worker_pipeline.drive_pipeline(payload_with_providers)

    cp.assert_called_once()
    gp.assert_not_called()
    llm_ctor.assert_called_once_with(custom_provider)


@pytest.mark.asyncio
async def test_drive_pipeline_raises_when_mongo_row_missing():
    """A vanished videoSummary is a non-retryable error surfaced to the runner."""
    mock_client, _, mock_repo = _patch_common(repo_return_value={})
    mock_repo.get_video_summary.return_value = None  # row vanished

    with (
        patch.object(worker_pipeline, "get_mongo_client", return_value=mock_client),
        patch.object(worker_pipeline, "MongoDBVideoRepository", return_value=mock_repo),
        patch.object(worker_pipeline, "get_llm_provider", return_value=MagicMock()),
        patch.object(worker_pipeline, "LLMService", return_value=MagicMock()),
        patch.object(
            worker_pipeline.pipeline_event_stream,
            "acquire_lock",
            new=AsyncMock(return_value=True),
        ),
        patch("src.routes.pipeline_broker.produce_to_broker", new=AsyncMock()),
    ):
        with pytest.raises(RuntimeError, match="video_summary not found"):
            await worker_pipeline.drive_pipeline(VALID_PAYLOAD)


@pytest.mark.asyncio
async def test_drive_pipeline_forwards_bypass_cache_as_force_refresh():
    """bypassCache submissions must skip the youtubeId-keyed response cache —
    otherwise the fresh version row is instantly re-fed the stale payload."""
    mock_client, _mock_db, mock_repo = _patch_common()
    payload = VALID_PAYLOAD.model_copy(update={"bypass_cache": True})

    produce = AsyncMock()
    with (
        patch.object(worker_pipeline, "get_mongo_client", return_value=mock_client),
        patch.object(worker_pipeline, "MongoDBVideoRepository", return_value=mock_repo),
        patch.object(worker_pipeline, "get_llm_provider", return_value=MagicMock()),
        patch.object(worker_pipeline, "LLMService", return_value=MagicMock()),
        patch.object(
            worker_pipeline.pipeline_event_stream,
            "acquire_lock",
            new=AsyncMock(return_value=True),
        ),
        patch("src.routes.pipeline_broker.produce_to_broker", new=produce),
        patch.object(worker_pipeline, "clear_override"),
    ):
        await worker_pipeline.drive_pipeline(payload)

    produce.assert_awaited_once()
    assert produce.await_args is not None
    assert produce.await_args.kwargs["force_refresh"] is True


# ─── Duplicate runs (A4): a completed row is never re-run ────────────────────


@contextmanager
def _patched_driver(
    rows: list | Callable[[str], dict],
) -> Iterator[tuple[AsyncMock, AsyncMock, AsyncMock]]:
    """Patch drive_pipeline's deps; get_video_summary follows ``rows`` (a mock side_effect)."""
    mock_client, _mock_db, mock_repo = _patch_common()
    mock_repo.get_video_summary.side_effect = rows
    produce, acquire, release = AsyncMock(), AsyncMock(return_value=True), AsyncMock()
    with (
        patch.object(worker_pipeline, "get_mongo_client", return_value=mock_client),
        patch.object(worker_pipeline, "MongoDBVideoRepository", return_value=mock_repo),
        patch.object(worker_pipeline, "get_llm_provider", return_value=MagicMock()),
        patch.object(worker_pipeline, "LLMService", return_value=MagicMock()),
        patch.object(worker_pipeline.pipeline_event_stream, "acquire_lock", new=acquire),
        patch.object(worker_pipeline.pipeline_event_stream, "release_lock", new=release),
        patch("src.routes.pipeline_broker.produce_to_broker", new=produce),
        patch.object(worker_pipeline, "clear_override"),
    ):
        yield produce, acquire, release


async def _drive_with_rows(rows: list) -> tuple[AsyncMock, AsyncMock, AsyncMock]:
    """drive_pipeline where each get_video_summary call returns the next row."""
    with _patched_driver(rows) as mocks:
        await worker_pipeline.drive_pipeline(VALID_PAYLOAD)
    return mocks


@pytest.mark.asyncio
async def test_should_skip_a_job_whose_row_is_already_completed():
    produce, acquire, _release = await _drive_with_rows([{"_id": "v", "status": "completed"}])

    assert (produce.await_count, acquire.await_count) == (0, 0)


@pytest.mark.asyncio
async def test_should_skip_when_the_row_completed_while_the_lock_was_taken():
    """Another producer finished between the first read and the lock."""
    produce, _acquire, release = await _drive_with_rows(
        [{"_id": "v", "status": "pending"}, {"_id": "v", "status": "completed"}]
    )

    assert (produce.await_count, release.await_count) == (0, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["pending", "processing", "failed"])
async def test_should_run_a_row_that_is_not_completed(status: str):
    produce, _acquire, _release = await _drive_with_rows(
        [{"_id": "v", "status": status}, {"_id": "v", "status": status}]
    )

    produce.assert_awaited_once()


@pytest.mark.asyncio
async def test_should_run_when_the_recheck_cannot_read_mongo():
    produce, _acquire, _release = await _drive_with_rows(
        [{"_id": "v", "status": "pending"}, OSError("mongo hiccup")]
    )

    produce.assert_awaited_once()


# ─── Stale-version regen (G21): the API re-dispatches a completed row ────────


def _completed(version: str) -> dict:
    return {"_id": "v", "status": "completed", "pipelineVersion": version}


@pytest.mark.asyncio
async def test_should_run_a_completed_row_stamped_with_an_older_pipeline_version():
    """The API's stale-version regen re-dispatches WITHOUT resetting status."""
    old = _completed("v0-older-than-current")

    produce, _acquire, _release = await _drive_with_rows([old, old])

    produce.assert_awaited_once()


@pytest.mark.asyncio
async def test_should_skip_a_completed_row_stamped_with_the_current_pipeline_version():
    current = _completed(settings.PIPELINE_VERSION)

    produce, acquire, _release = await _drive_with_rows([current])

    assert (produce.await_count, acquire.await_count) == (0, 0)


@pytest.mark.asyncio
async def test_should_skip_after_lock_when_the_regen_finished_meanwhile():
    """A duplicate regen message: the row was re-stamped while we took the lock."""
    rows = [_completed("v0-older-than-current"), _completed(settings.PIPELINE_VERSION)]

    produce, _acquire, release = await _drive_with_rows(rows)

    assert (produce.await_count, release.await_count) == (0, 1)


# ─── Lock safety (G21): nothing between acquire and produce strands the lock ─


@pytest.mark.asyncio
async def test_should_run_and_hand_the_lock_to_the_producer_when_the_recheck_hits_autoreconnect():
    """produce_to_broker's finally owns the release once it runs — no double release."""
    produce, _acquire, release = await _drive_with_rows(
        [{"_id": "v", "status": "pending"}, AutoReconnect("primary stepped down")]
    )

    assert (produce.await_count, release.await_count) == (1, 0)


@pytest.mark.asyncio
async def test_should_release_the_lock_when_the_recheck_raises_unexpectedly():
    with _patched_driver([{"_id": "v", "status": "pending"}, ValueError("bad row")]) as mocks:
        with pytest.raises(ValueError, match="bad row"):
            await worker_pipeline.drive_pipeline(VALID_PAYLOAD)
    produce, _acquire, release = mocks

    assert (produce.await_count, release.await_count) == (0, 1)


@pytest.mark.asyncio
async def test_should_release_the_lock_when_cancelled_after_acquiring_it():
    entered, unblock = threading.Event(), threading.Event()
    reads = iter([{"_id": "v", "status": "pending"}])

    def _rows(_video_summary_id: str) -> dict:
        """First read returns at once; the post-lock re-check blocks until cancelled."""
        row = next(reads, None)
        if row is None:
            entered.set()
            unblock.wait(timeout=5)
            row = {"_id": "v", "status": "pending"}
        return row

    with _patched_driver(_rows) as (produce, _acquire, release):
        task = asyncio.create_task(worker_pipeline.drive_pipeline(VALID_PAYLOAD))
        try:
            while not entered.is_set():
                await asyncio.sleep(0.01)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            unblock.set()

    assert (produce.await_count, release.await_count) == (0, 1)
