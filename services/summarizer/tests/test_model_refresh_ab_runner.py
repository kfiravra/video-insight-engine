"""Unit tests for the model A/B's live-call rows and report round trip (pipeline-1min 4.0d).

A call's row is built from a LiteLLM response (cost, cache tokens, the streamed
usage through the provider's own stream path with the HTTP layer patched);
saved rows decide what a rerun sends again; ``--rescore`` re-reads a saved
report into the same rows. No network, no LLM, no spend.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

import litellm
import pytest

from tests.llm_wire import anthropic_stream, as_stream, offline_http, set_fake_keys, text

_SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

import model_refresh_ab  # noqa: E402
from _model_ab_cost import PlannedCall  # noqa: E402
from _model_ab_runner import (  # noqa: E402
    _result,
    answered,
    arm_request,
    is_answered,
    load_answers,
    request_sha256,
    send,
)
from _model_ab_types import (  # noqa: E402
    GPT_4O_MINI,
    HAIKU_45,
    HAIKU_55,
    AbRequest,
    Arm,
    CallResult,
)


def _request(*, stream: bool = False) -> AbRequest:
    kwargs: dict[str, Any] = {
        "model": HAIKU_45,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 50,
        "timeout": 5.0,
    }
    if stream:
        kwargs |= {"stream": True, "stream_options": {"include_usage": True}}
    return AbRequest("r1", "extraction", "test", "v1", kwargs, 50)


def _response(cache_read: int) -> litellm.ModelResponse:
    usage = litellm.Usage(
        prompt_tokens=10_000,
        completion_tokens=100,
        total_tokens=10_100,
        cache_read_input_tokens=cache_read,
        cache_creation_input_tokens=0,
    )
    choice = {
        "index": 0,
        "finish_reason": "stop",
        "message": {"role": "assistant", "content": "{}"},
    }
    return litellm.ModelResponse(model="claude-haiku-5-5", choices=[choice], usage=usage)


def _write(path: Path, rows: list[CallResult]) -> Path:
    path.write_text("".join(json.dumps(asdict(r)) + "\n" for r in rows), encoding="utf-8")
    return path


class TestResultRow:
    def test_should_price_cache_reads_below_fresh_input_when_the_answer_reports_them(
        self,
    ) -> None:
        call = PlannedCall(_request(), Arm("B", HAIKU_55), 0)
        cached = _result(call, _response(cache_read=8_000), 100, None, "h")
        fresh = _result(call, _response(cache_read=0), 100, None, "h")
        assert (cached.cache_read_tokens, cached.cost_usd < fresh.cost_usd) == (8_000, True)

    async def test_should_keep_the_streamed_usage_when_the_request_streams(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        set_fake_keys(monkeypatch)
        call = PlannedCall(_request(stream=True), Arm("B", HAIKU_55), 0)
        with offline_http(lambda body: as_stream(anthropic_stream([text("{}")], model="m"))):
            row = await send(call)
        assert (row.error, row.input_tokens, row.ttft_ms is not None, row.raw) == (
            None,
            1200,
            True,
            "{}",
        )

    async def test_should_stamp_the_hash_of_the_bytes_sent_when_a_call_is_made(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        set_fake_keys(monkeypatch)
        call = PlannedCall(_request(stream=True), Arm("B", HAIKU_55), 0)
        with offline_http(lambda body: as_stream(anthropic_stream([text("{}")], model="m"))):
            row = await send(call)
        assert row.request_sha256 == request_sha256(arm_request(call))


class TestSavedRows:
    _CALL = PlannedCall(_request(), Arm("A", HAIKU_45), 0)

    def _row(self, **extra: Any) -> CallResult:
        return CallResult("r1", "extraction", "A", HAIKU_45, "low", 0, 10, **extra)

    def test_should_send_again_when_the_saved_row_errored(self, tmp_path: Path) -> None:
        done = answered(_write(tmp_path / "a.jsonl", [self._row(error="Timeout: slow")]))
        assert is_answered(done, self._CALL) is False

    def test_should_skip_the_call_when_it_was_answered_with_todays_bytes(
        self, tmp_path: Path
    ) -> None:
        digest = request_sha256(arm_request(self._CALL))
        done = answered(_write(tmp_path / "a.jsonl", [self._row(request_sha256=digest)]))
        assert is_answered(done, self._CALL) is True

    def test_should_send_again_when_the_request_bytes_changed(self, tmp_path: Path) -> None:
        done = answered(_write(tmp_path / "a.jsonl", [self._row(request_sha256="0" * 64)]))
        assert is_answered(done, self._CALL) is False

    def test_should_keep_the_last_row_when_a_call_was_answered_twice(self, tmp_path: Path) -> None:
        path = _write(tmp_path / "a.jsonl", [self._row(raw="first"), self._row(raw="second")])
        assert [r.raw for r in load_answers(path)] == ["second"]


class TestRescore:
    def test_should_rebuild_the_same_rows_when_a_saved_report_is_rescored(
        self, tmp_path: Path
    ) -> None:
        args = argparse.Namespace(out_json=tmp_path / "ab.json", out_md=tmp_path / "ab.md")
        projection = {"chunk_cl100k": 1, "total_cl100k": 2, "haiku55_tokens": 3}
        meta = {
            "run_at": "2026-10-13T00:00:00+00:00",
            "estimate_usd": 0.1,
            "ceiling_usd": 1.5,
            "arms": {},
            "requests": {"r1": {"context": {}, "pipeline_timeout_s": 20.0}},
            "longest_chunk_projection": projection | {"price_tier_tokens": 4},
        }
        results = [
            CallResult(
                "r1", "faithfulness", "A", GPT_4O_MINI, "low", 0, 100, raw='{"grounded": true}'
            ),
            CallResult("r1", "faithfulness", "B", GPT_4O_MINI, "low", 0, 90, raw='{"grounded": 0}'),
        ]
        model_refresh_ab._finish(args, meta, results)
        first = json.loads(args.out_json.read_text(encoding="utf-8"))
        argv = ["--rescore", "--out-json", str(args.out_json), "--out-md", str(args.out_md)]
        assert model_refresh_ab.main(argv) == 0
        second = json.loads(args.out_json.read_text(encoding="utf-8"))
        assert (second["rows"], second["summary"]) == (first["rows"], first["summary"])
