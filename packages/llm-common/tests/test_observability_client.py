"""Tests for ``llm_common.observability.client`` against the real Langfuse v4 SDK.

The SDK exports to an in-memory OpenTelemetry exporter instead of the network,
so the assertions read the exact span attributes Langfuse would ingest:
- disabled (no keys / no SDK): every helper no-ops, nothing is imported or recorded
- ``run_trace``: root span, propagated trace name/session/user/tags/metadata,
  environment, pinned trace id, failure status, user-id policy
- nesting: ``span`` → ``generation``, across ``create_task`` and ``to_thread``
- ``generation`` / ``finish_generation``: usage, cost, output, ERROR level, prompt versions
- ``score`` and ``update_root_metadata``; SDK errors never escape
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from langfuse._client.resource_manager import LangfuseResourceManager
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from llm_common.observability import client as lc
from llm_common.observability import prompts as lp

# ─── Fixtures ───────────────────────────────────────────────────────────


def _settings(**overrides: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        # A fresh public key per test: the SDK keeps one resource manager per key.
        "LANGFUSE_PUBLIC_KEY": f"pk-lf-test-{uuid4().hex}",
        "LANGFUSE_SECRET_KEY": "sk-lf-test",
        "LANGFUSE_BASE_URL": "http://127.0.0.1:9",
        "ENVIRONMENT": "Development",
        "LANGFUSE_USER_ID_MODE": "identity",
        "LANGFUSE_USER_ID_HASH_SALT": "",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture(autouse=True)
def _reset_state():
    lc._reset_for_tests()
    yield
    lc._reset_for_tests()
    LangfuseResourceManager.reset()


@pytest.fixture
def exporter() -> InMemorySpanExporter:
    exp = InMemorySpanExporter()
    assert lc.init_langfuse(_settings(), span_exporter=exp) is not None
    return exp


def _spans(exp: InMemorySpanExporter) -> dict[str, ReadableSpan]:
    client = lc.get_client()
    assert client is not None
    client.flush()
    return {s.name: s for s in exp.get_finished_spans()}


def _attr(span: ReadableSpan, key: str) -> Any:
    return (span.attributes or {}).get(key)


def _parent_id(span: ReadableSpan) -> int | None:
    return span.parent.span_id if span.parent else None


# ─── Disabled ───────────────────────────────────────────────────────────


def test_init_returns_none_without_keys():
    settings = _settings(LANGFUSE_PUBLIC_KEY=None, LANGFUSE_SECRET_KEY=None)
    assert lc.init_langfuse(settings) is None
    assert lc.is_enabled() is False


def test_init_returns_none_when_sdk_missing(monkeypatch):
    monkeypatch.setattr(lc, "_load_sdk", lambda: None)
    assert lc.init_langfuse(_settings()) is None


def test_init_swallows_sdk_constructor_errors(monkeypatch):
    boom = MagicMock(side_effect=RuntimeError("bad config"))
    monkeypatch.setattr(lc, "_load_sdk", lambda: (boom, MagicMock()))
    assert lc.init_langfuse(_settings()) is None


def test_init_is_idempotent(exporter):
    first = lc.get_client()
    assert lc.init_langfuse(_settings()) is first


def test_init_passes_base_url_and_environment(monkeypatch):
    sdk = MagicMock()
    monkeypatch.setattr(lc, "_load_sdk", lambda: (sdk, MagicMock()))
    lc.init_langfuse(_settings(LANGFUSE_BASE_URL="https://eu.example", ENVIRONMENT="Production"))
    kwargs = sdk.call_args.kwargs
    assert kwargs["base_url"] == "https://eu.example"
    assert kwargs["environment"] == "production"
    assert "host" not in kwargs


def test_init_leaves_out_an_environment_langfuse_would_reject(monkeypatch):
    sdk = MagicMock()
    monkeypatch.setattr(lc, "_load_sdk", lambda: (sdk, MagicMock()))
    lc.init_langfuse(_settings(ENVIRONMENT=""))
    assert "environment" not in sdk.call_args.kwargs


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("Production", "production"), ("dev-2", "dev-2"), ("", None), ("langfuse-x", None)],
)
def test_langfuse_environment(raw, expected):
    assert lc.langfuse_environment(raw) == expected


def test_init_warns_when_hash_mode_has_no_salt(monkeypatch, caplog):
    monkeypatch.setattr(lc, "_load_sdk", lambda: (MagicMock(), MagicMock()))
    with caplog.at_level("WARNING"):
        lc.init_langfuse(_settings(LANGFUSE_USER_ID_MODE="hash"))
    assert "LANGFUSE_USER_ID_HASH_SALT is empty" in caplog.text


@pytest.mark.asyncio
async def test_every_helper_no_ops_when_disabled():
    async with lc.run_trace(name="pipeline:x") as root:
        assert root is None
        with lc.span("phase:a") as s:
            assert s is None
        assert lc.generation("g", model="m", input="hi") is None
        lc.finish_generation(None, output="o")
        lc.score("faithfulness", 1.0)
        lc.update_root_metadata({"k": "v"})
        assert lc.current_trace_id() is None
    await lc.flush_langfuse()
    await lc.shutdown_langfuse()


# ─── run_trace ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_trace_opens_a_root_span_with_propagated_attributes(exporter):
    async with lc.run_trace(
        name="pipeline:vsid1",
        session_id="pipeline-vsid1",
        user_id="user-1",
        tags=["youtubeId:abc", "path:worker"],
        metadata={"youtubeId": "abc", "evalRun": True, "language": None},
        input={"youtubeId": "abc", "videoSummaryId": "vsid1"},
    ) as root:
        assert root is not None
        assert lc.current_trace_id() == root.trace_id
    assert lc.current_trace_id() is None

    root_span = _spans(exporter)["pipeline:vsid1"]
    assert root_span.parent is None
    assert _attr(root_span, "langfuse.trace.name") == "pipeline:vsid1"
    assert _attr(root_span, "session.id") == "pipeline-vsid1"
    assert _attr(root_span, "user.id") == "user-1"
    assert _attr(root_span, "langfuse.trace.tags") == ("youtubeId:abc", "path:worker")
    assert _attr(root_span, "langfuse.trace.metadata.youtubeId") == "abc"
    assert _attr(root_span, "langfuse.trace.metadata.evalRun") == "true"
    assert _attr(root_span, "langfuse.trace.metadata.language") is None
    assert _attr(root_span, "langfuse.environment") == "development"
    assert json.loads(_attr(root_span, "langfuse.observation.input")) == {
        "youtubeId": "abc",
        "videoSummaryId": "vsid1",
    }


@pytest.mark.asyncio
async def test_run_trace_uses_a_pinned_trace_id(exporter):
    pinned = "ab" * 16
    async with lc.run_trace(name="pipeline:x", trace_id=pinned) as root:
        assert root.trace_id == pinned
    assert format(_spans(exporter)["pipeline:x"].context.trace_id, "032x") == pinned


@pytest.mark.asyncio
async def test_run_trace_drops_metadata_values_over_the_propagation_limit(exporter):
    async with lc.run_trace(name="pipeline:x", metadata={"short": "ok", "long": "x" * 201}):
        pass
    root_span = _spans(exporter)["pipeline:x"]
    assert _attr(root_span, "langfuse.trace.metadata.short") == "ok"
    assert _attr(root_span, "langfuse.trace.metadata.long") is None


@pytest.mark.asyncio
async def test_run_trace_marks_the_root_failed_and_reraises(exporter):
    with pytest.raises(ValueError, match="phase blew up"):
        async with lc.run_trace(name="pipeline:x"):
            raise ValueError("phase blew up")
    root_span = _spans(exporter)["pipeline:x"]
    assert root_span.status.status_code.name == "ERROR"
    assert lc.current_trace_id() is None


@pytest.mark.asyncio
async def test_run_trace_yields_none_when_the_sdk_fails(exporter, monkeypatch):
    client = lc.get_client()
    monkeypatch.setattr(
        client, "start_as_current_observation", MagicMock(side_effect=RuntimeError("sdk"))
    )
    async with lc.run_trace(name="pipeline:x") as root:
        assert root is None
        assert lc.current_trace_id() is None


@pytest.mark.asyncio
async def test_user_id_omit_mode_sends_no_user():
    exp = InMemorySpanExporter()
    lc.init_langfuse(_settings(LANGFUSE_USER_ID_MODE="omit"), span_exporter=exp)
    async with lc.run_trace(name="pipeline:x", user_id="user-1"):
        pass
    assert _attr(_spans(exp)["pipeline:x"], "user.id") is None


@pytest.mark.asyncio
async def test_user_id_hash_mode_sends_the_salted_digest():
    exp = InMemorySpanExporter()
    lc.init_langfuse(
        _settings(LANGFUSE_USER_ID_MODE="hash", LANGFUSE_USER_ID_HASH_SALT="pepper"),
        span_exporter=exp,
    )
    async with lc.run_trace(name="pipeline:x", user_id="user-1"):
        pass
    sent = _attr(_spans(exp)["pipeline:x"], "user.id")
    assert sent != "user-1"
    assert len(sent) == 16


# ─── Nesting ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generation_nests_in_a_phase_span_across_tasks_and_threads(exporter):
    def threaded_generation() -> None:
        handle = lc.generation("vision", model="m-fast", input="frame")
        lc.finish_generation(handle, output="ok")

    async def child() -> None:
        with lc.span("phase:plan"):
            handle = lc.generation("plan", model="m", input="hi")
            await asyncio.sleep(0)
            lc.finish_generation(handle, output="out")
            await asyncio.to_thread(threaded_generation)

    async with lc.run_trace(name="pipeline:x", session_id="pipeline-x"):
        await asyncio.create_task(child())

    spans = _spans(exporter)
    root, phase = spans["pipeline:x"], spans["phase:plan"]
    assert _parent_id(phase) == root.context.span_id
    assert _parent_id(spans["plan"]) == phase.context.span_id
    assert _parent_id(spans["vision"]) == phase.context.span_id
    assert {s.context.trace_id for s in spans.values()} == {root.context.trace_id}
    # Propagated attributes reach every child (session cost needs them on generations).
    assert _attr(spans["plan"], "session.id") == "pipeline-x"
    assert _attr(spans["plan"], "langfuse.trace.name") == "pipeline:x"


@pytest.mark.asyncio
async def test_span_works_with_async_with_and_records_failures(exporter):
    async with lc.run_trace(name="pipeline:x"):
        with pytest.raises(KeyError):
            async with lc.span("tool:quiz", as_type="tool", metadata={"k": "v"}) as handle:
                assert handle is not None
                raise KeyError("missing")
    tool = _spans(exporter)["tool:quiz"]
    assert _attr(tool, "langfuse.observation.type") == "tool"
    assert _attr(tool, "langfuse.observation.metadata.k") == "v"
    assert tool.status.status_code.name == "ERROR"


@pytest.mark.asyncio
async def test_span_and_generation_record_nothing_outside_a_trace(exporter):
    with lc.span("phase:orphan") as handle:
        assert handle is None
    assert lc.generation("orphan", model="m") is None
    assert "phase:orphan" not in _spans(exporter)


@pytest.mark.asyncio
async def test_work_spawned_in_the_trace_still_lands_in_it_after_the_root_ended(exporter):
    gate = asyncio.Event()

    async def judge() -> None:
        await gate.wait()
        lc.finish_generation(lc.generation("judge", model="m"), output="1")

    async with lc.run_trace(name="pipeline:x"):
        task = asyncio.create_task(judge())
    gate.set()
    await task
    spans = _spans(exporter)
    assert spans["judge"].context.trace_id == spans["pipeline:x"].context.trace_id


# ─── Generations ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_finish_generation_records_usage_cost_output_and_metadata(exporter):
    async with lc.run_trace(name="pipeline:x"):
        handle = lc.generation(
            "extraction",
            model="anthropic/claude",
            input=[{"role": "user", "content": "mail me at a@b.co"}],
            model_parameters={"temperature": 0.2},
            metadata={"attempt": 1},
        )
        lc.finish_generation(
            handle,
            output="answer",
            usage=lc.usage_details(100, 20, cache_read_tokens=80),
            cost_usd=0.0042,
            metadata={"finishReason": "stop", "latencyMs": 12},
        )
    gen = _spans(exporter)["extraction"]
    assert _attr(gen, "langfuse.observation.type") == "generation"
    assert _attr(gen, "langfuse.observation.model.name") == "anthropic/claude"
    assert json.loads(_attr(gen, "langfuse.observation.usage_details")) == {
        "input": 100,
        "output": 20,
        "total": 120,
        "cache_read_input_tokens": 80,
    }
    assert json.loads(_attr(gen, "langfuse.observation.cost_details")) == {"total": 0.0042}
    assert _attr(gen, "langfuse.observation.output") == "answer"
    assert "a@b.co" not in _attr(gen, "langfuse.observation.input")
    assert json.loads(_attr(gen, "langfuse.observation.model.parameters")) == {"temperature": 0.2}
    assert _attr(gen, "langfuse.observation.metadata.attempt") == 1
    assert _attr(gen, "langfuse.observation.metadata.finishReason") == "stop"
    assert gen.end_time >= gen.start_time


@pytest.mark.asyncio
async def test_finish_generation_marks_a_failed_call(exporter):
    async with lc.run_trace(name="pipeline:x"):
        handle = lc.generation("plan", model="m")
        lc.finish_generation(
            handle, level="ERROR", status_message=lc.error_status(TimeoutError("slow"))
        )
    gen = _spans(exporter)["plan"]
    assert _attr(gen, "langfuse.observation.level") == "ERROR"
    assert _attr(gen, "langfuse.observation.status_message") == "TimeoutError: slow"


@pytest.mark.asyncio
async def test_generation_carries_the_active_prompt_versions(exporter):
    async with lc.run_trace(name="pipeline:x"):
        lc.record_active_prompt("summarizer:plan", "7")
        lc.finish_generation(lc.generation("plan", model="m"))
    meta = _attr(_spans(exporter)["plan"], "langfuse.observation.metadata.promptVersions")
    assert json.loads(meta) == {"summarizer:plan": "7"}


@pytest.mark.asyncio
async def test_generation_and_finish_swallow_sdk_errors(exporter, monkeypatch):
    client = lc.get_client()
    monkeypatch.setattr(client, "start_observation", MagicMock(side_effect=RuntimeError("sdk")))
    async with lc.run_trace(name="pipeline:x"):
        assert lc.generation("plan", model="m") is None
    broken = MagicMock()
    broken.update.side_effect = RuntimeError("update")
    broken.end.side_effect = RuntimeError("end")
    lc.finish_generation(broken, output="o")  # must not raise
    broken.end.assert_called_once()


def test_usage_details_leaves_out_zero_cache_keys():
    assert lc.usage_details(3, 4) == {"input": 3, "output": 4, "total": 7}
    assert lc.usage_details(3, 4, cache_creation_tokens=2)["cache_creation_input_tokens"] == 2


# ─── Scores and root metadata ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_score_defaults_to_the_current_trace(exporter, monkeypatch):
    create_score = MagicMock()
    monkeypatch.setattr(lc.get_client(), "create_score", create_score)
    async with lc.run_trace(name="pipeline:x") as root:
        lc.score("faithfulness", 0.75, comment="3/4 grounded")
    create_score.assert_called_once_with(
        trace_id=root.trace_id, name="faithfulness", value=0.75, comment="3/4 grounded"
    )


@pytest.mark.asyncio
async def test_score_targets_an_explicit_trace_and_swallows_errors(exporter, monkeypatch):
    create_score = MagicMock(side_effect=RuntimeError("ingestion down"))
    monkeypatch.setattr(lc.get_client(), "create_score", create_score)
    lc.score("faithfulness", 1, trace_id="cd" * 16)  # must not raise
    assert create_score.call_args.kwargs["trace_id"] == "cd" * 16
    create_score.reset_mock()
    lc.score("faithfulness", 1)  # no trace → nothing sent
    create_score.assert_not_called()


@pytest.mark.asyncio
async def test_update_root_metadata_lands_on_the_root_span(exporter):
    async with lc.run_trace(name="pipeline:x"):
        with lc.span("phase:transcript"):
            lc.update_root_metadata({"transcriptSource": "captions", "timing": {"wallMs": 5}})
    root_span = _spans(exporter)["pipeline:x"]
    assert _attr(root_span, "langfuse.observation.metadata.transcriptSource") == "captions"
    assert json.loads(_attr(root_span, "langfuse.observation.metadata.timing")) == {"wallMs": 5}
    assert (
        _attr(_spans(exporter)["phase:transcript"], "langfuse.observation.metadata.timing") is None
    )


# ─── Prompts through the client ─────────────────────────────────────────


def test_fetch_prompt_with_obj_no_ops_when_disabled():
    assert lc.fetch_prompt_with_obj("summarizer:plan") is None


def test_fetch_prompt_with_obj_asks_the_sdk(exporter, monkeypatch):
    found = MagicMock(prompt="TEXT", version=3)
    monkeypatch.setattr(lc.get_client(), "get_prompt", MagicMock(return_value=found))
    assert lc.fetch_prompt_with_obj("summarizer:plan") is found
    assert lp._prompt_misses == {}


@pytest.mark.asyncio
async def test_flush_and_shutdown_swallow_sdk_errors(exporter, monkeypatch):
    client = lc.get_client()
    monkeypatch.setattr(client, "flush", MagicMock(side_effect=RuntimeError("net")))
    monkeypatch.setattr(client, "shutdown", MagicMock(side_effect=RuntimeError("net")))
    await lc.flush_langfuse()
    await lc.shutdown_langfuse()
    assert lc.is_enabled() is False
