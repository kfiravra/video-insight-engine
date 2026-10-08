"""The worker loads the embedding model at start (pipeline-1min 1d.5, A4).

Like the API's lifespan, so the first job's Qdrant writes do not pay the
SentenceTransformer load. Non-fatal and skipped when Qdrant is off.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.services.vector import embedding
from src.worker import __main__ as entry


async def test_should_load_the_model_when_qdrant_is_on() -> None:
    load = MagicMock()
    with (
        patch.object(entry.settings, "QDRANT_ENABLED", True),
        patch.object(embedding, "_get_model", load),
    ):
        await entry._preload_embeddings()

    load.assert_called_once()


async def test_should_skip_the_model_when_qdrant_is_off() -> None:
    load = MagicMock()
    with (
        patch.object(entry.settings, "QDRANT_ENABLED", False),
        patch.object(embedding, "_get_model", load),
    ):
        await entry._preload_embeddings()

    load.assert_not_called()


async def test_should_keep_the_worker_up_when_the_model_fails_to_load() -> None:
    with (
        patch.object(entry.settings, "QDRANT_ENABLED", True),
        patch.object(embedding, "_get_model", MagicMock(side_effect=OSError("no weights"))),
    ):
        outcome = await entry._preload_embeddings()

    assert outcome is None  # logged, not raised


@pytest.mark.asyncio
async def test_main_should_preload_before_connecting() -> None:
    order: list[str] = []

    async def _preload() -> None:
        order.append("preload")

    async def _connect(*_a: object, **_k: object) -> None:
        order.append("connect")
        raise ConnectionError("stop here")

    with (
        patch.object(entry, "_preload_embeddings", _preload),
        patch.object(entry, "_setup_usage_callback", MagicMock()),
        patch.object(entry, "init_sentry_for_worker", MagicMock(return_value=False)),
        patch.object(entry.aio_pika, "connect_robust", _connect),
        pytest.raises(ConnectionError),
    ):
        await entry.main()

    assert order == ["preload", "connect"]
