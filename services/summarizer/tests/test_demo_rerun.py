"""Tests for ``scripts/demo_rerun.py`` (pipeline-1min 3.9): the demo library re-run.

Mongo is an in-memory fake that evaluates the few query operators the script
uses; vie-api is an ``httpx.MockTransport`` handler — no network, no spend.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))

import _demo_rerun_sources as sources  # noqa: E402
import _eval_api  # noqa: E402
import demo_rerun  # noqa: E402

from src.shared_config.pipeline_version import get_pipeline_version  # noqa: E402

_TOKEN = "jwt-demo-secret"
_DEMO_EMAIL = "demo@example.com"
_URLS_TXT = (
    "https://www.youtube.com/watch?v=T1dQhQAm8Tc\tReact in 100 seconds\n"
    "\n"
    "https://www.youtube.com/watch?v=jMq8lEu-of0\tOne-pot pasta\n"
    "https://www.youtube.com/watch?v=T1dQhQAm8Tc\tduplicate line\n"
)


# ─── Fakes ───


def _matches(doc: Mapping[str, Any], query: Mapping[str, Any]) -> bool:
    """Equality, ``$ne``, ``$in`` and ``$type: string`` — what the selections send."""
    for key, condition in query.items():
        value = doc.get(key)
        if not isinstance(condition, dict):
            if value != condition:
                return False
            continue
        if "$ne" in condition and value == condition["$ne"]:
            return False
        if "$in" in condition and value not in condition["$in"]:
            return False
        if condition.get("$type") == "string" and not isinstance(value, str):
            return False
    return True


class FakeCollection:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self.docs = docs

    def find(self, filter: Mapping[str, Any], projection: Mapping[str, Any]) -> Iterable[Any]:  # noqa: ARG002
        return [doc for doc in self.docs if _matches(doc, filter)]

    def find_one(self, filter: Mapping[str, Any], projection: Mapping[str, Any]) -> Any:  # noqa: ARG002
        return next(iter(self.find(filter, projection)), None)

    def distinct(self, key: str, filter: Mapping[str, Any]) -> list[Any]:
        return sorted({doc.get(key) for doc in self.find(filter, {})})


def _summary(sid: str, ytid: str, version: str | None, **fields: Any) -> dict[str, Any]:
    doc = {"_id": sid, "youtubeId": ytid, "title": ytid, "status": "completed", "duration": 600}
    if version is not None:
        doc["pipelineVersion"] = version
    return {**doc, **fields}


def _db() -> dict[str, FakeCollection]:
    """Demo owns A (v8), B (already v9), C (failed run); 'other' owns D (v8) and A."""
    return {
        "users": FakeCollection([{"_id": "u-demo", "email": _DEMO_EMAIL}, {"_id": "u-other"}]),
        "userVideos": FakeCollection(
            [
                {"userId": "u-demo", "youtubeId": "aaaaaaaaaaa", "videoSummaryId": "s-a"},
                {"userId": "u-demo", "youtubeId": "bbbbbbbbbbb", "videoSummaryId": "s-b"},
                {"userId": "u-demo", "youtubeId": "ccccccccccc", "videoSummaryId": "s-c"},
                {"userId": "u-other", "youtubeId": "ddddddddddd", "videoSummaryId": "s-d"},
                {"userId": "u-other", "youtubeId": "aaaaaaaaaaa", "videoSummaryId": "s-a"},
            ]
        ),
        "videoSummaryCache": FakeCollection(
            [
                _summary("s-a", "aaaaaaaaaaa", "v8", duration=3000),
                _summary("s-b", "bbbbbbbbbbb", "v9"),
                _summary("s-c", "ccccccccccc", "v9", status="failed"),
                _summary("s-d", "ddddddddddd", "v8"),
            ]
        ),
    }


class FakeApi:
    """vie-api double: demo login → POST → SSE terminal event → doc."""

    def __init__(
        self,
        failing: frozenset[str] = frozenset(),
        login_status: int = 200,
        post_status: int = 201,
        stream_status: int = 200,
        served_ids: Mapping[str, str] | None = None,
    ) -> None:
        self.failing = failing
        self.login_status = login_status
        self.post_status = post_status
        self.stream_status = stream_status
        self.served_ids = dict(served_ids or {})
        self.logins: list[dict[str, Any]] = []
        self.posts: list[dict[str, Any]] = []
        self.auth_headers: set[str] = set()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/auth/login":
            self.logins.append(json.loads(request.content))
            return httpx.Response(self.login_status, json={"accessToken": _TOKEN})
        self.auth_headers.add(request.headers.get("authorization", ""))
        if request.method == "POST":
            return self._post(json.loads(request.content))
        if path.endswith("/stream"):
            return httpx.Response(self.stream_status, text="event: done\ndata: {}\n\n")
        ytid = path.rsplit("/", 1)[1]
        doc = {
            "status": "failed" if ytid in self.failing else "completed",
            "error": "boom",
            "videoSummaryId": self.served_ids.get(ytid, f"vs-{ytid}"),
        }
        return httpx.Response(200, json=doc)

    def _post(self, body: dict[str, Any]) -> httpx.Response:
        self.posts.append(body)
        if self.post_status != 201:
            refusal = {"error": "RATE_LIMITED", "message": "Too many videos today"}
            return httpx.Response(self.post_status, json=refusal)
        ytid = body["url"].rsplit("=", 1)[1]
        return httpx.Response(201, json={"video": {"id": ytid, "videoSummaryId": f"vs-{ytid}"}})


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> Callable[[FakeApi], FakeApi]:
    """Route every httpx client (demo login + ``_eval_api``) through a ``FakeApi``."""
    real_client = httpx.AsyncClient

    def _mount(fake: FakeApi) -> FakeApi:
        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            lambda **kw: real_client(transport=httpx.MockTransport(fake), **kw),
        )
        monkeypatch.setattr(_eval_api.asyncio, "sleep", _no_sleep)
        return fake

    return _mount


async def _no_sleep(_: float) -> None:
    return None


@pytest.fixture
def mongo(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Serve ``_db()`` for any URI; returns the URIs the script opened."""
    opened: list[str] = []

    @contextmanager
    def fake_open(uri: str, name: str) -> Iterator[Any]:  # noqa: ARG001
        opened.append(uri)
        yield _db()

    monkeypatch.setattr(sources, "open_database", fake_open)
    return opened


@pytest.fixture(autouse=True)
def _no_stack_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Other summarizer imports load the repo .env; these tests pass what they need."""
    for name in ("DEMO_USER_EMAIL", "MONGODB_URI", "MONGODB_DB"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def pinned(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests read ``v9`` as current whatever the next bump writes."""
    monkeypatch.setattr(demo_rerun, "current_pipeline_version", lambda: "v9")


@pytest.fixture
def urls_file(tmp_path: Path) -> Path:
    path = tmp_path / "urls.txt"
    path.write_text(_URLS_TXT, encoding="utf-8")
    return path


def _out(capsys: pytest.CaptureFixture[str]) -> str:
    captured = capsys.readouterr()
    return captured.out + captured.err


# ─── Tests ───


class TestVersion:
    def test_should_read_the_version_the_summarizer_reads(self) -> None:
        assert sources.current_pipeline_version() == get_pipeline_version()


@pytest.mark.usefixtures("pinned")
class TestDryRun:
    def test_should_list_videos_and_cost_without_submitting_when_demo_rerun_runs_dry(
        self, api, urls_file: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake = api(FakeApi())

        code = demo_rerun.main(["--urls", str(urls_file)])

        out = _out(capsys)
        listed = ("T1dQhQAm8Tc" in out, "jMq8lEu-of0" in out)
        cost = "2 videos: 2 × ≈ $0.15–0.33 = ≈ $0.30–0.66" in out
        assert (code, listed, cost, fake.logins, fake.posts) == (0, (True, True), True, [], [])

    def test_should_list_only_the_demo_users_stale_entries_by_default(
        self, mongo, capsys: pytest.CaptureFixture[str]
    ) -> None:
        demo_rerun.main(["--demo-email", "  Demo@Example.com "])

        out = _out(capsys)
        rows = ("aaaaaaaaaaa" in out, "bbbbbbbbbbb  " in out, "ddddddddddd" in out)
        assert (rows, "ccccccccccc (failed)" in out) == ((True, False, False), True)

    def test_should_exit_2_when_no_demo_email_is_known(self, mongo) -> None:
        assert demo_rerun.main([]) == 2

    def test_should_show_owners_when_listing_the_whole_cache(
        self, mongo, capsys: pytest.CaptureFixture[str]
    ) -> None:
        demo_rerun.main(["--all-cache", "--demo-email", _DEMO_EMAIL])

        rows = [
            line for line in _out(capsys).splitlines() if "aaaaaaaaaaa" in line or "ddddd" in line
        ]
        assert (" demo+1 " in rows[0], " 1 user " in rows[1]) == (True, True)

    def test_should_print_the_api_url_in_the_header(
        self, urls_file: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        demo_rerun.main(["--urls", str(urls_file), "--api-url", "https://vie.example"])

        assert "api: https://vie.example" in _out(capsys)

    def test_should_refuse_a_remote_api_when_the_list_comes_from_local_mongo(self, mongo) -> None:
        code = demo_rerun.main(["--demo-email", _DEMO_EMAIL, "--api-url", "https://vie.example"])

        assert (code, mongo) == (2, [])

    def test_should_accept_a_remote_api_when_the_mongo_uri_is_explicit(self, mongo) -> None:
        argv = ["--demo-email", _DEMO_EMAIL, "--api-url", "https://vie.example"]

        code = demo_rerun.main([*argv, "--mongo-uri", "mongodb://tunnel:27018"])

        assert (code, mongo) == (0, ["mongodb://tunnel:27018"])

    def test_should_mark_videos_over_40_minutes_as_chunked_and_leave_them_out_of_the_band(
        self, mongo, capsys: pytest.CaptureFixture[str]
    ) -> None:
        demo_rerun.main(["--all-cache"])

        out = _out(capsys)
        row = next(line for line in out.splitlines() if "aaaaaaaaaaa" in line)
        total = "2 videos: 1 × ≈ $0.15–0.33 = ≈ $0.15–0.33 (concurrency 1) + 1 chunked" in out
        assert ("50 min" in row, "[chunked, band unmeasured]" in row, total) == (True, True, True)

    def test_should_exit_2_when_the_urls_file_has_a_line_without_a_youtube_id(
        self, tmp_path: Path
    ) -> None:
        bad = tmp_path / "urls.txt"
        bad.write_text("not a url\tTitle\n", encoding="utf-8")

        assert demo_rerun.main(["--urls", str(bad)]) == 2


class TestParseUrlsFile:
    def test_should_keep_the_first_occurrence_of_each_id_and_skip_blank_lines(self) -> None:
        videos = sources.parse_urls_file(_URLS_TXT)

        assert [(v.youtube_id, v.title) for v in videos] == [
            ("T1dQhQAm8Tc", "React in 100 seconds"),
            ("jMq8lEu-of0", "One-pot pasta"),
        ]

    def test_should_name_the_line_when_it_has_no_youtube_id(self) -> None:
        with pytest.raises(sources.SourceError, match="line 2"):
            sources.parse_urls_file("https://youtu.be/T1dQhQAm8Tc\nhttps://example.com/x\n")

    def test_should_read_short_links_and_bare_ids(self) -> None:
        videos = sources.parse_urls_file("https://youtu.be/jMq8lEu-of0\nT1dQhQAm8Tc\n")

        assert [v.youtube_id for v in videos] == ["jMq8lEu-of0", "T1dQhQAm8Tc"]


class TestSelectStale:
    def test_should_not_count_an_eval_only_current_version_doc_as_done(self) -> None:
        cache = [
            _summary("s1", "aaaaaaaaaaa", "v8"),
            _summary("s2", "aaaaaaaaaaa", "v9", evalRun=True),
        ]
        db = {"videoSummaryCache": FakeCollection(cache), "userVideos": FakeCollection([])}

        selection = sources.select_stale(db, "v9")  # type: ignore[arg-type]

        assert [v.youtube_id for v in selection.videos] == ["aaaaaaaaaaa"]

    def test_should_skip_videos_completed_at_the_current_version_and_eval_rows(self) -> None:
        cache = [
            _summary("s1", "aaaaaaaaaaa", "v8"),
            _summary("s2", "aaaaaaaaaaa", "v9"),
            _summary("s3", "bbbbbbbbbbb", "v8", evalRun=True),
            _summary("s4", "ccccccccccc", None),
        ]
        db = {"videoSummaryCache": FakeCollection(cache), "userVideos": FakeCollection([])}

        assert sources.select_stale(db, "v9").videos == []  # type: ignore[arg-type]


@pytest.mark.usefixtures("pinned")
class TestApply:
    def test_should_submit_each_video_with_bypass_cache_as_the_demo_user_when_applied(
        self, api, urls_file: Path
    ) -> None:
        fake = api(FakeApi())

        code = demo_rerun.main(["--urls", str(urls_file), "--apply", "--api-url", "http://api.t/"])

        assert (code, fake.logins, fake.posts, fake.auth_headers) == (
            0,
            [{"demo": True}],
            [
                {"url": "https://www.youtube.com/watch?v=T1dQhQAm8Tc", "bypassCache": True},
                {"url": "https://www.youtube.com/watch?v=jMq8lEu-of0", "bypassCache": True},
            ],
            {f"Bearer {_TOKEN}"},
        )

    def test_should_stop_at_the_first_failure_when_keep_going_is_off(
        self, api, urls_file: Path
    ) -> None:
        fake = api(FakeApi(failing=frozenset({"T1dQhQAm8Tc"})))

        code = demo_rerun.main(["--urls", str(urls_file), "--apply"])

        assert (code, len(fake.posts)) == (1, 1)

    def test_should_run_every_video_when_keep_going_is_on(self, api, urls_file: Path) -> None:
        fake = api(FakeApi(failing=frozenset({"T1dQhQAm8Tc"})))

        code = demo_rerun.main(["--urls", str(urls_file), "--apply", "--keep-going"])

        assert (code, len(fake.posts)) == (1, 2)

    def test_should_stop_and_show_the_error_code_when_the_api_refuses_a_submission(
        self, api, urls_file: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake = api(FakeApi(post_status=429))

        code = demo_rerun.main(["--urls", str(urls_file), "--apply"])

        out = _out(capsys)
        left = "Left to re-run: T1dQhQAm8Tc jMq8lEu-of0" in out
        refused = "FAILED (unchanged) — HTTP 429 RATE_LIMITED" in out
        assert (code, len(fake.posts), refused, left) == (1, 1, True, True)

    def test_should_write_what_is_left_as_a_urls_file_it_can_resume_from(
        self, api, urls_file: Path
    ) -> None:
        api(FakeApi(failing=frozenset({"jMq8lEu-of0"})))

        demo_rerun.main(["--urls", str(urls_file), "--apply"])

        left = Path(f"{urls_file}.left.txt").read_text(encoding="utf-8")
        assert left == "https://www.youtube.com/watch?v=jMq8lEu-of0\tOne-pot pasta\n"

    def test_should_submit_one_video_when_limited_to_one(self, api, urls_file: Path) -> None:
        fake = api(FakeApi())

        demo_rerun.main(["--urls", str(urls_file), "--apply", "--limit", "1"])

        assert [post["url"][-11:] for post in fake.posts] == ["T1dQhQAm8Tc"]

    def test_should_submit_only_the_named_video_when_only_is_given(
        self, api, urls_file: Path
    ) -> None:
        fake = api(FakeApi())

        demo_rerun.main(["--urls", str(urls_file), "--apply", "--only", "jMq8lEu-of0"])

        assert [post["url"][-11:] for post in fake.posts] == ["jMq8lEu-of0"]

    def test_should_report_a_restored_entry_when_the_library_still_points_at_the_old_doc(
        self, api
    ) -> None:
        api(FakeApi(served_ids={"aaaaaaaaaaa": "s-old"}))
        video = sources.DemoVideo("aaaaaaaaaaa", "A", "v8", summary_id="s-old")

        outcomes = asyncio.run(demo_rerun.rerun([video], "http://api.t", keep_going=False))

        assert [(o.ok, o.entry) for o in outcomes] == [(False, "restored")]

    def test_should_name_the_entry_to_check_when_a_run_never_ends(
        self, api, urls_file: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        api(FakeApi(stream_status=502))

        demo_rerun.main(["--urls", str(urls_file), "--apply"])

        out = _out(capsys)
        check = "Library entries to check (may point at an unfinished version): T1dQhQAm8Tc" in out
        assert (check, "Left to re-run: jMq8lEu-of0\n" in out) == (True, True)

    def test_should_submit_nothing_when_the_demo_login_is_refused(
        self, api, urls_file: Path
    ) -> None:
        fake = api(FakeApi(login_status=403))

        code = demo_rerun.main(["--urls", str(urls_file), "--apply"])

        assert (code, fake.posts) == (1, [])

    def test_should_never_print_the_access_token(
        self, api, urls_file: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        api(FakeApi(failing=frozenset({"jMq8lEu-of0"})))

        demo_rerun.main(["--urls", str(urls_file), "--apply", "--keep-going"])

        assert _TOKEN not in _out(capsys)
