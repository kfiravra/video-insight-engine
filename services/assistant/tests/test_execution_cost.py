"""Per-execution LLM cost for the token ledger: the meter, what feeds it, the
endpoints that start and report it, and the usage report to vie-api."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from src.services import execution_cost as ec
from src.services import llm_provider as lp
from src.services.api_client import ApiClient
from src.services.llm_provider import LLMProvider

SECRET_HEADERS = {"X-Internal-Secret": "dev-internal-secret-change-me"}


async def _drain_reports() -> None:
    """Let the fire-and-forget usage reports finish."""
    for _ in range(5):
        await asyncio.sleep(0)
    if ec._REPORT_TASKS:
        await asyncio.gather(*list(ec._REPORT_TASKS))


# ─── The meter ───


class TestMeter:
    def test_should_not_meter_without_an_execution_id(self) -> None:
        assert (
            ec.start_execution(execution_id=None, kind="chat", user_id="u1", request_id="r1")
            is None
        )
        ec.record_llm_cost(0.5)  # no meter: a no-op

    def test_should_not_meter_without_a_user(self) -> None:
        assert (
            ec.start_execution(execution_id="e1", kind="chat", user_id=None, request_id="r1")
            is None
        )

    def test_should_add_every_call_and_count_free_ones(self) -> None:
        meter = ec.start_execution(execution_id="e1", kind="chat", user_id="u1", request_id="r1")
        ec.record_llm_cost(0.01)
        ec.record_llm_cost(0.0)
        ec.record_llm_cost(0.02)

        assert meter is not None
        assert meter.cost_usd == pytest.approx(0.03)
        assert meter.calls == 3

    def test_should_default_the_request_id_to_the_execution_id(self) -> None:
        meter = ec.start_execution(execution_id="e1", kind="action", user_id="u1", request_id=None)

        assert meter is not None and meter.request_id == "e1"

    async def test_concurrent_requests_should_not_share_a_meter(self) -> None:
        async def _request(execution_id: str, cost: float) -> ec.ExecutionCost | None:
            meter = ec.start_execution(
                execution_id=execution_id, kind="chat", user_id="u1", request_id=execution_id
            )
            await asyncio.sleep(0)
            ec.record_llm_cost(cost)
            await asyncio.sleep(0)
            ec.record_llm_cost(cost)
            return meter

        first, second = await asyncio.gather(_request("e1", 0.01), _request("e2", 0.1))

        assert first is not None and second is not None
        assert first.cost_usd == pytest.approx(0.02)
        assert second.cost_usd == pytest.approx(0.2)


class TestMeteredStream:
    @staticmethod
    async def _events(*items: str):
        for item in items:
            yield item

    async def test_should_report_once_after_the_stream_ends(self) -> None:
        meter = ec.ExecutionCost(
            execution_id="e1", kind="chat", user_id="u1", request_id="r1", cost_usd=0.04, calls=2
        )
        reporter = MagicMock()
        reporter.report_execution_usage = AsyncMock(return_value=True)

        out = [event async for event in ec.metered_stream(self._events("a", "b"), meter, reporter)]
        await _drain_reports()

        assert out == ["a", "b"]
        reporter.report_execution_usage.assert_awaited_once_with(
            "u1", execution_id="e1", request_id="r1", kind="chat", cost_usd=0.04, calls=2
        )

    async def test_should_report_when_the_reader_stops_early(self) -> None:
        meter = ec.ExecutionCost(execution_id="e1", kind="chat", user_id="u1", request_id="r1")
        reporter = MagicMock()
        reporter.report_execution_usage = AsyncMock(return_value=True)

        stream = ec.metered_stream(self._events("a", "b", "c"), meter, reporter)
        assert await stream.__anext__() == "a"
        await stream.aclose()
        await _drain_reports()

        reporter.report_execution_usage.assert_awaited_once()

    async def test_should_not_report_without_a_meter(self) -> None:
        reporter = MagicMock()
        reporter.report_execution_usage = AsyncMock()

        _ = [event async for event in ec.metered_stream(self._events("a"), None, reporter)]
        await _drain_reports()

        reporter.report_execution_usage.assert_not_awaited()

    async def test_a_failing_report_should_never_raise(self) -> None:
        meter = ec.ExecutionCost(execution_id="e1", kind="chat", user_id="u1", request_id="r1")
        reporter = MagicMock()
        reporter.report_execution_usage = AsyncMock(side_effect=RuntimeError("api down"))

        ec.report_in_background(meter, reporter)
        await _drain_reports()


# ─── What feeds the meter: every final LLM response ───


def _completion(content: str = "answer") -> SimpleNamespace:
    message = SimpleNamespace(content=content, tool_calls=None)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason="stop")],
        usage=SimpleNamespace(prompt_tokens=20, completion_tokens=4),
    )


def _stream(chunks: list[SimpleNamespace]):
    class _Stream:
        def __init__(self) -> None:
            self._items = list(chunks)

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self._items:
                raise StopAsyncIteration
            return self._items.pop(0)

    return _Stream()


def _delta(content: str, finish_reason: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(delta=SimpleNamespace(content=content), finish_reason=finish_reason)
        ],
        usage=None,
    )


def _usage_chunk() -> SimpleNamespace:
    return SimpleNamespace(choices=[], usage=SimpleNamespace(prompt_tokens=12, completion_tokens=3))


class TestProviderFeedsTheMeter:
    async def test_completion_should_add_its_cost(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(lp, "acompletion", AsyncMock(return_value=_completion()))
        monkeypatch.setattr(lp, "completion_cost", lambda **_kw: 0.003)
        meter = ec.start_execution(execution_id="e1", kind="chat", user_id="u1", request_id="r1")

        await LLMProvider(model="anthropic/claude-sonnet-4-6").complete_with_messages(
            [{"role": "user", "content": "hi"}]
        )

        assert meter is not None and meter.cost_usd == pytest.approx(0.003) and meter.calls == 1

    async def test_tool_completion_should_add_its_cost(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(lp, "acompletion", AsyncMock(return_value=_completion()))
        monkeypatch.setattr(lp, "completion_cost", lambda **_kw: 0.002)
        meter = ec.start_execution(execution_id="e1", kind="chat", user_id="u1", request_id="r1")
        provider = LLMProvider(model="anthropic/claude-sonnet-4-6")

        await provider.complete_with_tools([{"role": "user", "content": "hi"}], tools=[])
        await provider.complete_with_tools([{"role": "user", "content": "again"}], tools=[])

        assert meter is not None and meter.cost_usd == pytest.approx(0.004) and meter.calls == 2

    async def test_stream_should_ask_for_usage_and_add_the_rebuilt_cost(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict = {}

        async def _fake_acompletion(**kwargs):
            captured.update(kwargs)
            return _stream([_delta("Hel"), _delta("lo", "stop"), _usage_chunk()])

        rebuilt = object()
        monkeypatch.setattr(lp, "acompletion", _fake_acompletion)
        monkeypatch.setattr(lp, "stream_chunk_builder", lambda chunks, messages: rebuilt)
        monkeypatch.setattr(
            lp,
            "completion_cost",
            lambda **kw: 0.005 if kw.get("completion_response") is rebuilt else 0.0,
        )
        meter = ec.start_execution(execution_id="e1", kind="chat", user_id="u1", request_id="r1")

        tokens = [
            t
            async for t in LLMProvider(model="anthropic/claude-sonnet-4-6").stream_with_messages(
                [{"role": "user", "content": "hi"}]
            )
        ]

        assert tokens == ["Hel", "lo"]
        assert captured["stream_options"] == {"include_usage": True}
        assert meter is not None and meter.cost_usd == pytest.approx(0.005)

    async def test_stream_closed_early_should_still_add_what_it_cost(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _fake_acompletion(**_kwargs):
            return _stream([_delta("a"), _delta("b"), _delta("c", "stop"), _usage_chunk()])

        monkeypatch.setattr(lp, "acompletion", _fake_acompletion)
        monkeypatch.setattr(lp, "stream_chunk_builder", lambda chunks, messages: None)
        monkeypatch.setattr(lp, "completion_cost", lambda **_kw: 0.001)
        meter = ec.start_execution(execution_id="e1", kind="chat", user_id="u1", request_id="r1")

        stream = LLMProvider(model="anthropic/claude-sonnet-4-6").stream_with_messages(
            [{"role": "user", "content": "hi"}]
        )
        assert await stream.__anext__() == "a"
        await stream.aclose()

        assert meter is not None and meter.calls == 1


# ─── Endpoints start a meter and report it ───


def _client_with(service: MagicMock, api_client: MagicMock):
    from httpx import ASGITransport, AsyncClient

    from src.server import app

    app.state.assistant_service = service
    app.state.api_client = api_client
    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers=SECRET_HEADERS
    )


def _api_client() -> MagicMock:
    client = MagicMock()
    client.report_execution_usage = AsyncMock(return_value=True)
    return client


def _chat_service() -> MagicMock:
    async def _chat(**_kwargs):
        ec.record_llm_cost(0.01)
        ec.record_llm_cost(0.02)
        yield 'data: {"type": "done", "content": ""}\n\n'

    service = MagicMock()
    service.chat = _chat
    service.library_chat = _chat
    service.dispatch_action = AsyncMock(
        side_effect=lambda **_kw: (ec.record_llm_cost(0.007), {"ok": True})[1]
    )
    return service


@pytest.fixture(autouse=True)
def _reset_app_state():
    yield
    from src.server import app

    app.state.assistant_service = None
    app.state.api_client = None


class TestEndpointsReportUsage:
    async def test_chat_should_report_its_cost_under_the_execution_id(self) -> None:
        api_client = _api_client()
        headers = {"X-User-Id": "u1", "X-Request-ID": "r1", "X-Execution-Id": "e1"}
        async with _client_with(_chat_service(), api_client) as client:
            response = await client.post(
                "/chat", json={"video_id": "v1", "message": "hi"}, headers=headers
            )
            assert response.status_code == 200
        await _drain_reports()

        api_client.report_execution_usage.assert_awaited_once_with(
            "u1", execution_id="e1", request_id="r1", kind="chat", cost_usd=0.03, calls=2
        )

    async def test_library_chat_should_report_with_its_kind(self) -> None:
        api_client = _api_client()
        headers = {"X-User-Id": "u1", "X-Request-ID": "r1", "X-Execution-Id": "e2"}
        body = {"video_ids": ["v1"], "message": "hi", "library": [{"video_id": "v1", "title": "t"}]}
        async with _client_with(_chat_service(), api_client) as client:
            await client.post("/library/chat", json=body, headers=headers)
        await _drain_reports()

        assert api_client.report_execution_usage.await_args.kwargs["kind"] == "library_chat"

    async def test_action_should_report_its_cost(self) -> None:
        api_client = _api_client()
        headers = {"X-User-Id": "u1", "X-Request-ID": "r1", "X-Execution-Id": "e3"}
        async with _client_with(_chat_service(), api_client) as client:
            response = await client.post(
                "/action",
                json={"video_id": "v1", "action": "quiz_me", "params": {}},
                headers=headers,
            )
            assert response.status_code == 200
        await _drain_reports()

        kwargs = api_client.report_execution_usage.await_args.kwargs
        assert (kwargs["kind"], kwargs["execution_id"], kwargs["cost_usd"]) == (
            "action",
            "e3",
            0.007,
        )

    async def test_no_execution_id_should_report_nothing(self) -> None:
        api_client = _api_client()
        async with _client_with(_chat_service(), api_client) as client:
            await client.post(
                "/chat", json={"video_id": "v1", "message": "hi"}, headers={"X-User-Id": "u1"}
            )
        await _drain_reports()

        api_client.report_execution_usage.assert_not_awaited()


# ─── The usage report itself ───


def _transport(*responses: int | Exception) -> httpx.MockTransport:
    queue = list(responses)
    seen: list[httpx.Request] = []

    def _handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return httpx.Response(item, json={"received": True})

    transport = httpx.MockTransport(_handle)
    transport.seen = seen  # type: ignore[attr-defined]
    return transport


def _api_client_over(transport: httpx.MockTransport) -> ApiClient:
    client = ApiClient("http://api:3000", "secret")
    client._client = httpx.AsyncClient(base_url="http://api:3000", transport=transport)
    return client


@pytest.fixture
def _no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.services import api_client as module

    monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())


@pytest.mark.usefixtures("_no_backoff")
class TestReportExecutionUsage:
    async def _report(self, client: ApiClient) -> bool:
        return await client.report_execution_usage(
            "u1", execution_id="e1", request_id="r1", kind="chat", cost_usd=0.03, calls=2
        )

    async def test_should_post_the_cost_as_the_user(self) -> None:
        transport = _transport(200)
        ok = await self._report(_api_client_over(transport))

        request = transport.seen[0]  # type: ignore[attr-defined]
        assert ok is True
        assert request.url.path == "/internal/assistant/usage"
        assert request.headers["X-User-Id"] == "u1"
        assert request.headers["X-Internal-Secret"] == "secret"
        assert json.loads(request.read()) == {
            "executionId": "e1",
            "requestId": "r1",
            "kind": "chat",
            "costUsd": 0.03,
            "calls": 2,
        }

    async def test_should_retry_twice_on_5xx_and_transport_errors(self) -> None:
        transport = _transport(503, httpx.ConnectError("down"), 200)
        ok = await self._report(_api_client_over(transport))

        assert ok is True
        assert len(transport.seen) == 3  # type: ignore[attr-defined]

    async def test_should_give_up_after_three_tries(self) -> None:
        transport = _transport(500, 502, 503)

        assert await self._report(_api_client_over(transport)) is False

    async def test_should_not_retry_a_refusal(self) -> None:
        transport = _transport(400, 200)

        assert await self._report(_api_client_over(transport)) is False
        assert len(transport.seen) == 1  # type: ignore[attr-defined]
