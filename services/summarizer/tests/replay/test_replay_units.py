"""Unit tests for the replay harness parts: network guard, fake LLM, report, cassettes."""

from __future__ import annotations

import json
import re
import socket
import sys
import threading
import types
from collections.abc import Callable

import pytest
from litellm import Choices, ModelResponse
from llm_common.context import llm_feature_var

from tests.replay.cassette import CASSETTE_DIR, LLMEntry, LLMKey, available_cassettes
from tests.replay.cassette_timing import phase_bounds
from tests.replay.fake_llm import CassetteMissError, ReplayLLM
from tests.replay.network_guard import NetworkBlockedError, block_network
from tests.replay.driver import ReplayResult
from tests.replay.process_state import UnsnapshottedModulesError, preserved_process_state
from tests.replay.report import PhaseRow, check_speed, divergences

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

    def test_should_refuse_thread_reusing_finished_foreign_thread_slot(self) -> None:
        # A foreign thread ends while the guard is active; a replay thread
        # started afterwards (often recycling its ident) is still policed.
        release, outcome = threading.Event(), []
        foreign = threading.Thread(target=release.wait, args=(5,), name="foreign-lib-thread")
        foreign.start()
        with block_network() as attempts:
            release.set()
            foreign.join(timeout=5)
            late = threading.Thread(target=_resolve_localhost, args=(release, outcome))
            late.start()
            late.join(timeout=5)
        assert (len(attempts), outcome) == (1, ["NetworkBlockedError"])

    def test_should_refuse_gethostbyname_when_guard_active(self) -> None:
        with block_network(), pytest.raises(NetworkBlockedError):
            socket.gethostbyname("example.com")

    def test_should_refuse_udp_sendto_when_guard_active(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
            with block_network(), pytest.raises(NetworkBlockedError):
                udp.sendto(b"x", ("127.0.0.1", 9))

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


# ─── Process state ───


class TestPreservedProcessState:
    def test_should_fail_naming_src_module_first_imported_during_replay(self) -> None:
        name = "src._replay_probe_module"
        try:
            with pytest.raises(UnsnapshottedModulesError, match=name):
                with preserved_process_state():
                    sys.modules[name] = types.ModuleType(name)
        finally:
            sys.modules.pop(name, None)


# ─── Report ───


def _result(**overrides: object) -> ReplayResult:
    fields: dict[str, object] = {
        "video_id": "vid",
        "speed": 0.0,
        "wall_ms": 1,
        "events": [],
        "timing": None,
        "done_line": "[pipeline] DONE",
        "saved_result": None,
        "statuses": [],
        "llm_calls_served": 1,
        "llm_misses": [],
        "llm_unused": [],
        "llm_model_mismatches": [],
        "network_attempts": [],
    }
    return ReplayResult(**{**fields, **overrides})  # type: ignore[arg-type]


class TestDivergences:
    def test_should_count_model_mismatch_as_divergence(self) -> None:
        result = _result(llm_model_mismatches=["plan/plan#0: requested a, recorded b"])
        assert divergences(result) == ["model mismatch: plan/plan#0: requested a, recorded b"]

    def test_should_count_unfaked_command_as_divergence(self) -> None:
        assert divergences(_result(unexpected_commands=["yt-dlp --get-url"])) == [
            "unfaked command: yt-dlp --get-url"
        ]

    def test_should_report_nothing_for_faithful_run(self) -> None:
        assert divergences(_result()) == []


class TestCheckSpeed:
    @pytest.mark.parametrize("speed", [0.0, 0.02, 0.05, 1.0])
    def test_should_accept_zero_or_scaled_speed(self, speed: float) -> None:
        assert check_speed(speed) == speed

    @pytest.mark.parametrize("speed", [-1.0, 0.001, 0.019])
    def test_should_reject_speed_below_floor(self, speed: float) -> None:
        with pytest.raises(ValueError, match="speed must be"):
            check_speed(speed)


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


def _call(span: str, start_s: float, end_s: float, feature: str = "") -> dict:
    return {
        "span": span,
        "feature": feature or f"summarize:{span}",
        "_startS": start_s,
        "_endS": end_s,
    }


def _recorded_calls(probe_span: str) -> list[dict]:
    return [
        _call("description_analysis", 1.0, 3.0, "summarize:metadata"),
        _call(probe_span, 40.0, 41.0),
        _call("plan", 41.0, 70.0),
        _call("extraction", 70.0, 140.0, "summarize:extraction"),
        _call("synthesis", 140.0, 145.0),
    ]


class TestPhaseBounds:
    def test_should_end_transcript_frames_at_the_tier_probe_when_the_trace_has_one(self) -> None:
        assert phase_bounds(_recorded_calls("tier_probe"), 160.0)["transcript_frames"] == 40.0

    def test_should_fall_back_to_the_classifier_when_recorded_before_the_probe(self) -> None:
        assert phase_bounds(_recorded_calls("classifier"), 160.0)["transcript_frames"] == 40.0
