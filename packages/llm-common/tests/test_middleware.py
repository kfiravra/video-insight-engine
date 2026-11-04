"""Tests for shared request context middleware."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog

from llm_common.middleware import (
    REQUEST_ID_HEADER,
    SILENT_PATHS,
    RequestContextMiddleware,
)


def _make_request(path: str = "/test", method: str = "GET", headers: dict | None = None):
    """Create a mock Starlette Request."""
    req = MagicMock()
    req.url.path = path
    req.method = method
    req.headers = headers or {}
    req.query_params = {}
    return req


def _make_response(status_code: int = 200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = {}
    return resp


class TestSilentPaths:
    def test_health_is_silent(self):
        assert "/health" in SILENT_PATHS

    def test_healthz_is_silent(self):
        assert "/healthz" in SILENT_PATHS

    def test_ready_is_silent(self):
        assert "/ready" in SILENT_PATHS

    def test_normal_path_not_silent(self):
        assert "/api/test" not in SILENT_PATHS


class TestRequestIdHeader:
    def test_header_name(self):
        assert REQUEST_ID_HEADER == "X-Request-ID"


class TestHealthCheckFilter:
    def test_filters_health_get(self):
        from llm_common.middleware import HealthCheckFilter

        f = HealthCheckFilter()
        record = MagicMock()
        record.getMessage.return_value = '"GET /health HTTP/1.1" 200'
        assert f.filter(record) is False

    def test_passes_normal_request(self):
        from llm_common.middleware import HealthCheckFilter

        f = HealthCheckFilter()
        record = MagicMock()
        record.getMessage.return_value = '"POST /api/summarize HTTP/1.1" 200'
        assert f.filter(record) is True


class TestRequestIdValidation:
    """The middleware must reject forged X-Request-ID values.

    Direct callers (internal services, tests, future load balancers) can bypass
    the API gateway's regex, so the Python side has to validate independently.
    Without this, an attacker controlling the header can forge structured log
    lines or pollute Sentry tag space via log-injection.
    """

    @pytest.fixture(autouse=True)
    def _clean_contextvars(self):
        structlog.contextvars.clear_contextvars()
        yield
        structlog.contextvars.clear_contextvars()

    def _run(self, request, response):
        middleware = RequestContextMiddleware(app=MagicMock())
        call_next = AsyncMock(return_value=response)
        return asyncio.run(middleware.dispatch(request, call_next)), call_next

    def test_accepts_safe_request_id_from_header(self):
        request = _make_request(headers={"X-Request-ID": "abc12345-OK_id"})
        response = _make_response()
        result, _ = self._run(request, response)
        assert result.headers["X-Request-ID"] == "abc12345-OK_id"

    def test_rejects_request_id_with_newline_log_injection(self):
        request = _make_request(headers={"X-Request-ID": "abc12345\nfake-line"})
        response = _make_response()
        result, _ = self._run(request, response)
        # Forged value must be replaced by a fresh UUID, not echoed.
        echoed = result.headers["X-Request-ID"]
        assert "\n" not in echoed
        assert echoed != "abc12345\nfake-line"
        assert len(echoed) >= 8

    def test_rejects_too_short_request_id(self):
        request = _make_request(headers={"X-Request-ID": "short"})
        response = _make_response()
        result, _ = self._run(request, response)
        assert result.headers["X-Request-ID"] != "short"

    def test_rejects_request_id_with_semicolon(self):
        # Some structured loggers parse `key=value;key=value`; semicolons in an
        # id could split a single log line into two records.
        request = _make_request(headers={"X-Request-ID": "abc12345;fake=1"})
        response = _make_response()
        result, _ = self._run(request, response)
        assert ";" not in result.headers["X-Request-ID"]

    def test_generates_uuid_when_header_missing(self):
        request = _make_request(headers={})
        response = _make_response()
        result, _ = self._run(request, response)
        assert len(result.headers["X-Request-ID"]) >= 8

    def test_rejects_request_id_with_trailing_newline(self):
        # Python's `$` anchor matches just before a final `\n`, so a pattern
        # like `^[A-Za-z0-9_-]{8,128}$` would silently accept this payload —
        # exactly the bypass `\A`/`\Z` guards against. If this test fails the
        # anchors have been weakened back to `^`/`$`.
        request = _make_request(headers={"X-Request-ID": "abcd12345\n"})
        response = _make_response()
        result, _ = self._run(request, response)
        echoed = result.headers["X-Request-ID"]
        assert "\n" not in echoed
        assert echoed != "abcd12345\n"

    def test_rejects_request_id_with_trailing_carriage_return(self):
        # `\r` isn't in the allowed char class, but pin the behavior so a
        # future "be more lenient" change can't sneak it in.
        request = _make_request(headers={"X-Request-ID": "abcd12345\r"})
        response = _make_response()
        result, _ = self._run(request, response)
        assert "\r" not in result.headers["X-Request-ID"]

    def test_accepts_request_id_at_exact_min_length(self):
        # 8 chars is the documented minimum.
        request = _make_request(headers={"X-Request-ID": "abcd1234"})
        response = _make_response()
        result, _ = self._run(request, response)
        assert result.headers["X-Request-ID"] == "abcd1234"

    def test_accepts_request_id_at_exact_max_length(self):
        rid = "a" * 128
        request = _make_request(headers={"X-Request-ID": rid})
        response = _make_response()
        result, _ = self._run(request, response)
        assert result.headers["X-Request-ID"] == rid

    def test_rejects_request_id_over_max_length(self):
        forged = "a" * 129
        request = _make_request(headers={"X-Request-ID": forged})
        response = _make_response()
        result, _ = self._run(request, response)
        assert result.headers["X-Request-ID"] != forged

    def test_rejects_request_id_one_below_min_length(self):
        # Boundary test — 7 chars must not slip through.
        request = _make_request(headers={"X-Request-ID": "abcd123"})
        response = _make_response()
        result, _ = self._run(request, response)
        assert result.headers["X-Request-ID"] != "abcd123"

    def test_rejects_unicode_lookalike(self):
        # The first char is Cyrillic 'а' (U+0430), not Latin 'a'. The ASCII
        # char class must reject it so unicode tricks can't bypass the guard.
        forged = "а" + "bcd12345"
        request = _make_request(headers={"X-Request-ID": forged})
        response = _make_response()
        result, _ = self._run(request, response)
        assert result.headers["X-Request-ID"] != forged

    def test_generates_uuid_when_header_is_empty_string(self):
        # Empty string is falsy in Python, so the short-circuit `if incoming`
        # falls through to UUID generation without touching the regex.
        request = _make_request(headers={"X-Request-ID": ""})
        response = _make_response()
        result, _ = self._run(request, response)
        assert len(result.headers["X-Request-ID"]) >= 8


class TestSessionId:
    """``X-Session-Id`` (the web's chat session, via vie-api) reaches logs and the ledger."""

    @pytest.fixture(autouse=True)
    def _clean_contextvars(self):
        structlog.contextvars.clear_contextvars()
        yield
        structlog.contextvars.clear_contextvars()

    def _run_capturing(self, headers: dict) -> dict:
        from llm_common.context import llm_session_id_var

        seen: dict = {}

        async def call_next(_request):
            seen["log"] = structlog.contextvars.get_contextvars().get("session_id")
            seen["ledger"] = llm_session_id_var.get()
            return _make_response()

        middleware = RequestContextMiddleware(app=MagicMock())
        asyncio.run(middleware.dispatch(_make_request(headers=headers), call_next))
        seen["after"] = llm_session_id_var.get()
        return seen

    def test_should_bind_the_session_for_logs_and_ledger_when_the_header_is_valid(self):
        seen = self._run_capturing({"X-Session-Id": "3f2b8c1e-7a4d-4e5f-9b6a-1c2d3e4f5a6b"})
        assert seen["log"] == seen["ledger"] == "3f2b8c1e-7a4d-4e5f-9b6a-1c2d3e4f5a6b"

    def test_should_drop_the_session_when_the_header_is_forged(self):
        seen = self._run_capturing({"X-Session-Id": "abc12345\nforged"})
        assert seen["log"] is None
        assert seen["ledger"] is None

    def test_should_bind_no_session_when_the_header_is_absent(self):
        seen = self._run_capturing({})
        assert seen["log"] is None
        assert seen["ledger"] is None

    def test_should_reset_the_ledger_session_when_the_request_ends(self):
        seen = self._run_capturing({"X-Session-Id": "chat-session-abcdef12"})
        assert seen["after"] is None
