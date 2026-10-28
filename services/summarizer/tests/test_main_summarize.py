"""Tests for ``POST /summarize`` (src.main).

The route stores who dispatched the run on the ``videoSummaryCache`` row so the
SSE producer — which carries no user — can attribute ``llm_usage`` to them. The
repository is real, over a mocked collection, so the asserts read the exact
update the row would receive.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock

import pytest
from bson import ObjectId
from httpx import ASGITransport, AsyncClient

from src.dependencies import get_video_repository
from src.main import app
from src.repositories.mongodb_repository import MongoDBVideoRepository

_VIDEO_SUMMARY_ID = "0" * 24
_REQUEST_ID = "req-abc12345"
_BODY = {
    "videoSummaryId": _VIDEO_SUMMARY_ID,
    "youtubeId": "dQw4w9WgXcQ",
    "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
}


@pytest.fixture
def collection() -> Iterator[MagicMock]:
    """Mocked ``videoSummaryCache`` collection behind the real repository."""
    mocked = MagicMock()
    database = MagicMock()
    database.videoSummaryCache = mocked
    app.dependency_overrides[get_video_repository] = lambda: MongoDBVideoRepository(database)
    yield mocked
    app.dependency_overrides.clear()


async def _post(body: dict, headers: dict[str, str] | None = None):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post("/summarize", json=body, headers=headers or {})


def _update_for(collection: MagicMock, field: str) -> dict:
    """The single update that touches ``field`` ($set or $unset)."""
    matching = [
        call.args[1]
        for call in collection.update_one.call_args_list
        if field in call.args[1].get("$set", {}) or field in call.args[1].get("$unset", {})
    ]
    assert len(matching) == 1
    return matching[0]


class TestSummarizeDispatchContext:
    async def test_should_accept_the_request_when_dispatch_context_is_stored(self, collection):
        response = await _post({**_BODY, "userId": "user_42"}, {"X-Request-ID": _REQUEST_ID})

        assert response.status_code == 202
        assert response.json() == {"status": "accepted", "videoSummaryId": _VIDEO_SUMMARY_ID}

    async def test_should_target_the_row_by_object_id(self, collection):
        await _post({**_BODY, "userId": "user_42"}, {"X-Request-ID": _REQUEST_ID})

        assert collection.update_one.call_args.args[0] == {"_id": ObjectId(_VIDEO_SUMMARY_ID)}

    async def test_should_persist_user_and_request_id_when_both_are_sent(self, collection):
        await _post({**_BODY, "userId": "user_42"}, {"X-Request-ID": _REQUEST_ID})

        update = _update_for(collection, "userId")
        assert update == {"$set": {"userId": "user_42", "requestId": _REQUEST_ID}}

    async def test_should_unset_user_id_when_the_request_carries_none(self, collection):
        await _post(_BODY, {"X-Request-ID": _REQUEST_ID})

        update = _update_for(collection, "userId")
        assert update == {"$set": {"requestId": _REQUEST_ID}, "$unset": {"userId": ""}}

    async def test_should_keep_a_valid_request_id_over_a_generated_one(self, collection):
        await _post({**_BODY, "userId": "user_42"}, {"X-Request-ID": _REQUEST_ID})

        assert _update_for(collection, "requestId")["$set"]["requestId"] == _REQUEST_ID

    async def test_should_bind_a_fresh_request_id_when_the_header_is_rejected(self, collection):
        await _post({**_BODY, "userId": "user_42"}, {"X-Request-ID": "bad id\nforged"})

        stored = _update_for(collection, "requestId")["$set"]["requestId"]
        assert stored != "bad id\nforged"

    async def test_should_still_store_provider_config_when_providers_are_sent(self, collection):
        body = {**_BODY, "userId": "user_42", "providers": {"default": "openai"}}

        await _post(body, {"X-Request-ID": _REQUEST_ID})

        assert _update_for(collection, "providerConfig")["$set"]["providerConfig"]["default"] == (
            "openai"
        )

    async def test_should_reject_a_malformed_video_summary_id(self, collection):
        response = await _post({**_BODY, "videoSummaryId": "not-an-object-id"})

        assert response.status_code == 400
        collection.update_one.assert_not_called()
