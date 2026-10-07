"""Unit tests for the replay harness parts: network guard, fake LLM, report, cassettes."""

from __future__ import annotations

import json
import re
import socket
import threading
from collections.abc import Callable

import pytest
from litellm import Choices, ModelResponse
from llm_common.context import llm_feature_var

from tests.replay.cassette import CASSETTE_DIR, LLMEntry, LLMKey, available_cassettes
from tests.replay.fake_llm import CassetteMissError, ReplayLLM
from tests.replay.network_guard import NetworkBlockedError, block_network
from tests.replay.report import PhaseRow

_BENCHMARK_VIDEOS = ("T1dQhQAm8Tc", "jMq8lEu-of0", "uC45_4nnEAI")
# Anything that looks like a credential, a signed URL or a personal address.
_SECRET_PATTERNS = {
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "aws access key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "signed url": re.compile(r"X-Amz-(?:Signature|Credential)|Signature=|[?&]token=", re.I),
    "api key": re.compile(r"\b(?:sk-[A-Za-z0-9_-]{16,}|sk-ant-|pk-lf-|sk-lf-)"),
    "bearer": re.compile(r"Bearer\s+[A-Za-z0-9._-]{12,}"),
}


def _entry(feature: str, span: str | None, ordinal: int, output: str = "{}") -> LLMEntry:
    return LLMEntry(
        key=LLMKey(feature=feature, span=span, ordinal=ordinal),
        model="openai/gpt-4o-mini",
        latency_ms=1234,
        output=output,
        finish_reason="stop",
        input_tokens=10,
        output_tokens=5,
    )


# ─── Network guard ───


def _resolve_localhost(release: threading.Event, outcome: list[str]) -> None:
    release.wait(timeout=5)
    try:
        socket.getaddrinfo("localhost", 80)
        outcome.append("ok")
    except OSError as exc:
        outcome.append(type(exc).__name__)


def _run_in_thread(*, started_before_guard: bool) -> tuple[list[str], str]:
    """Resolve localhost (no network) from a thread started before/inside the guard."""
    release, outcome = threading.Event(), []
    make: Callable[[], threading.Thread] = lambda: threading.Thread(  # noqa: E731
        target=_resolve_localhost, args=(release, outcome), name="foreign-lib-thread"
    )
    thread = make() if started_before_guard else None
    if thread:
        thread.start()
    with block_network() as attempts:
        if thread is None:
            thread = make()
            thread.start()
        release.set()
        thread.join(timeout=5)
    return list(attempts), outcome[0]


class TestNetworkGuard:
    def test_should_refuse_tcp_connect_when_guard_active(self) -> None:
        with block_network(), pytest.raises(NetworkBlockedError):
            socket.create_connection(("127.0.0.1", 9), timeout=1)

    def test_should_record_refused_target_when_guard_active(self) -> None:
        with block_network() as attempts:
            with pytest.raises(OSError):
                socket.create_connection(("127.0.0.1", 9), timeout=1)
        assert any("127.0.0.1" in a for a in attempts)

    def test_should_refuse_dns_lookup_when_guard_active(self) -> None:
        with block_network(), pytest.raises(NetworkBlockedError):
            socket.getaddrinfo("example.com", 443)

    def test_should_let_preexisting_foreign_thread_resolve(self) -> None:
        outcome = _run_in_thread(started_before_guard=True)
        assert outcome == ([], "ok")

    def test_should_refuse_thread_started_under_guard(self) -> None:
        attempts, result = _run_in_thread(started_before_guard=False)
        assert (len(attempts), result) == (1, "NetworkBlockedError")

    def test_should_restore_connect_when_guard_exits(self) -> None:
        original = socket.socket.connect
        with block_network():
            pass
        assert socket.socket.connect is original


# ─── Fake LLM ───


class TestReplayLLM:
    async def test_should_serve_recorded_output_for_feature_and_ordinal(self) -> None:
        fake = ReplayLLM(
            [_entry("summarize:plan", None, 0, "first"), _entry("summarize:plan", None, 1, "2nd")],
            speed=0,
            video_id="vid",
        )
        token = llm_feature_var.set("summarize:plan")
        try:
            await fake.acompletion(model="openai/gpt-4o-mini")
            second = await fake.acompletion(model="openai/gpt-4o-mini")
        finally:
            llm_feature_var.reset(token)
        assert isinstance(second, ModelResponse)
        choice = second.choices[0]
        assert isinstance(choice, Choices)
        assert choice.message.content == "2nd"

    async def test_should_raise_cassette_miss_when_call_was_not_recorded(self) -> None:
        fake = ReplayLLM([_entry("summarize:plan", None, 0)], speed=0, video_id="vid")
        token = llm_feature_var.set("summarize:extraction")
        try:
            with pytest.raises(CassetteMissError, match="summarize:extraction"):
                await fake.acompletion(model="openai/gpt-4o-mini")
        finally:
            llm_feature_var.reset(token)

    async def test_should_report_unused_recordings(self) -> None:
        fake = ReplayLLM([_entry("summarize:plan", None, 0)], speed=0, video_id="vid")
        assert fake.unused_keys() == ["summarize:plan/None#0"]

    async def test_should_flag_model_mismatch_when_requested_model_differs(self) -> None:
        fake = ReplayLLM([_entry("summarize:plan", None, 0)], speed=0, video_id="vid")
        token = llm_feature_var.set("summarize:plan")
        try:
            await fake.acompletion(model="anthropic/claude-sonnet-4-6")
        finally:
            llm_feature_var.reset(token)
        assert len(fake.model_mismatches) == 1


# ─── Report ───


class TestPhaseRow:
    def test_should_be_within_tolerance_when_delta_under_ten_percent(self) -> None:
        assert PhaseRow("plan", recorded_ms=30_000, replayed_ms=32_900).within(0.10)

    def test_should_be_outside_tolerance_when_delta_over_ten_percent(self) -> None:
        assert not PhaseRow("plan", recorded_ms=30_000, replayed_ms=33_100).within(0.10)

    def test_should_allow_absolute_slack_for_tiny_phases(self) -> None:
        assert PhaseRow("visual_inject", recorded_ms=0, replayed_ms=400).within(0.10)

    def test_should_fail_when_phase_missing_from_replay(self) -> None:
        assert not PhaseRow("plan", recorded_ms=30_000, replayed_ms=None).within(0.10)


# ─── Cassettes ───


class TestCassettes:
    def test_should_ship_a_cassette_per_benchmark_video(self) -> None:
        assert set(_BENCHMARK_VIDEOS) <= set(available_cassettes())

    @pytest.mark.parametrize("video_id", available_cassettes())
    @pytest.mark.parametrize("kind", sorted(_SECRET_PATTERNS))
    def test_should_hold_no_credentials_or_emails(self, video_id: str, kind: str) -> None:
        text = (CASSETTE_DIR / f"{video_id}.json").read_text(encoding="utf-8")
        assert _SECRET_PATTERNS[kind].findall(text) == []

    @pytest.mark.parametrize("video_id", available_cassettes())
    def test_should_record_phase_walls_summing_to_total(self, video_id: str) -> None:
        raw = json.loads((CASSETTE_DIR / f"{video_id}.json").read_text(encoding="utf-8"))
        recorded = raw["recorded"]
        assert abs(sum(recorded["phasesMs"].values()) - recorded["totalMs"]) <= 10
