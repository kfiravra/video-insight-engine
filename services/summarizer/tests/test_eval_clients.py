"""Tests for the golden eval's HTTP clients (``scripts/_eval_api.py``, ``_eval_langfuse.py``).

vie-api and Langfuse are replaced by ``httpx.MockTransport`` handlers — no
network, no spend.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))

import _eval_api  # noqa: E402
from _eval_langfuse import RunItem, TraceTarget, collect_signals, publish_dataset_run  # noqa: E402

_API = "http://api.test"
_URL = "https://www.youtube.com/watch?v=abcdefghijk"
_DOC = {
    "status": "completed",
    "tabs": [{"id": "overview", "component": "overview", "props": {}}],
    "meta": {"primaryTag": "food"},
    "duration": 300,
    "youtubeId": "abcdefghijk",
}

Handler = Callable[[httpx.Request], httpx.Response]


class FakeApi:
    """vie-api double: POST → SSE terminal event → doc; optional 401s on the doc poll."""

    def __init__(
        self, doc_statuses: list[int] | None = None, doc: dict[str, Any] | None = None
    ) -> None:
        self.posts: list[dict[str, Any]] = []
        self.doc_statuses = list(doc_statuses or [])
        self.doc = doc or _DOC

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path == "/api/videos":
            self.posts.append(json.loads(request.content))
            return httpx.Response(201, json={"video": {"id": "uv1", "videoSummaryId": "vs1"}})
        if path == "/api/videos/vs1/stream":
            return httpx.Response(200, text="event: phase\ndata: {}\n\nevent: done\ndata: {}\n\n")
        if path == "/api/videos/uv1":
            status = self.doc_statuses.pop(0) if self.doc_statuses else 200
            return httpx.Response(status, json=self.doc)
        return httpx.Response(404)


@pytest.fixture
def mount(monkeypatch: pytest.MonkeyPatch) -> Callable[[Handler], None]:
    """Route ``_eval_api``'s HTTP client through a handler; stub re-auth and sleeps."""

    def _mount(handler: Handler) -> None:
        def client(token: str) -> httpx.AsyncClient:
            return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)

        async def fake_auth(api_url: str) -> str:
            return "fresh-token"

        async def no_sleep(_: float) -> None:
            return None

        monkeypatch.setattr(_eval_api, "_client", client)
        monkeypatch.setattr(_eval_api, "authenticate", fake_auth)
        monkeypatch.setattr(_eval_api.asyncio, "sleep", no_sleep)

    return _mount


# ─── vie-api ───────────────────────────────────────────────────────────
class TestRunPipeline:
    async def test_should_send_bypass_cache_when_requested(self, mount) -> None:
        api = FakeApi()
        mount(api)
        await _eval_api.run_pipeline(_API, _URL, "t", bypass_cache=True)
        assert api.posts == [{"url": _URL, "bypassCache": True}]

    async def test_should_return_tabs_duration_and_summary_id_when_completed(self, mount) -> None:
        mount(FakeApi())
        out = await _eval_api.run_pipeline(_API, _URL, "t")
        assert (out["videoSummaryId"], out["duration"], len(out["tabs"])) == ("vs1", 300, 1)

    async def test_should_raise_when_doc_reports_failure(self, mount) -> None:
        mount(FakeApi(doc={"status": "failed", "error": "boom"}))
        with pytest.raises(RuntimeError, match="boom"):
            await _eval_api.run_pipeline(_API, _URL, "t")

    async def test_should_repoll_without_resubmitting_when_token_expires_after_run(
        self, mount
    ) -> None:
        api = FakeApi(doc_statuses=[401])
        mount(api)
        out, token = await _eval_api.run_with_reauth(_API, _URL, "old", bypass_cache=True)
        assert (len(api.posts), token, out["videoSummaryId"]) == (1, "fresh-token", "vs1")


class TestAuthenticate:
    async def test_should_log_in_when_registration_is_closed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/auth/register":
                return httpx.Response(403, json={"error": "REGISTRATION_CLOSED"})
            return httpx.Response(200, json={"accessToken": "jwt"})

        real_client = httpx.AsyncClient
        monkeypatch.setattr(
            _eval_api.httpx,
            "AsyncClient",
            lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
        )
        monkeypatch.setenv("EVAL_USER_PASSWORD", "EvalRunner2026!")
        assert await _eval_api.authenticate(_API) == "jwt"

    def test_should_refuse_when_password_is_not_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("EVAL_USER_PASSWORD", raising=False)
        with pytest.raises(RuntimeError, match="EVAL_USER_PASSWORD"):
            _eval_api._resolve_eval_credentials()


# ─── Langfuse ──────────────────────────────────────────────────────────
_TRACE = {
    "id": "tr1",
    "scores": [{"name": "faithfulness", "value": 0.75, "timestamp": "2026-10-07T10:00:00Z"}],
    "observations": [{"name": "classifier", "output": '{"format": "tutorial"}'}],
}
_TARGET = TraceTarget("vs1", datetime(2026, 10, 7, tzinfo=UTC))


def _langfuse(handler: Handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url="http://lf.test", transport=httpx.MockTransport(handler))


async def _no_sleep(_: float) -> None:
    return None


class TestLangfuse:
    async def test_should_read_faithfulness_and_format_from_the_run_trace(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/public/traces":
                return httpx.Response(200, json={"data": [{"id": "tr1"}]})
            return httpx.Response(200, json=_TRACE)

        async with _langfuse(handler) as client:
            signals = await collect_signals(client, {"g1": _TARGET}, sleep=_no_sleep)
        assert (signals["g1"].faithfulness, signals["g1"].classifier_format) == (0.75, "tutorial")

    async def test_should_search_traces_by_pipeline_summary_id(self) -> None:
        names: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            names.append(request.url.params["name"])
            return httpx.Response(200, json={"data": []})

        async with _langfuse(handler) as client:
            await collect_signals(client, {"g1": _TARGET}, rounds=1, sleep=_no_sleep)
        assert names == ["pipeline:vs1"]

    async def test_should_omit_video_when_no_trace_matches(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": []})

        async with _langfuse(handler) as client:
            signals = await collect_signals(client, {"g1": _TARGET}, rounds=2, sleep=_no_sleep)
        assert signals == {}

    async def test_should_link_only_items_that_have_a_trace(self) -> None:
        posted: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            posted.append(request.url.path)
            return httpx.Response(200, json={})

        items = [
            RunItem("g1", "tr1", {}, {"quality": 0.9, "duplicateRate": None}),
            RunItem("g2", None, {}),
        ]
        async with _langfuse(handler) as client:
            linked = await publish_dataset_run(client, "golden-test", items)
        assert (linked, posted) == (1, ["/api/public/scores", "/api/public/dataset-run-items"])
