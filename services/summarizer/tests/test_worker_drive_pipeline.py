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

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.worker import pipeline as worker_pipeline
from src.worker.payload import ProviderConfig as WorkerProviderConfig, VideoJobPayload


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
    assert produce.await_args.kwargs["force_refresh"] is True


# ─── Duplicate runs (A4): a completed row is never re-run ────────────────────


async def _drive_with_rows(rows: list[dict]) -> tuple[AsyncMock, AsyncMock, AsyncMock]:
    """drive_pipeline where each get_video_summary call returns the next row."""
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
        await worker_pipeline.drive_pipeline(VALID_PAYLOAD)
    return produce, acquire, release


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
