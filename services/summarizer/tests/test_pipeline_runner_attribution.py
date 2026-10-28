"""Run attribution in the pipeline entry (billing-roles P0.4 / B2).

``stream_summarization`` stamps the run's user and request id on the cost
ContextVars (``llm_user_id_var`` / ``llm_request_id_var``) before the cache
lookup. Precedence: the queue worker's structlog contextvars name the true
owner of THIS run and win; the cache row's ``userId`` / ``requestId`` (stamped
by the last ``POST /summarize``) are the fallback — the only owner an SSE
producer has. The one exception is the request id on an HTTP-bound context,
where the middleware's fresh UUID must not shadow the dispatching request's id.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import structlog
from llm_common.context import llm_request_id_var, llm_user_id_var

from src.routes import pipeline_runner

_CACHED = {
    "status": "completed",
    "youtubeId": "yt_attribution",
    "tabs": [{"id": "overview"}],
    "meta": {"contentTags": ["learning"]},
}


async def _run_cache_hit(entry: dict[str, Any], **bound: str) -> dict[str, str | None]:
    """Drive the Redis cache-hit path and snapshot the cost ContextVars it set."""
    captured: dict[str, str | None] = {}

    async def fake_get_response(_youtube_id: str) -> dict:
        return _CACHED

    async def capture_then_stream(_vsid: str, _cached: dict):
        captured["user_id"] = llm_user_id_var.get()
        captured["request_id"] = llm_request_id_var.get()
        yield "data: {}\n\n"

    structlog.contextvars.bind_contextvars(**bound)
    try:
        with (
            patch.object(pipeline_runner.response_cache, "get_response", new=fake_get_response),
            patch.object(pipeline_runner, "_stream_cached_structured", new=capture_then_stream),
            patch.object(pipeline_runner.settings, "REDIS_ENABLED", True),
        ):
            _ = [
                ev
                async for ev in pipeline_runner.stream_summarization(
                    "vsum_attr", entry, MagicMock(), MagicMock()
                )
            ]
    finally:
        structlog.contextvars.unbind_contextvars(*bound)
    return captured


def _entry(**row: str) -> dict[str, Any]:
    return {"youtubeId": "yt_attribution", "status": "processing", **row}


class TestUserAttribution:
    async def test_should_prefer_the_worker_user_over_the_row_user(self):
        captured = await _run_cache_hit(_entry(userId="row_user"), user_id="worker_user")

        assert captured["user_id"] == "worker_user"

    async def test_should_fall_back_to_the_row_user_when_none_is_bound(self):
        captured = await _run_cache_hit(_entry(userId="row_user"), request_id="req-sse-1")

        assert captured["user_id"] == "row_user"


class TestRequestIdAttribution:
    async def test_should_prefer_the_worker_request_id_over_the_row_request_id(self):
        captured = await _run_cache_hit(
            _entry(requestId="row-req-1"), request_id="worker-req-1", user_id="worker_user"
        )

        assert captured["request_id"] == "worker-req-1"

    async def test_should_fall_back_to_the_row_request_id_when_none_is_bound(self):
        captured = await _run_cache_hit(_entry(requestId="row-req-1"))

        assert captured["request_id"] == "row-req-1"

    async def test_should_prefer_the_row_request_id_on_an_http_bound_context(self):
        # The SSE GET's middleware binds a fresh UUID plus method/path.
        captured = await _run_cache_hit(
            _entry(requestId="row-req-1"),
            request_id="fresh-sse-uuid",
            method="GET",
            path="/summarize/stream/vsum_attr",
        )

        assert captured["request_id"] == "row-req-1"

    async def test_should_use_the_http_request_id_when_the_row_has_none(self):
        captured = await _run_cache_hit(
            _entry(), request_id="fresh-sse-uuid", method="GET", path="/summarize/stream/x"
        )

        assert captured["request_id"] == "fresh-sse-uuid"
