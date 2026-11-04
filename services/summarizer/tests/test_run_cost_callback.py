"""The run-cost hand-back (token ledger): a completed run reports
``pipeline.timing.costUsd`` to vie-api once, after the timing is persisted;
failed or purged runs report nothing, and nothing here ever raises."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.routes import pipeline_orchestration, run_timing
from src.services import status_callback
from src.services.status_callback import post_run_cost

# ─── post_run_cost ───


def _client_returning(*responses: object) -> MagicMock:
    """An httpx.AsyncClient class mock whose ``post`` yields `responses` in order (an exception raises)."""
    client = AsyncMock()
    client.post.side_effect = list(responses)
    client_class = MagicMock()
    client_class.return_value.__aenter__.return_value = client
    return client_class


def _status(code: int) -> SimpleNamespace:
    return SimpleNamespace(status_code=code)


@pytest.fixture(autouse=True)
def _api_settings():
    with patch.object(status_callback, "settings") as settings:
        settings.API_URL = "http://api:3000"
        settings.INTERNAL_SECRET = "test-secret"
        yield settings


@pytest.fixture(autouse=True)
def _no_backoff():
    with patch.object(status_callback.asyncio, "sleep", AsyncMock()) as sleep:
        yield sleep


class TestPostRunCost:
    async def test_should_post_the_cost_with_the_secret(self) -> None:
        client_class = _client_returning(_status(200))
        with patch.object(status_callback.httpx, "AsyncClient", client_class):
            ok = await post_run_cost("vsid", "run-1", 0.2526, 41)

        client = client_class.return_value.__aenter__.return_value
        url = client.post.call_args.args[0]
        assert ok is True
        assert url == "http://api:3000/internal/run-cost"
        assert client.post.call_args.kwargs["json"] == {
            "videoSummaryId": "vsid",
            "billingRunId": "run-1",
            "costUsd": 0.2526,
            "llmCalls": 41,
        }
        assert client.post.call_args.kwargs["headers"] == {"X-Internal-Secret": "test-secret"}

    async def test_should_omit_a_missing_billing_run_id(self) -> None:
        client_class = _client_returning(_status(200))
        with patch.object(status_callback.httpx, "AsyncClient", client_class):
            await post_run_cost("vsid", None, 0.1, 3)

        payload = client_class.return_value.__aenter__.return_value.post.call_args.kwargs["json"]
        assert "billingRunId" not in payload

    async def test_should_retry_a_5xx_then_succeed(self) -> None:
        client_class = _client_returning(_status(503), _status(200))
        with patch.object(status_callback.httpx, "AsyncClient", client_class):
            ok = await post_run_cost("vsid", "run-1", 0.1, 3)

        assert ok is True
        assert client_class.return_value.__aenter__.return_value.post.call_count == 2

    async def test_should_give_up_after_three_transport_errors(self) -> None:
        error = httpx.ConnectError("down")
        client_class = _client_returning(error, error, error)
        with patch.object(status_callback.httpx, "AsyncClient", client_class):
            ok = await post_run_cost("vsid", "run-1", 0.1, 3)

        assert ok is False
        assert client_class.return_value.__aenter__.return_value.post.call_count == 3

    async def test_should_not_retry_a_4xx(self) -> None:
        client_class = _client_returning(_status(400), _status(200))
        with patch.object(status_callback.httpx, "AsyncClient", client_class):
            ok = await post_run_cost("vsid", "run-1", 0.1, 3)

        assert ok is False
        assert client_class.return_value.__aenter__.return_value.post.call_count == 1


# ─── report_run_cost ───

_DOC = {"costUsd": 0.31, "counts": {"llmCalls": 12}}


def _ctx(**overrides: object) -> SimpleNamespace:
    fields: dict[str, object] = {"row_deleted": False, "entry": {"billingRunId": "run-1"}}
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _repository(status: str | None) -> MagicMock:
    repository = MagicMock()
    repository.get_video_summary.return_value = None if status is None else {"status": status}
    return repository


class TestReportRunCost:
    async def test_should_report_a_completed_run_with_its_dispatch_run_id(self) -> None:
        with patch.object(run_timing, "post_run_cost", AsyncMock(return_value=True)) as post:
            await run_timing.report_run_cost(_ctx(), _repository("completed"), "vsid", _DOC)  # type: ignore[arg-type]

        post.assert_awaited_once_with("vsid", "run-1", 0.31, 12)

    @pytest.mark.parametrize("status", ["failed", "processing", None])
    async def test_should_not_charge_a_run_whose_row_did_not_complete(
        self, status: str | None
    ) -> None:
        with patch.object(run_timing, "post_run_cost", AsyncMock()) as post:
            await run_timing.report_run_cost(_ctx(), _repository(status), "vsid", _DOC)  # type: ignore[arg-type]

        post.assert_not_awaited()

    async def test_should_skip_a_purged_row(self) -> None:
        repository = _repository("completed")
        with patch.object(run_timing, "post_run_cost", AsyncMock()) as post:
            await run_timing.report_run_cost(_ctx(row_deleted=True), repository, "vsid", _DOC)  # type: ignore[arg-type]

        post.assert_not_awaited()
        repository.get_video_summary.assert_not_called()

    async def test_should_send_no_run_id_for_a_row_dispatched_without_one(self) -> None:
        with patch.object(run_timing, "post_run_cost", AsyncMock(return_value=True)) as post:
            await run_timing.report_run_cost(_ctx(entry={}), _repository("completed"), "vsid", _DOC)  # type: ignore[arg-type]

        assert post.await_args is not None
        assert post.await_args.args[1] is None

    async def test_should_never_raise(self) -> None:
        repository = MagicMock()
        repository.get_video_summary.side_effect = RuntimeError("mongo down")

        await run_timing.report_run_cost(_ctx(), repository, "vsid", _DOC)  # type: ignore[arg-type]


# ─── run_pipeline_phases wiring ───


class TestRunPipelinePhasesReportsCost:
    @staticmethod
    def _phases(fail: bool):
        async def _phases(*_a: object, **_k: object):
            yield "data: x\n\n"
            if fail:
                raise RuntimeError("assembly exploded")

        return _phases

    async def _drive(self, fail: bool, doc: dict | None) -> tuple[AsyncMock, AsyncMock]:
        persist = AsyncMock(return_value=doc)
        report = AsyncMock()
        with (
            patch.object(pipeline_orchestration, "_run_phases_in_order", self._phases(fail)),
            patch.object(pipeline_orchestration, "_end_run_work", AsyncMock()),
            patch.object(pipeline_orchestration, "persist_run_timing", persist),
            patch.object(pipeline_orchestration, "report_run_cost", report),
        ):
            timer = MagicMock()
            try:
                async for _ in pipeline_orchestration.run_pipeline_phases(
                    SimpleNamespace(),
                    MagicMock(),
                    "vsid",
                    timer,  # type: ignore[arg-type]
                ):
                    pass
            except RuntimeError:
                pass
        return persist, report

    async def test_should_report_once_after_the_timing_persisted(self) -> None:
        persist, report = await self._drive(fail=False, doc=_DOC)

        persist.assert_awaited_once()
        report.assert_awaited_once()
        assert report.await_args is not None
        assert report.await_args.args[2:] == ("vsid", _DOC)

    async def test_should_not_report_a_run_that_raised(self) -> None:
        persist, report = await self._drive(fail=True, doc=_DOC)

        persist.assert_awaited_once()
        report.assert_not_awaited()

    async def test_should_not_report_when_no_timing_was_written(self) -> None:
        _persist, report = await self._drive(fail=False, doc=None)

        report.assert_not_awaited()
