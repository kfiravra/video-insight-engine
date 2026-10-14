"""``EXTRACTION_PARALLEL=False`` sends TODAY's single extraction prompt, byte for byte (3.4, §4).

Kfir's phase-3 rule: the false path is the rollback lever, so its one
extraction call must stay what 41501dd sent. Each cassette replays with the
flag off and the prompt the fake receives — its messages, every content block
and ``cache_control`` breakpoint, the system prompt included, and its
``response_format`` (``canonical_prompt``) — is compared with the committed fixture's
(``baselines/<video>.extraction.json`` = the baseline capture's extraction
line). Since 4.0b the comparison keys on those prompt bytes only: a model
change (4.0) or the model-keyed params (thinking, effort, sampling,
max_tokens) must not break it. The fixture itself is still checked against
its full-request hash (``canonical_request``, copied verbatim from
``<scratch>/baseline/capture.py``).

The premise is asserted first: reconcile at plan-done is a no-op on these
recordings (no plan-done record, the plan-parse tabs unchanged), so a future
cassette that triggers it fails loudly here instead of confounding the hash.

Every request of both paths also ends with a user turn: no assistant prefill
(the 5.x models reject it). The last role is read at the guard itself
(``llm_model_rules.refuse_prefill``), which raises before the fake would see a
prefilled request — and a stage may swallow that error.
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
from src.services import llm_model_rules
from src.services.llm_model_rules import AssistantPrefillError
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


_PROMPT_KEYS = ("messages", "response_format")


def canonical_prompt(kwargs: dict[str, Any]) -> str:
    """Byte-exact form of one request's prompt, as ``canonical_request`` writes it.

    Its messages (the system prompt first; content-block lists and their
    ``cache_control`` kept) and its ``response_format`` — neither depends on
    the model. Model, sampling, thinking, effort, max_tokens and timeout are
    not part of it.
    """
    return canonical_request({key: kwargs[key] for key in _PROMPT_KEYS if key in kwargs})


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class _Request:
    feature: str
    span: str | None
    prompt: str

    @property
    def prompt_sha256(self) -> str:
        return _sha256(self.prompt)


@contextmanager
def _recording(requests: list[_Request], last_roles: list[str | None]) -> Iterator[None]:
    """Canonicalise every request before the fake answers it (the capture's seam), and
    note the last message's role of every request at the prefill guard."""
    original = fake_llm.ReplayLLM.acompletion
    guard = llm_model_rules.refuse_prefill

    async def recording(self: fake_llm.ReplayLLM, **kwargs: Any) -> Any:
        span = fake_llm._span_var.get()
        requests.append(_Request(llm_feature_var.get(), span, canonical_prompt(kwargs)))
        return await original(self, **kwargs)

    def guarded(messages: list[dict[str, Any]]) -> None:
        last_roles.append(messages[-1].get("role") if messages else None)
        guard(messages)

    with (
        patch.object(fake_llm.ReplayLLM, "acompletion", recording),
        patch.object(llm_model_rules, "refuse_prefill", guarded),
    ):
        yield


@dataclass(frozen=True)
class _RecordedRun:
    result: ReplayResult
    requests: list[_Request]
    last_roles: list[str | None]

    def extraction_requests(self) -> list[_Request]:
        return [r for r in self.requests if (r.feature, r.span) == _EXTRACTION]

    def group_requests(self) -> list[_Request]:
        return [r for r in self.requests if r.feature.startswith("summarize:extraction:")]

    def guard_check(self) -> tuple[int, int, int]:
        """(requests sent, requests the prefill guard saw, prefilled ones among them)."""
        return len(self.requests), len(self.last_roles), self.last_roles.count("assistant")


def _record(cassettes: dict[str, Cassette], *, parallel: bool) -> dict[str, _RecordedRun]:
    runs: dict[str, _RecordedRun] = {}
    for video_id, cassette in cassettes.items():
        requests: list[_Request] = []
        last_roles: list[str | None] = []
        overrides = {"EXTRACTION_PARALLEL": parallel}
        with _recording(requests, last_roles):
            result = asyncio.run(run_replay(cassette, speed=0, settings_overrides=overrides))
        runs[video_id] = _RecordedRun(result, requests, last_roles)
    return runs


@pytest.fixture(scope="module")
def false_path(cassettes: dict[str, Cassette]) -> dict[str, _RecordedRun]:
    return _record(cassettes, parallel=False)


@pytest.fixture(scope="module")
def split_path(cassettes: dict[str, Cassette]) -> dict[str, _RecordedRun]:
    return _record(cassettes, parallel=True)


def _baseline(video_id: str) -> dict[str, Any]:
    return json.loads((_BASELINES / f"{video_id}.extraction.json").read_text(encoding="utf-8"))


def _plan_parse_tabs(cassette: Cassette, content_format: str | None) -> list[dict]:
    """The recorded plan through today's parse (registry check + plan-parse guard)."""
    output = next(e.output for e in cassette.llm if e.key.span == "plan")
    data = parse_json_response(output)
    _normalize_plan_data(data, content_format)
    return PlanResult.model_validate(data).tabs


def _pipeline(run: _RecordedRun) -> dict[str, Any]:
    return (run.result.saved_result or {}).get("pipeline", {})


@pytest.mark.parametrize("video_id", available_cassettes())
class TestFalsePathExtractionIdentity:
    def test_should_record_no_plan_done_reconcile_action(
        self, false_path: dict[str, _RecordedRun], video_id: str
    ) -> None:
        records = _pipeline(false_path[video_id]).get("reconcile", [])

        assert [r for r in records if r.get("stage") == _PLAN_DONE] == []

    def test_should_keep_the_plan_parse_tabs_after_reconcile(
        self, false_path: dict[str, _RecordedRun], cassettes: dict[str, Cassette], video_id: str
    ) -> None:
        triage = _pipeline(false_path[video_id])["triage"]

        assert triage["tabs"] == _plan_parse_tabs(cassettes[video_id], triage["contentFormat"])

    def test_should_send_exactly_one_extraction_call(
        self, false_path: dict[str, _RecordedRun], video_id: str
    ) -> None:
        run = false_path[video_id]

        assert (len(run.extraction_requests()), run.group_requests()) == (1, [])

    def test_should_keep_the_baseline_fixture_self_consistent(self, video_id: str) -> None:
        baseline = _baseline(video_id)
        canonical = canonical_request(baseline["request"])

        assert hashlib.sha256(canonical.encode("utf-8")).hexdigest() == baseline["sha256"]

    def test_should_send_todays_exact_extraction_prompt_when_extraction_parallel_is_false(
        self, false_path: dict[str, _RecordedRun], video_id: str
    ) -> None:
        sent = false_path[video_id].extraction_requests()[0]

        assert sent.prompt_sha256 == _sha256(canonical_prompt(_baseline(video_id)["request"]))


@pytest.mark.parametrize("video_id", available_cassettes())
class TestNoAssistantPrefill:
    def test_should_end_every_request_with_a_user_turn_when_extraction_parallel_is_false(
        self, false_path: dict[str, _RecordedRun], video_id: str
    ) -> None:
        sent, guarded, prefilled = false_path[video_id].guard_check()

        assert (sent > 0, guarded, prefilled) == (True, sent, 0)

    def test_should_end_every_request_with_a_user_turn_when_extraction_is_split(
        self, split_path: dict[str, _RecordedRun], video_id: str
    ) -> None:
        run = split_path[video_id]
        sent, guarded, prefilled = run.guard_check()

        assert (run.group_requests() != [], guarded, prefilled) == (True, sent, 0)


class TestPrefillGuardRecording:
    def test_should_see_a_prefilled_request_when_the_guard_refuses_it(self) -> None:
        last_roles: list[str | None] = []
        prefilled = [{"role": "user", "content": "q"}, {"role": "assistant", "content": "{"}]

        with _recording([], last_roles), pytest.raises(AssistantPrefillError):
            llm_model_rules.request_for_model(
                {"model": "anthropic/claude-haiku-4-5-20251001", "messages": prefilled},
                temperature=None,
                effort="low",
            )

        assert last_roles == ["assistant"]


class TestCanonicalPrompt:
    def test_should_ignore_model_and_request_params_when_the_messages_match(self) -> None:
        messages = [
            {"role": "system", "content": "rules"},
            {"role": "user", "content": [{"type": "text", "text": "a", "cache_control": {}}]},
        ]
        today = {"model": "anthropic/claude-haiku-4-5-20251001", "max_tokens": 16384}
        refreshed = {
            "model": "anthropic/claude-haiku-5-5",
            "thinking": {"type": "disabled"},
            "output_config": {"effort": "low"},
            "temperature": 0.0,
        }

        assert canonical_prompt({**today, "messages": messages}) == canonical_prompt(
            {**refreshed, "messages": messages}
        )

    def test_should_change_when_the_response_format_changes(self) -> None:
        messages = [{"role": "user", "content": "q"}]
        json_mode = {"messages": messages, "response_format": {"type": "json_object"}}

        assert canonical_prompt(json_mode) != canonical_prompt({"messages": messages})

    def test_should_change_when_a_cache_breakpoint_moves(self) -> None:
        def prompt(cached: bool) -> dict[str, Any]:
            block: dict[str, Any] = {"type": "text", "text": "a"}
            if cached:
                block["cache_control"] = {"type": "ephemeral"}
            return {"messages": [{"role": "user", "content": [block]}]}

        assert canonical_prompt(prompt(True)) != canonical_prompt(prompt(False))
