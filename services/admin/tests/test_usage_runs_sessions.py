"""D10: /usage/by-run keyed by run id, /usage/by-session, and a video's runs (P2.1)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from src.config import settings
from src.main import app
from src.routes import usage as usage_module
from src.routes.usage import (
    _attach_cache_fields,
    _build_run_summary,
    _build_session_summary,
    _calls_filter,
)

T0 = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def project(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "LANGFUSE_PROJECT_ID", "proj-1")
    monkeypatch.setattr(settings, "LANGFUSE_BASE_URL", "https://lf.example")


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.ADMIN_API_KEY}"}


def _run_group(**overrides: Any) -> dict[str, Any]:
    group: dict[str, Any] = {
        "_id": "run-1",
        "run_id": "run-1",
        "request_id": "req-1",
        "trace_id": "t-ledger",
        "attempt": 2,
        "pipeline_version": "v11",
        "bypass": True,
        "video_id": "FTha4zARGN4",
        "video_summary_id": "vs-1",
        "user_id": "u-1",
        "first_call": T0,
        "last_call": T0,
        "total_cost_usd": 0.21,
        "call_count": 14,
    }
    group.update(overrides)
    return group


# ─── Run rows ───


class TestRunKeyedSummary:
    def test_should_carry_the_run_and_ledger_ids_when_grouped_by_run_id(self) -> None:
        run = _build_run_summary(_run_group(), [], ordinal=1)

        assert (run.run_id, run.request_id) == ("run-1", "req-1")
        assert (run.attempt, run.pipeline_version, run.bypass) == (2, "v11", True)

    def test_should_read_the_request_id_from_the_key_when_a_legacy_group_has_no_run_id(
        self,
    ) -> None:
        group = {
            k: v for k, v in _run_group(_id="req-legacy", run_id=None).items() if k != "request_id"
        }

        run = _build_run_summary(group, [], ordinal=None)

        assert (run.run_id, run.request_id) == (None, "req-legacy")

    def test_should_take_the_attempt_fields_from_the_runs_entry_with_its_run_id(
        self, project: None
    ) -> None:
        run = _build_run_summary(
            _run_group(attempt=None, pipeline_version=None, bypass=None), [], None
        )
        rows = {
            "vs-1": {
                "runs": [
                    {"runId": "run-0", "requestId": "req-1", "traceId": "t-0", "status": "failed"},
                    {
                        "runId": "run-1",
                        "requestId": "req-1",
                        "traceId": "t-1",
                        "attempt": 2,
                        "pipelineVersion": "v11",
                        "bypass": False,
                        "path": "worker",
                        "status": "completed",
                        "retryOf": "run-0",
                    },
                ]
            }
        }

        _attach_cache_fields([run], rows)

        assert (run.attempt, run.pipeline_version, run.bypass) == (2, "v11", False)
        assert (run.path, run.status, run.retry_of) == ("worker", "completed", "run-0")
        assert run.langfuse_url == "https://lf.example/project/proj-1/traces/t-1"
        assert run.session_url == "https://lf.example/project/proj-1/sessions/pipeline-vs-1"

    def test_should_link_the_ledger_trace_id_when_the_runs_entry_is_gone(
        self, project: None
    ) -> None:
        run = _build_run_summary(_run_group(), [], None)

        _attach_cache_fields([run], {"vs-1": {"runs": []}}, {0: "t-ledger"})

        assert run.langfuse_url == "https://lf.example/project/proj-1/traces/t-ledger"
        assert run.status is None

    def test_should_link_the_ledger_trace_id_when_the_row_has_no_cache_doc(
        self, project: None
    ) -> None:
        run = _build_run_summary(_run_group(), [], None)

        _attach_cache_fields([run], {}, {0: "t-ledger"})

        assert run.langfuse_url == "https://lf.example/project/proj-1/traces/t-ledger"
        assert run.degraded is None


class TestCallsFilter:
    def test_should_match_the_run_id_when_the_group_has_one(self) -> None:
        assert _calls_filter(_run_group()) == {"service": "summarizer", "run_id": "run-1"}

    def test_should_match_legacy_rows_of_the_request_when_the_group_has_no_run_id(self) -> None:
        group = _run_group(_id="req-legacy", run_id=None)
        assert _calls_filter(group) == {
            "service": "summarizer",
            "run_id": None,
            "request_id": "req-legacy",
        }


# ─── Session rows ───


class TestSessionSummary:
    def _group(self, **overrides: Any) -> dict[str, Any]:
        group: dict[str, Any] = {
            "_id": "sess-1",
            "user_id": "u-1",
            "video_summary_id": "vs-1",
            "turns": 3,
            "tool_llm_calls": 1,
            "call_count": 4,
            "total_cost_usd": 0.0007,
            "first_call": T0,
            "last_call": T0,
        }
        group.update(overrides)
        return group

    def test_should_link_the_chat_session_when_the_project_is_set(self, project: None) -> None:
        session = _build_session_summary(self._group())

        assert (session.session_id, session.turns, session.tool_llm_calls) == ("sess-1", 3, 1)
        assert session.langfuse_url == "https://lf.example/project/proj-1/sessions/chat-sess-1"
        assert session.first_call == T0.isoformat()

    def test_should_have_no_link_for_the_unattributed_bucket(self, project: None) -> None:
        session = _build_session_summary(self._group(_id=None))

        assert (session.session_id, session.langfuse_url) == (None, None)


# ─── Endpoints ───


@pytest.fixture
def mock_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[MagicMock]:
    """Stub get_database: every aggregate returns no rows; pipelines are inspectable."""
    db = MagicMock()
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=[])
    db.llm_usage.aggregate.return_value = cursor
    db.llm_usage.find.return_value.sort.return_value.limit.return_value.to_list = AsyncMock(
        return_value=[]
    )
    db.videoSummaryCache.find_one = AsyncMock(return_value=None)
    monkeypatch.setattr(usage_module, "get_database", lambda: db)
    usage_module._cache.clear()
    yield db
    usage_module._cache.clear()


def _match_of(db: MagicMock, call: int = 0) -> dict[str, Any]:
    return db.llm_usage.aggregate.call_args_list[call].args[0][0]["$match"]


@pytest.mark.anyio
async def test_by_run_should_group_the_summarizer_rows_by_run_key(mock_db: MagicMock) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/usage/by-run", headers=_auth_headers())

    assert resp.status_code == 200
    pipeline = mock_db.llm_usage.aggregate.call_args.args[0]
    assert pipeline[0]["$match"]["service"] == "summarizer"
    assert pipeline[1]["$group"]["_id"] == {"$ifNull": ["$run_id", "$request_id"]}


@pytest.mark.anyio
async def test_by_run_should_narrow_to_one_video_when_a_youtube_id_is_given(
    mock_db: MagicMock,
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/usage/by-run?video_id=FTha4zARGN4", headers=_auth_headers())

    assert resp.status_code == 200
    assert _match_of(mock_db)["video_id"] == "FTha4zARGN4"


@pytest.mark.anyio
async def test_by_run_should_refuse_a_video_id_that_is_no_youtube_id(mock_db: MagicMock) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/usage/by-run?video_id=not-a-youtube-id", headers=_auth_headers())

    assert resp.status_code == 422


@pytest.mark.anyio
async def test_by_session_should_require_auth() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/usage/by-session")

    assert resp.status_code == 401


@pytest.mark.anyio
@pytest.mark.parametrize("query", ["days=0", "days=91", "limit=0", "limit=101", "offset=-1"])
async def test_by_session_should_validate_the_paging(query: str, mock_db: MagicMock) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/usage/by-session?{query}", headers=_auth_headers())

    assert resp.status_code == 422


@pytest.mark.anyio
async def test_by_session_should_group_the_assistant_rows_by_session_id(mock_db: MagicMock) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/usage/by-session", headers=_auth_headers())

    assert (resp.status_code, resp.json()) == (200, [])
    pipeline = mock_db.llm_usage.aggregate.call_args.args[0]
    assert pipeline[0]["$match"]["service"] == "assistant"
    assert pipeline[1]["$group"]["_id"] == {"$ifNull": ["$session_id", None]}


@pytest.mark.anyio
async def test_video_detail_should_return_runs_and_no_flat_calls_by_default(
    mock_db: MagicMock,
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/usage/video/FTha4zARGN4", headers=_auth_headers())

    body = resp.json()
    assert resp.status_code == 200
    assert body["runs"] == []
    assert "calls" not in body
    mock_db.llm_usage.find.assert_not_called()


@pytest.mark.anyio
async def test_video_detail_should_keep_the_flat_calls_behind_the_flag(mock_db: MagicMock) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/usage/video/FTha4zARGN4?flat=1", headers=_auth_headers())

    assert resp.json()["calls"] == []
    assert mock_db.llm_usage.find.call_args.args[0] == {"video_id": "FTha4zARGN4"}
