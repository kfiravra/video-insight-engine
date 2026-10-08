"""Tests for the golden eval's vie-api clients (``scripts/_eval_api.py``, ``_eval_resume.py``).

vie-api and Langfuse are replaced by ``httpx.MockTransport`` handlers — no
network, no spend.
"""

from __future__ import annotations

import asyncio
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
import _eval_resume  # noqa: E402

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
        self,
        doc_statuses: list[int] | None = None,
        doc: dict[str, Any] | None = None,
        stream_statuses: list[int] | None = None,
    ) -> None:
        self.posts: list[dict[str, Any]] = []
        self.stream_opens = 0
        self.doc_statuses = list(doc_statuses or [])
        self.stream_statuses = list(stream_statuses or [])
        self.doc = doc or _DOC

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path == "/api/videos":
            self.posts.append(json.loads(request.content))
            return httpx.Response(201, json={"video": {"id": "uv1", "videoSummaryId": "vs1"}})
        if path == "/api/videos/vs1/stream":
            self.stream_opens += 1
            status = self.stream_statuses.pop(0) if self.stream_statuses else 200
            return httpx.Response(
                status, text="event: phase\ndata: {}\n\nevent: done\ndata: {}\n\n"
            )
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

    async def test_should_send_cold_when_a_cold_run_is_requested(self, mount) -> None:
        api = FakeApi()
        mount(api)
        await _eval_api.run_with_reauth(_API, _URL, "t", bypass_cache=True, cold=True)
        assert api.posts == [{"url": _URL, "bypassCache": True, "cold": True}]

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

    async def test_should_reattach_to_the_same_stream_when_stream_open_returns_401(
        self, mount
    ) -> None:
        api = FakeApi(stream_statuses=[401])
        mount(api)
        out, token = await _eval_api.run_with_reauth(_API, _URL, "old", bypass_cache=True)
        assert (len(api.posts), api.stream_opens, token) == (1, 2, "fresh-token")


class FakeAuth:
    """vie-api auth double: scripted status per endpoint, records the call order."""

    def __init__(self, login: list[int], register: int = 201) -> None:
        self.login = list(login)
        self.register = register
        self.paths: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(request.url.path)
        if request.url.path == "/api/auth/register":
            return httpx.Response(self.register, json={"accessToken": "registered"})
        return httpx.Response(self.login.pop(0), json={"accessToken": "jwt"})


@pytest.fixture
def auth_api(monkeypatch: pytest.MonkeyPatch) -> Callable[[FakeAuth], FakeAuth]:
    """Route ``authenticate``'s client through a ``FakeAuth``; registration off by default."""
    real_client = httpx.AsyncClient
    monkeypatch.setenv("EVAL_USER_PASSWORD", "EvalRunner2026!")
    monkeypatch.delenv("EVAL_ALLOW_REGISTER", raising=False)

    def _mount(fake: FakeAuth) -> FakeAuth:
        monkeypatch.setattr(
            _eval_api.httpx,
            "AsyncClient",
            lambda **kw: real_client(transport=httpx.MockTransport(fake), **kw),
        )
        return fake

    return _mount


class TestAuthenticate:
    async def test_should_log_in_without_registering_when_login_succeeds(self, auth_api) -> None:
        fake = auth_api(FakeAuth(login=[200]))
        token = await _eval_api.authenticate(_API)
        assert (token, fake.paths) == ("jwt", ["/api/auth/login"])

    async def test_should_not_register_when_login_fails_and_registration_is_not_allowed(
        self, auth_api
    ) -> None:
        fake = auth_api(FakeAuth(login=[401]))
        with pytest.raises(httpx.HTTPStatusError):
            await _eval_api.authenticate(_API)
        assert fake.paths == ["/api/auth/login"]

    async def test_should_register_when_login_fails_and_registration_is_allowed(
        self, auth_api, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("EVAL_ALLOW_REGISTER", "1")
        auth_api(FakeAuth(login=[401], register=201))
        assert await _eval_api.authenticate(_API) == "registered"

    async def test_should_fall_through_to_login_when_register_is_rate_limited(
        self, auth_api, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("EVAL_ALLOW_REGISTER", "true")
        fake = auth_api(FakeAuth(login=[401, 200], register=429))
        token = await _eval_api.authenticate(_API)
        assert (token, fake.paths[-1]) == ("jwt", "/api/auth/login")

    def test_should_refuse_when_password_is_not_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("EVAL_USER_PASSWORD", raising=False)
        with pytest.raises(RuntimeError, match="EVAL_USER_PASSWORD"):
            _eval_api._resolve_eval_credentials()


class TestSharedToken:
    async def test_should_log_in_once_when_concurrent_runs_hit_a_401_together(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        logins: list[str] = []

        async def fake_auth(api_url: str) -> str:
            logins.append(api_url)
            await asyncio.sleep(0)
            return f"token-{len(logins)}"

        monkeypatch.setattr(_eval_api, "authenticate", fake_auth)
        shared = _eval_api.SharedToken(_API, "stale")
        tokens = await asyncio.gather(*(shared.refresh("stale") for _ in range(3)))
        assert (tokens, len(logins)) == (["token-1"] * 3, 1)


# ─── Resume (prior completed runs) ─────────────────────────────────────
_SINCE = datetime(2026, 10, 7, 13, 30, tzinfo=UTC)


def _row(yid: str, status: str, created: str, uv: str) -> dict[str, Any]:
    return {
        "id": uv,
        "videoSummaryId": f"vs-{uv}",
        "youtubeId": yid,
        "status": status,
        "createdAt": created,
    }


class FakeLibrary:
    """GET /api/videos (newest first) + GET /api/videos/:id; records every request."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.requests: list[tuple[str, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((request.method, request.url.path))
        if request.url.path == "/api/videos":
            return httpx.Response(200, json={"videos": self.rows})
        row = next(r for r in self.rows if request.url.path.endswith(r["id"]))
        return httpx.Response(200, json={**_DOC, "status": row["status"]})


class TestResume:
    async def _load(self, monkeypatch: pytest.MonkeyPatch, fake: FakeLibrary, ids: set[str]):
        real_client = httpx.AsyncClient
        monkeypatch.setattr(
            _eval_resume.httpx,
            "AsyncClient",
            lambda **kw: real_client(transport=httpx.MockTransport(fake), **kw),
        )
        return await _eval_resume.load_reusable_runs(_API, "t", _SINCE, ids)

    async def test_should_reuse_only_completed_runs_created_since_the_cutoff(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakeLibrary(
            [
                _row("aaaaaaaaaaa", "completed", "2026-10-07T14:00:00.000Z", "uv1"),
                _row("bbbbbbbbbbb", "processing", "2026-10-07T13:50:00.000Z", "uv2"),
                _row("ccccccccccc", "completed", "2026-10-07T12:00:00.000Z", "uv3"),
            ]
        )
        ids = {"aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc"}
        reusable = await self._load(monkeypatch, fake, ids)
        assert sorted(reusable) == ["aaaaaaaaaaa"]

    async def test_should_never_post_when_loading_reusable_runs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakeLibrary([_row("aaaaaaaaaaa", "completed", "2026-10-07T14:00:00Z", "uv1")])
        await self._load(monkeypatch, fake, {"aaaaaaaaaaa"})
        assert {method for method, _ in fake.requests} == {"GET"}

    async def test_should_stamp_the_reused_run_with_its_creation_time(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakeLibrary([_row("aaaaaaaaaaa", "completed", "2026-10-07T14:00:00Z", "uv1")])
        reusable = await self._load(monkeypatch, fake, {"aaaaaaaaaaa"})
        out = reusable["aaaaaaaaaaa"]
        assert (out["submittedAt"], out["videoSummaryId"]) == (
            "2026-10-07T14:00:00+00:00",
            "vs-uv1",
        )

    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("https://www.youtube.com/watch?v=abcdefghijk", "abcdefghijk"),
            ("https://youtu.be/abcdefghijk", "abcdefghijk"),
            ("https://www.youtube.com/watch?list=x", None),
        ],
    )
    def test_should_extract_the_youtube_id_when_given_a_video_url(
        self, url: str, expected: str | None
    ) -> None:
        assert _eval_resume.youtube_id(url) == expected

    def test_should_refuse_a_cutoff_when_it_has_no_utc_offset(self) -> None:
        with pytest.raises(ValueError, match="UTC offset"):
            _eval_resume.parse_since("2026-10-07T16:30:00")
