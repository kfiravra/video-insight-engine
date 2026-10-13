"""``EXTRACTION_PARALLEL=False`` sends TODAY's single extraction request, byte for byte (3.4, §4).

Kfir's phase-3 rule: the false path is the rollback lever, so its one
extraction call must stay what 41501dd sent. Each cassette replays with the
flag off; the request the fake receives is canonicalised exactly as the
baseline capture did it (``canonical_request``, copied verbatim from
``<scratch>/baseline/capture.py``) and its sha256 compared with the committed
fixture (``baselines/<video>.extraction.json`` = the capture's extraction line).

The premise is asserted first: reconcile at plan-done is a no-op on these
recordings (no plan-done record, the plan-parse tabs unchanged), so a future
cassette that triggers it fails loudly here instead of confounding the hash.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from llm_common.context import llm_feature_var

from src.models.pipeline_types import PlanResult
from src.services.pipeline.plan import _normalize_plan_data
from src.utils.json_parsing import parse_json_response
from tests.replay import fake_llm
from tests.replay.cassette import Cassette, available_cassettes
from tests.replay.driver import ReplayResult, run_replay

_BASELINES = Path(__file__).parent / "baselines"
_EXTRACTION = ("summarize:extraction", "extraction")
_PLAN_DONE = "plan_done"


def canonical_request(kwargs: dict[str, Any]) -> str:
    """Byte-exact form of one ``acompletion(**kwargs)`` request.

    Every kwarg the provider sends is kept (model, messages after
    ``prepare_for_model`` incl. content-block lists and ``cache_control``,
    max_tokens, temperature, response_format, timeout, num_retries, and
    metadata when a caller sets it): none varied across process restarts,
    hash seeds, in-process repeats or replay speed, so nothing is dropped.
    Non-JSON values raise instead of being stringified.
    """
    return json.dumps(kwargs, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


@dataclass(frozen=True)
class _Request:
    feature: str
    span: str | None
    canonical: str

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.canonical.encode("utf-8")).hexdigest()


@contextmanager
def _recording(requests: list[_Request]) -> Iterator[None]:
    """Canonicalise every request before the fake answers it (the capture's seam)."""
    original = fake_llm.ReplayLLM.acompletion

    async def recording(self: fake_llm.ReplayLLM, **kwargs: Any) -> Any:
        span = fake_llm._span_var.get()
        requests.append(_Request(llm_feature_var.get(), span, canonical_request(kwargs)))
        return await original(self, **kwargs)

    with patch.object(fake_llm.ReplayLLM, "acompletion", recording):
        yield


@dataclass(frozen=True)
class _FalsePathRun:
    result: ReplayResult
    requests: list[_Request]

    def extraction_requests(self) -> list[_Request]:
        return [r for r in self.requests if (r.feature, r.span) == _EXTRACTION]

    def group_requests(self) -> list[_Request]:
        return [r for r in self.requests if r.feature.startswith("summarize:extraction:")]


@pytest.fixture(scope="module")
def false_path(cassettes: dict[str, Cassette]) -> dict[str, _FalsePathRun]:
    runs: dict[str, _FalsePathRun] = {}
    for video_id, cassette in cassettes.items():
        requests: list[_Request] = []
        with _recording(requests):
            result = asyncio.run(
                run_replay(cassette, speed=0, settings_overrides={"EXTRACTION_PARALLEL": False})
            )
        runs[video_id] = _FalsePathRun(result, requests)
    return runs


def _baseline(video_id: str) -> dict[str, Any]:
    return json.loads((_BASELINES / f"{video_id}.extraction.json").read_text(encoding="utf-8"))


def _plan_parse_tabs(cassette: Cassette, content_format: str | None) -> list[dict]:
    """The recorded plan through today's parse (registry check + plan-parse guard)."""
    output = next(e.output for e in cassette.llm if e.key.span == "plan")
    data = parse_json_response(output)
    _normalize_plan_data(data, content_format)
    return PlanResult.model_validate(data).tabs


def _pipeline(run: _FalsePathRun) -> dict[str, Any]:
    return (run.result.saved_result or {}).get("pipeline", {})


@pytest.mark.parametrize("video_id", available_cassettes())
class TestFalsePathExtractionIdentity:
    def test_should_record_no_plan_done_reconcile_action(
        self, false_path: dict[str, _FalsePathRun], video_id: str
    ) -> None:
        records = _pipeline(false_path[video_id]).get("reconcile", [])

        assert [r for r in records if r.get("stage") == _PLAN_DONE] == []

    def test_should_keep_the_plan_parse_tabs_after_reconcile(
        self, false_path: dict[str, _FalsePathRun], cassettes: dict[str, Cassette], video_id: str
    ) -> None:
        triage = _pipeline(false_path[video_id])["triage"]

        assert triage["tabs"] == _plan_parse_tabs(cassettes[video_id], triage["contentFormat"])

    def test_should_send_exactly_one_extraction_call(
        self, false_path: dict[str, _FalsePathRun], video_id: str
    ) -> None:
        run = false_path[video_id]

        assert (len(run.extraction_requests()), run.group_requests()) == (1, [])

    def test_should_keep_the_baseline_fixture_self_consistent(self, video_id: str) -> None:
        baseline = _baseline(video_id)
        canonical = canonical_request(baseline["request"])

        assert hashlib.sha256(canonical.encode("utf-8")).hexdigest() == baseline["sha256"]

    def test_should_send_todays_exact_extraction_request_when_extraction_parallel_is_false(
        self, false_path: dict[str, _FalsePathRun], video_id: str
    ) -> None:
        sent = false_path[video_id].extraction_requests()[0]

        assert sent.sha256 == _baseline(video_id)["sha256"]
