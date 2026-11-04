"""Tests for GET /usage/by-run endpoint — auth, validation, and payload shape."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from src.config import settings
from src.main import app
from src.routes.usage_runs import (
    _attach_cache_fields,
    _build_run_call,
    _build_run_summary,
    _cache_rows_by_summary_id,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.ADMIN_API_KEY}"}


# ─── Auth + validation ───


@pytest.mark.anyio
async def test_by_run_requires_auth() -> None:
    """by-run endpoint must reject unauthenticated requests."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/usage/by-run")
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_by_run_rejects_wrong_key() -> None:
    """by-run endpoint must reject an incorrect API key."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/usage/by-run", headers={"Authorization": "Bearer wrong-key"})
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_by_run_validates_days_min() -> None:
    """days=0 should return 422 (below minimum)."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/usage/by-run?days=0", headers=_auth_headers())
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_by_run_validates_days_max() -> None:
    """days=91 should return 422 (above maximum of 90)."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/usage/by-run?days=91", headers=_auth_headers())
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_by_run_validates_limit_min() -> None:
    """limit=0 should return 422."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/usage/by-run?limit=0", headers=_auth_headers())
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_by_run_validates_limit_max() -> None:
    """limit=101 should return 422 (above maximum of 100)."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/usage/by-run?limit=101", headers=_auth_headers())
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_by_run_validates_offset_non_negative() -> None:
    """offset=-1 should return 422."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/usage/by-run?offset=-1", headers=_auth_headers())
    assert resp.status_code == 422


# ─── Unit tests for response model helpers ───


class TestBuildRunCall:
    """_build_run_call maps raw llm_usage documents correctly."""

    def test_maps_all_fields(self) -> None:
        """Standard document maps to RunCallSummary with correct types."""
        from datetime import UTC, datetime

        from bson import ObjectId

        raw = {
            "_id": ObjectId("aaaaaaaaaaaaaaaaaaaaaaaa"),
            "feature": "summarizer:plan",
            "model": "claude-sonnet-4-6",
            "cost_usd": 0.042,
            "tokens_in": 1000,
            "tokens_out": 500,
            "duration_ms": 1234.5,
            "success": True,
            "timestamp": datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC),
        }
        call = _build_run_call(raw)
        assert call.id == "aaaaaaaaaaaaaaaaaaaaaaaa"
        assert call.feature == "summarizer:plan"
        assert call.model == "claude-sonnet-4-6"
        assert call.cost_usd == pytest.approx(0.042)
        assert call.tokens_in == 1000
        assert call.tokens_out == 500
        assert call.success is True
        assert "2026-06-01" in call.timestamp

    def test_handles_missing_optional_fields(self) -> None:
        """Document with minimal fields should not raise."""
        from datetime import UTC, datetime

        from bson import ObjectId

        raw = {
            "_id": ObjectId("bbbbbbbbbbbbbbbbbbbbbbbb"),
            "timestamp": datetime(2026, 1, 1, tzinfo=UTC),
        }
        call = _build_run_call(raw)
        assert call.feature is None
        assert call.model is None
        assert call.cost_usd == pytest.approx(0.0)
        assert call.tokens_in is None

    def test_null_cost_defaults_to_zero(self) -> None:
        """cost_usd=None should be coerced to 0.0, not raise."""
        from datetime import UTC, datetime

        from bson import ObjectId

        raw = {
            "_id": ObjectId("cccccccccccccccccccccccc"),
            "cost_usd": None,
            "timestamp": datetime(2026, 1, 1, tzinfo=UTC),
        }
        call = _build_run_call(raw)
        assert call.cost_usd == 0.0

    def test_maps_audio_unit_fields(self) -> None:
        """Transcription rows surface unit + audio_seconds for the UI."""
        from datetime import UTC, datetime

        from bson import ObjectId

        raw = {
            "_id": ObjectId("dddddddddddddddddddddddd"),
            "feature": "summarize:transcript:whisper",
            "model": "whisper-1",
            "cost_usd": 0.36,
            "unit": "audio_seconds",
            "audio_seconds": 3600.0,
            "timestamp": datetime(2026, 6, 1, tzinfo=UTC),
        }
        call = _build_run_call(raw)
        assert call.unit == "audio_seconds"
        assert call.audio_seconds == pytest.approx(3600.0)

    def test_token_rows_have_no_audio_unit(self) -> None:
        """A normal token row leaves unit/audio_seconds unset (None)."""
        from datetime import UTC, datetime

        from bson import ObjectId

        raw = {
            "_id": ObjectId("eeeeeeeeeeeeeeeeeeeeeeee"),
            "model": "claude-sonnet-4-6",
            "timestamp": datetime(2026, 1, 1, tzinfo=UTC),
        }
        call = _build_run_call(raw)
        assert call.unit is None
        assert call.audio_seconds is None


class TestBuildRunSummary:
    """_build_run_summary assembles RunSummary from aggregation results."""

    def test_builds_correct_run_summary(self) -> None:
        """Basic run group with no calls produces valid RunSummary."""
        from datetime import UTC, datetime

        group = {
            "_id": "req-abc-123",
            "request_id": "req-abc-123",
            "video_id": "dQw4w9WgXcQ",
            "video_summary_id": "664f000000000000000000aa",
            "user_id": "664f000000000000000000bb",
            "first_call": datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC),
            "last_call": datetime(2026, 6, 1, 10, 5, 0, tzinfo=UTC),
            "total_cost_usd": 0.15,
            "call_count": 7,
        }
        summary = _build_run_summary(group, [], ordinal=2)
        assert summary.request_id == "req-abc-123"
        assert summary.video_id == "dQw4w9WgXcQ"
        assert summary.user_id == "664f000000000000000000bb"
        assert summary.total_cost_usd == pytest.approx(0.15)
        assert summary.call_count == 7
        assert summary.regen_ordinal == 2
        assert summary.calls == []

    def test_none_request_id_preserved(self) -> None:
        """Unattributed (legacy) runs have request_id=None in the response."""
        from datetime import UTC, datetime

        group = {
            "_id": None,
            "request_id": None,
            "video_id": "some-video",
            "video_summary_id": None,
            "user_id": None,
            "first_call": datetime(2026, 1, 1, tzinfo=UTC),
            "last_call": datetime(2026, 1, 1, tzinfo=UTC),
            "total_cost_usd": 0.0,
            "call_count": 1,
        }
        summary = _build_run_summary(group, [], ordinal=None)
        assert summary.request_id is None
        assert summary.regen_ordinal is None

    def test_calls_are_embedded(self) -> None:
        """Child calls are embedded in the RunSummary."""
        from datetime import UTC, datetime

        from bson import ObjectId

        group = {
            "_id": "req-xyz",
            "request_id": "req-xyz",
            "video_id": "vid1",
            "video_summary_id": None,
            "user_id": None,
            "first_call": datetime(2026, 6, 1, tzinfo=UTC),
            "last_call": datetime(2026, 6, 1, tzinfo=UTC),
            "total_cost_usd": 0.02,
            "call_count": 1,
        }
        raw_call = {
            "_id": ObjectId("dddddddddddddddddddddddd"),
            "feature": "summarizer:extraction",
            "model": "claude-haiku-4-5",
            "cost_usd": 0.02,
            "tokens_in": 800,
            "tokens_out": 200,
            "duration_ms": 800.0,
            "success": True,
            "timestamp": datetime(2026, 6, 1, 9, 0, 0, tzinfo=UTC),
        }
        summary = _build_run_summary(group, [raw_call], ordinal=1)
        assert len(summary.calls) == 1
        assert summary.calls[0].feature == "summarizer:extraction"

    def test_degraded_defaults_to_none(self) -> None:
        """Runs start with degraded=None until the doc lookup fills it in."""
        from datetime import UTC, datetime

        group = {
            "_id": "req-abc",
            "request_id": "req-abc",
            "video_id": "vid1",
            "video_summary_id": "664f000000000000000000aa",
            "user_id": None,
            "first_call": datetime(2026, 6, 1, tzinfo=UTC),
            "last_call": datetime(2026, 6, 1, tzinfo=UTC),
            "total_cost_usd": 0.0,
            "call_count": 1,
        }
        summary = _build_run_summary(group, [], ordinal=None)
        assert summary.degraded is None


# ─── Degraded-run badge lookup ───


class _FakeCursor:
    def __init__(self, docs: list[dict]) -> None:
        self._docs = docs

    async def to_list(self, _limit: int) -> list[dict]:
        return self._docs


class _FakeDb:
    """Motor stand-in exposing videoSummaryCache.find(filter, projection)."""

    def __init__(self, docs: list[dict]) -> None:
        self._docs = docs
        self.last_filter: dict | None = None

    @property
    def videoSummaryCache(self):  # noqa: N802 — mirrors the Mongo collection name
        return self

    def find(self, filter_: dict, projection: dict) -> _FakeCursor:
        self.last_filter = filter_
        wanted = set(filter_["_id"]["$in"])
        return _FakeCursor([d for d in self._docs if d["_id"] in wanted])


class TestDegradedBySummaryId:
    """_cache_rows_by_summary_id maps videoSummaryCache docs (badge flags, runs[] ids)."""

    @pytest.mark.anyio
    async def test_maps_degraded_and_clean_docs(self) -> None:
        from bson import ObjectId

        degraded_id = "664f000000000000000000aa"
        clean_id = "664f000000000000000000bb"
        db = _FakeDb(
            [
                {"_id": ObjectId(degraded_id), "degraded": True},
                {"_id": ObjectId(clean_id)},  # pre-feature/clean doc: no field
            ]
        )
        result = await _cache_rows_by_summary_id(db, {degraded_id, clean_id})
        assert result[degraded_id]["degraded"] is True
        assert result[clean_id].get("degraded") is None

    @pytest.mark.anyio
    async def test_returns_the_reasons_with_the_flag(self) -> None:
        from bson import ObjectId

        degraded_id = "664f000000000000000000aa"
        db = _FakeDb(
            [
                {
                    "_id": ObjectId(degraded_id),
                    "degraded": True,
                    "degradedReasons": ["media_download_failed"],
                }
            ]
        )
        result = await _cache_rows_by_summary_id(db, {degraded_id})
        assert result[degraded_id]["degradedReasons"] == ["media_download_failed"]

    @pytest.mark.anyio
    async def test_invalid_object_ids_are_skipped(self) -> None:
        db = _FakeDb([])
        result = await _cache_rows_by_summary_id(db, {"not-an-oid", ""})
        assert result == {}
        # No query was issued — nothing valid to look up.
        assert db.last_filter is None

    @pytest.mark.anyio
    async def test_missing_docs_are_absent_from_map(self) -> None:
        missing_id = "664f0000000000000000cc00"
        db = _FakeDb([])
        result = await _cache_rows_by_summary_id(db, {missing_id})
        assert missing_id not in result


class TestAttachCacheFields:
    """The by-run page's badge and Langfuse link come from the run's cache doc (P-6)."""

    def _run(self, request_id: str | None = "req-1", vsid: str | None = "vs-1"):
        group = {
            "_id": request_id,
            "request_id": request_id,
            "video_id": "yt",
            "video_summary_id": vsid,
            "first_call": None,
            "last_call": None,
            "total_cost_usd": 0.0,
            "call_count": 0,
        }
        return _build_run_summary(group, [], None)

    def test_should_link_the_newest_attempt_of_the_request_when_the_project_is_set(
        self, monkeypatch
    ) -> None:
        monkeypatch.setattr(settings, "LANGFUSE_PROJECT_ID", "proj-1")
        monkeypatch.setattr(settings, "LANGFUSE_BASE_URL", "https://lf.example")
        run = self._run()
        rows = {
            "vs-1": {
                "degraded": True,
                "runs": [
                    {"requestId": "req-1", "traceId": "t-old"},
                    {"requestId": "req-1", "traceId": "t-new"},
                ],
            }
        }

        _attach_cache_fields([run], rows)

        assert run.langfuse_url == "https://lf.example/project/proj-1/traces/t-new"
        assert run.degraded is True

    def test_should_leave_no_link_when_the_project_id_is_unset(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "LANGFUSE_PROJECT_ID", "")
        run = self._run()

        _attach_cache_fields([run], {"vs-1": {"runs": [{"requestId": "req-1", "traceId": "t"}]}})

        assert (run.langfuse_url, run.degraded) == (None, False)

    def test_should_leave_the_run_untouched_when_it_has_no_cache_doc(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "LANGFUSE_PROJECT_ID", "proj-1")
        run = self._run(vsid=None)

        _attach_cache_fields([run], {})

        assert (run.langfuse_url, run.degraded) == (None, None)
