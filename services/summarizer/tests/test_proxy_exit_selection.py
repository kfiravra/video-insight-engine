"""Proxy exit selection (pipeline-1min 1a.6): round-robin start per job, blocked exits skipped.

Each job key (youtube id, playlist id) starts on the next sticky exit of the
pool; an exit that got a bot check or 429 moves behind the others for
_BLOCKED_EXIT_TTL_SECONDS. The conftest autouse fixture gives every test a
fresh ``_ExitMemory``.
"""

from __future__ import annotations

import threading

import pytest

from src.services.media import download_utils

EXITS = [f"http://user-{n}:pass@p.webshare.io:80" for n in (1, 2, 3)]


class _Blocked(Exception):
    pass


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture(autouse=True)
def three_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(download_utils.settings, "YOUTUBE_PROXY_URL", EXITS[0])
    monkeypatch.setattr(download_utils.settings, "YOUTUBE_PROXY_EXIT_COUNT", 3)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    fake = _Clock()
    monkeypatch.setattr(download_utils, "_EXIT_MEMORY", download_utils._ExitMemory(clock=fake))
    return fake


def _rotate(job_key: str | None, blocked: set[str]) -> list[str]:
    """One rotation for ``job_key``; returns the exits tried."""
    tried: list[str] = []

    def attempt(proxy_url: str) -> str:
        tried.append(proxy_url)
        if proxy_url in blocked:
            raise _Blocked
        return "ok"

    download_utils.try_proxy_exits(
        download_utils.ytdlp_proxy_exit_urls(job_key),
        attempt,
        lambda e: isinstance(e, _Blocked),
        "Test fetch",
    )
    return tried


class TestRoundRobinStart:
    def test_should_start_each_new_job_on_the_next_exit(self) -> None:
        starts = [download_utils.ytdlp_proxy_exit_urls(f"video{n}")[0] for n in range(4)]

        assert starts == [EXITS[0], EXITS[1], EXITS[2], EXITS[0]]

    def test_should_keep_a_jobs_exit_for_every_call_of_that_job(self) -> None:
        download_utils.ytdlp_proxy_exit_urls("video0")
        first = download_utils.ytdlp_proxy_exit_urls("video1")

        assert download_utils.ytdlp_proxy_exit_urls("video1") == first

    def test_should_rotate_on_from_the_jobs_exit_and_wrap(self) -> None:
        download_utils.ytdlp_proxy_exit_urls("video0")

        assert download_utils.ytdlp_proxy_exit_urls("video1") == [EXITS[1], EXITS[2], EXITS[0]]

    def test_should_start_on_the_configured_exit_without_a_job_key(self) -> None:
        download_utils.ytdlp_proxy_exit_urls("video0")

        assert download_utils.ytdlp_proxy_exit_urls() == EXITS

    def test_should_round_robin_over_the_whole_pool_not_only_the_rotation_cap(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(download_utils.settings, "YOUTUBE_PROXY_EXIT_COUNT", 5)

        starts = [download_utils.ytdlp_proxy_exit_urls(f"video{n}")[0] for n in range(5)]

        assert [s.split("@")[0] for s in starts] == [f"http://user-{n}:pass" for n in range(1, 6)]

    def test_should_forget_the_oldest_job_beyond_the_tracking_bound(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(download_utils, "_MAX_TRACKED_JOBS", 2)
        for n in range(3):
            download_utils.ytdlp_proxy_exit_urls(f"video{n}")

        # video0 was evicted, so it is a new job (4th) again: offset 3 → exit 1.
        assert download_utils.ytdlp_proxy_exit_urls("video0")[0] == EXITS[0]
        assert len(download_utils._EXIT_MEMORY._job_offsets) == 2


class TestBlockedExits:
    def test_should_skip_an_exit_blocked_by_an_earlier_job(self) -> None:
        _rotate("video0", blocked={EXITS[0]})

        # video1 starts on exit 2 anyway; video2 would start on exit 3; the
        # 4th job would start on exit 1 but it is blocked, so it moves behind.
        _rotate("video1", blocked=set())
        _rotate("video2", blocked=set())

        assert _rotate("video3", blocked=set()) == [EXITS[1]]

    def test_should_start_on_the_next_fresh_exit_when_the_jobs_exit_is_blocked(self) -> None:
        download_utils.record_blocked_exit(EXITS[1])
        download_utils.ytdlp_proxy_exit_urls("video0")

        assert download_utils.ytdlp_proxy_exit_urls("video1") == [EXITS[2], EXITS[0], EXITS[1]]

    def test_should_still_try_blocked_exits_last_when_every_exit_is_blocked(self) -> None:
        for url in EXITS:
            download_utils.record_blocked_exit(url)

        with pytest.raises(_Blocked):
            _rotate("video0", blocked=set(EXITS))

    def test_should_unmark_an_exit_that_works_again(self) -> None:
        download_utils.record_blocked_exit(EXITS[0])
        download_utils.record_working_exit(EXITS[0], "Test", 1, 1)

        assert download_utils.ytdlp_proxy_exit_urls("video0")[0] == EXITS[0]

    def test_should_retry_a_blocked_exit_once_the_window_passed(self, clock: _Clock) -> None:
        download_utils.record_blocked_exit(EXITS[0])
        assert download_utils.ytdlp_proxy_exit_urls()[0] == EXITS[1]

        clock.now += download_utils._BLOCKED_EXIT_TTL_SECONDS

        assert download_utils.ytdlp_proxy_exit_urls()[0] == EXITS[0]

    def test_should_continue_through_the_others_when_the_jobs_exit_fails(self) -> None:
        tried = _rotate("video0", blocked={EXITS[0], EXITS[1]})

        assert tried == EXITS
        assert download_utils.ytdlp_proxy_exit_urls() == [EXITS[2], EXITS[0], EXITS[1]]


class TestConcurrency:
    def test_should_stay_consistent_under_concurrent_rotations(self) -> None:
        """Worker threads (asyncio.to_thread) rotate at once: every list must stay
        a reordering of the pool, every job gets one offset, and nothing raises."""
        orders: list[list[str]] = []
        errors: list[BaseException] = []

        def worker(n: int) -> None:
            try:
                for i in range(200):
                    url = EXITS[(n + i) % 3]
                    download_utils.record_blocked_exit(url)
                    download_utils.record_working_exit(url, "T", 1, 1)
                    orders.append(download_utils.ytdlp_proxy_exit_urls(f"video{n}-{i % 10}"))
            except BaseException as e:  # noqa: BLE001 — surfaced by the assert below
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert errors == []
        assert all(sorted(order) == EXITS for order in orders)
        assert sorted(download_utils._EXIT_MEMORY._job_offsets.values()) == list(range(80))
