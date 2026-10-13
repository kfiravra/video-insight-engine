"""Tests for src.services.observability.langfuse_client.

Covers:
- init returns None when keys are missing (no-op contract)
- init returns a client when keys are set, with idempotency
- pipeline_trace yields None when disabled and a trace handle when enabled
- log_generation / log_score no-op when no current trace is set
- redact_pii removes emails and long digit-runs
- truncate_payload caps oversized strings and reports original size
- exceptions inside Langfuse calls never bubble up to callers
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.services.observability import langfuse_client as lc


@pytest.fixture(autouse=True)
def _reset_module_state(monkeypatch):
    """Each test starts with a fresh module state and disabled SDK."""
    lc._reset_for_tests()
    monkeypatch.setattr(lc.settings, "LANGFUSE_PUBLIC_KEY", None, raising=False)
    monkeypatch.setattr(lc.settings, "LANGFUSE_SECRET_KEY", None, raising=False)
    yield
    lc._reset_for_tests()


def _install_fake_sdk(monkeypatch) -> MagicMock:
    """Wire a MagicMock SDK class into langfuse_client and return it."""
    fake_sdk_cls = MagicMock(name="LangfuseSDK")
    monkeypatch.setattr(lc, "Langfuse", fake_sdk_cls)
    monkeypatch.setattr(lc.settings, "LANGFUSE_PUBLIC_KEY", "pk_test", raising=False)
    monkeypatch.setattr(lc.settings, "LANGFUSE_SECRET_KEY", "sk_test", raising=False)
    return fake_sdk_cls


def test_init_returns_none_when_keys_missing():
    """No keys, no client. The pipeline must run without observability."""
    assert lc.init_langfuse() is None
    assert lc.is_enabled() is False


def test_init_returns_none_when_sdk_missing(monkeypatch):
    """SDK absent (e.g., trimmed image) still produces a clean no-op."""
    monkeypatch.setattr(lc, "Langfuse", None)
    monkeypatch.setattr(lc.settings, "LANGFUSE_PUBLIC_KEY", "pk", raising=False)
    monkeypatch.setattr(lc.settings, "LANGFUSE_SECRET_KEY", "sk", raising=False)
    assert lc.init_langfuse() is None


def test_init_builds_client_when_keys_present(monkeypatch):
    """Keys + SDK present produces a configured client."""
    fake_sdk_cls = _install_fake_sdk(monkeypatch)
    client = lc.init_langfuse()
    assert client is not None
    assert lc.is_enabled() is True
    fake_sdk_cls.assert_called_once()


def test_init_is_idempotent(monkeypatch):
    """Calling init twice returns the same instance."""
    fake_sdk_cls = _install_fake_sdk(monkeypatch)
    first = lc.init_langfuse()
    second = lc.init_langfuse()
    assert first is second
    fake_sdk_cls.assert_called_once()


@pytest.mark.asyncio
async def test_pipeline_trace_yields_none_when_disabled():
    """Disabled client → context yields None, no error."""
    async with lc.pipeline_trace("vid-123") as trace:
        assert trace is None


@pytest.mark.asyncio
async def test_pipeline_trace_opens_and_resets_context(monkeypatch):
    """Trace handle is bound to ContextVar while inside the block."""
    _install_fake_sdk(monkeypatch)
    lc.init_langfuse()
    sdk_instance = lc._client
    fake_trace = MagicMock(name="trace")
    sdk_instance.trace.return_value = fake_trace

    assert lc.get_current_trace() is None
    async with lc.pipeline_trace("vid-123", tags=["t:1"], metadata={"k": "v"}) as trace:
        assert trace is fake_trace
        assert lc.get_current_trace() is fake_trace
    assert lc.get_current_trace() is None
    # Flush is always attempted on exit
    sdk_instance.flush.assert_called()


@pytest.mark.asyncio
async def test_pipeline_trace_swallows_trace_creation_errors(monkeypatch):
    """A failing trace() call yields None instead of crashing the pipeline."""
    _install_fake_sdk(monkeypatch)
    lc.init_langfuse()
    lc._client.trace.side_effect = RuntimeError("network down")
    async with lc.pipeline_trace("vid-123") as trace:
        assert trace is None


def test_log_generation_no_op_when_no_trace():
    """No current trace → log_generation is a silent no-op."""
    lc.log_generation(
        name="extraction",
        model="m",
        input_payload="hi",
        output_payload="out",
    )  # must not raise


@pytest.mark.asyncio
async def test_log_generation_records_under_current_trace(monkeypatch):
    """Inside a trace block, generation() is forwarded with normalized payload."""
    _install_fake_sdk(monkeypatch)
    lc.init_langfuse()
    fake_trace = MagicMock()
    lc._client.trace.return_value = fake_trace

    async with lc.pipeline_trace("vid"):
        lc.log_generation(
            name="extraction",
            model="anthropic/claude-sonnet-4-6",
            input_payload="hello world",
            output_payload="response text",
            input_tokens=10,
            output_tokens=5,
            cost_usd=0.001,
            latency_ms=120,
            metadata={"attempt": 1},
        )

    fake_trace.generation.assert_called_once()
    kwargs = fake_trace.generation.call_args.kwargs
    assert kwargs["name"] == "extraction"
    assert kwargs["model"] == "anthropic/claude-sonnet-4-6"
    assert kwargs["usage"]["input"] == 10
    assert kwargs["usage"]["output"] == 5
    assert kwargs["usage"]["totalCost"] == 0.001
    assert kwargs["metadata"]["latencyMs"] == 120
    assert kwargs["metadata"]["attempt"] == 1


def test_log_score_no_op_when_no_trace():
    """No current trace → log_score is a silent no-op."""
    lc.log_score("faithfulness", 0.9)


@pytest.mark.asyncio
async def test_log_score_attaches_to_current_trace(monkeypatch):
    _install_fake_sdk(monkeypatch)
    lc.init_langfuse()
    fake_trace = MagicMock()
    lc._client.trace.return_value = fake_trace

    async with lc.pipeline_trace("vid"):
        lc.log_score("faithfulness", 0.75, comment="sampled 20%")

    fake_trace.score.assert_called_once_with(
        name="faithfulness",
        value=0.75,
        comment="sampled 20%",
    )


def test_redact_pii_strips_emails():
    text = "Contact me at alice@example.com or bob+spam@test.io please"
    out = lc.redact_pii(text)
    assert "alice@example.com" not in out
    assert "bob+spam@test.io" not in out
    assert "[REDACTED]" in out


def test_redact_pii_strips_phone_like_runs():
    text = "Call +1 (555) 123-4567 anytime"
    out = lc.redact_pii(text)
    assert "555" not in out
    assert "[REDACTED]" in out


def test_redact_pii_handles_none():
    assert lc.redact_pii(None) == ""


def test_truncate_payload_passthrough_when_small():
    assert lc.truncate_payload("hello", max_bytes=100) == "hello"


def test_truncate_payload_caps_large_strings():
    big = "x" * 1000
    out = lc.truncate_payload(big, max_bytes=200)
    assert out.startswith("x" * 200)
    assert out.endswith("[TRUNCATED]")
    # The original length must NOT be encoded — that's a sizing oracle.
    assert "1000" not in out


def test_truncate_payload_handles_none():
    assert lc.truncate_payload(None) == ""


@pytest.mark.asyncio
async def test_log_generation_swallows_sdk_errors(monkeypatch):
    """SDK explosion during generation() must not bubble up."""
    _install_fake_sdk(monkeypatch)
    lc.init_langfuse()
    fake_trace = MagicMock()
    fake_trace.generation.side_effect = RuntimeError("sdk boom")
    lc._client.trace.return_value = fake_trace

    async with lc.pipeline_trace("vid"):
        lc.log_generation(
            name="extraction",
            model="m",
            input_payload="i",
            output_payload="o",
        )  # must not raise


def test_redact_pii_strips_jwt():
    jwt = (
        "eyJhbGciOiJIUzI1NiJ9"
        ".eyJzdWIiOiIxMjM0NTY3ODkwIn0"
        ".SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    )
    assert jwt not in lc.redact_pii(f"token={jwt} more text")


def test_redact_pii_strips_aws_keys():
    text = "key AKIAIOSFODNN7EXAMPLE seen"
    assert "AKIAIOSFODNN7EXAMPLE" not in lc.redact_pii(text)


def test_redact_pii_strips_anthropic_key():
    text = "sk-ant-api03-xxxxxxxxxxxxxxxxxxxxabcdef in the logs"
    out = lc.redact_pii(text)
    assert "sk-ant-api03" not in out
    # The "sk-ant-" prefix must be redacted as a single unit so the
    # generic sk- pattern can't re-leak it.
    assert "sk-ant" not in out


def test_redact_pii_strips_openai_key():
    text = "OPENAI=sk-proj-aaaaaaaaaaaaaaaaaaaaaaaaaaa visible"
    assert "sk-proj-" not in lc.redact_pii(text)


def test_redact_pii_strips_github_token():
    text = "GH ghp_abcdefghijklmnopqrstuvwxyzABCD1234 leaked"
    assert "ghp_abcdefghijklmnopqrstuvwxyzABCD1234" not in lc.redact_pii(text)


def test_redact_pii_strips_bearer_header():
    text = "Authorization: Bearer my.long.token.abc"
    out = lc.redact_pii(text)
    assert "my.long.token.abc" not in out


def test_redact_pii_strips_google_api_key():
    text = "GMAPS_KEY=AIzaSyA-1234567890abcdefghijklmnopqrstuvw extra"
    assert "AIzaSyA-1234567890abcdefghijklmnopqrstuvw" not in lc.redact_pii(text)


def test_redact_pii_strips_slack_token():
    text = "SLACK=xoxb-1234567890abcdefghij more text"
    assert "xoxb-1234567890abcdefghij" not in lc.redact_pii(text)


def test_redact_pii_strips_stripe_live_key():
    text = "STRIPE=sk_live_abcdef1234567890ABCDEF and more"
    assert "sk_live_abcdef1234567890ABCDEF" not in lc.redact_pii(text)


def test_redact_pii_strips_pem_private_key():
    pem = (
        "before\n"
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEpQIBAAKCAQEAxYz1Z\n"
        "secret_body_here\n"
        "-----END RSA PRIVATE KEY-----\n"
        "after"
    )
    out = lc.redact_pii(pem)
    assert "MIIEpQIBAAKCAQEAxYz1Z" not in out
    assert "BEGIN RSA PRIVATE KEY" not in out
    assert "before" in out and "after" in out


def test_init_warns_when_hash_mode_with_empty_salt(monkeypatch, caplog):
    _install_fake_sdk(monkeypatch)
    monkeypatch.setattr(lc.settings, "LANGFUSE_USER_ID_MODE", "hash", raising=False)
    monkeypatch.setattr(lc.settings, "LANGFUSE_USER_ID_HASH_SALT", "", raising=False)
    import logging

    with caplog.at_level(logging.WARNING):
        lc.init_langfuse()
    assert any("HASH_SALT is empty" in r.message for r in caplog.records)


def test_init_does_not_warn_when_hash_mode_with_salt(monkeypatch, caplog):
    _install_fake_sdk(monkeypatch)
    monkeypatch.setattr(lc.settings, "LANGFUSE_USER_ID_MODE", "hash", raising=False)
    monkeypatch.setattr(lc.settings, "LANGFUSE_USER_ID_HASH_SALT", "p3pper", raising=False)
    import logging

    with caplog.at_level(logging.WARNING):
        lc.init_langfuse()
    assert not any("HASH_SALT is empty" in r.message for r in caplog.records)


# ─── Structured-content redaction (frame-vision OCR fix) ─────────────────
@pytest.mark.asyncio
async def test_structured_content_blocks_are_redacted(monkeypatch):
    """OCR text inside Anthropic-style content blocks must be redacted."""
    _install_fake_sdk(monkeypatch)
    lc.init_langfuse()
    fake_trace = MagicMock()
    lc._client.trace.return_value = fake_trace

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Slide says alice@example.com"},
                {"type": "image", "source": {"type": "base64", "data": "ignored"}},
            ],
        },
    ]
    async with lc.pipeline_trace("vid"):
        lc.log_generation(
            name="frame_vision",
            model="m",
            input_payload=messages,
            output_payload="",
        )
    sent = fake_trace.generation.call_args.kwargs["input"]
    text_block = sent[0]["content"][0]
    assert "alice@example.com" not in text_block["text"]
    assert "[REDACTED]" in text_block["text"]
    # Non-text blocks (images) must be preserved structurally
    assert sent[0]["content"][1]["type"] == "image"


# ─── Prompt-version recording on the active trace ───────────────────────
@pytest.mark.asyncio
async def test_record_active_prompt_populates_metadata(monkeypatch):
    """Explicit record_active_prompt + log_generation pipeline."""
    _install_fake_sdk(monkeypatch)
    lc.init_langfuse()
    fake_trace = MagicMock()
    lc._client.trace.return_value = fake_trace
    obj = MagicMock()
    obj.prompt = "PROMPT_BODY"
    obj.version = 42
    lc._client.get_prompt.return_value = obj

    async with lc.pipeline_trace("vid"):
        prompt_obj = lc.fetch_prompt_with_obj("summarizer:plan")
        assert prompt_obj is obj
        lc.record_active_prompt("summarizer:plan", prompt_obj)
        assert lc.get_active_prompts() == {"summarizer:plan": "42"}
        lc.log_generation(
            name="plan",
            model="m",
            input_payload="i",
            output_payload="o",
        )
    md = fake_trace.generation.call_args.kwargs["metadata"]
    assert md["promptVersions"] == {"summarizer:plan": "42"}
    # Native linkage: the Prompt object was passed via prompt= kwarg.
    assert fake_trace.generation.call_args.kwargs.get("prompt") is obj


@pytest.mark.asyncio
async def test_record_active_prompt_no_op_outside_trace(monkeypatch):
    """Without a trace open, record_active_prompt is a silent no-op."""
    _install_fake_sdk(monkeypatch)
    lc.init_langfuse()
    obj = MagicMock()
    obj.prompt = "p"
    obj.version = 7
    lc.record_active_prompt("any", obj)
    assert lc.get_active_prompts() == {}


# ─── User-id consent gate ───────────────────────────────────────────────
@pytest.mark.asyncio
async def test_user_id_omit_mode_drops_user_id(monkeypatch):
    _install_fake_sdk(monkeypatch)
    monkeypatch.setattr(lc.settings, "LANGFUSE_USER_ID_MODE", "omit", raising=False)
    lc.init_langfuse()
    fake_trace = MagicMock()
    lc._client.trace.return_value = fake_trace
    async with lc.pipeline_trace("vid", user_id="user-123"):
        pass
    assert lc._client.trace.call_args.kwargs["user_id"] is None


@pytest.mark.asyncio
async def test_user_id_hash_mode_replaces_user_id(monkeypatch):
    _install_fake_sdk(monkeypatch)
    monkeypatch.setattr(lc.settings, "LANGFUSE_USER_ID_MODE", "hash", raising=False)
    monkeypatch.setattr(lc.settings, "LANGFUSE_USER_ID_HASH_SALT", "salty", raising=False)
    lc.init_langfuse()
    fake_trace = MagicMock()
    lc._client.trace.return_value = fake_trace
    async with lc.pipeline_trace("vid", user_id="user-123"):
        pass
    sent = lc._client.trace.call_args.kwargs["user_id"]
    assert sent != "user-123"
    assert len(sent) == 16  # 16-char hex digest
    # Deterministic given the salt
    async with lc.pipeline_trace("vid2", user_id="user-123"):
        pass
    assert lc._client.trace.call_args.kwargs["user_id"] == sent


@pytest.mark.asyncio
async def test_update_trace_metadata_merges_on_active_trace(monkeypatch):
    _install_fake_sdk(monkeypatch)
    lc.init_langfuse()
    fake_trace = MagicMock()
    lc._client.trace.return_value = fake_trace
    async with lc.pipeline_trace("vid"):
        lc.update_trace_metadata({"cacheHit": True})
    fake_trace.update.assert_called_once_with(metadata={"cacheHit": True})


def test_update_trace_metadata_no_op_outside_trace():
    """No trace open → silent no-op."""
    lc.update_trace_metadata({"cacheHit": True})  # must not raise


# ─── Prompt registry misses (pipeline-1min R40 B1) ───


class _Clock:
    """``lc.time`` stand-in: a monotonic clock the test moves."""

    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now


def _registry(monkeypatch, get_prompt: MagicMock) -> _Clock:
    monkeypatch.setattr(lc, "_client", MagicMock(get_prompt=get_prompt))
    clock = _Clock()
    monkeypatch.setattr(lc, "time", clock)
    return clock


class TestPromptMisses:
    def test_should_not_refetch_a_missing_prompt_within_the_miss_ttl(self, monkeypatch):
        get_prompt = MagicMock(side_effect=RuntimeError("404 prompt not found"))
        clock = _registry(monkeypatch, get_prompt)
        lc.fetch_prompt_with_obj("summarizer:extraction_group_job")
        clock.now += lc.PROMPT_MISS_TTL_S - 1

        lc.fetch_prompt_with_obj("summarizer:extraction_group_job")

        assert get_prompt.call_count == 1

    def test_should_refetch_a_missing_prompt_once_the_miss_ttl_passed(self, monkeypatch):
        get_prompt = MagicMock(side_effect=RuntimeError("404 prompt not found"))
        clock = _registry(monkeypatch, get_prompt)
        lc.fetch_prompt_with_obj("summarizer:extraction_group_job")
        clock.now += lc.PROMPT_MISS_TTL_S

        lc.fetch_prompt_with_obj("summarizer:extraction_group_job")

        assert get_prompt.call_count == 2

    def test_should_still_fetch_other_prompts_while_one_is_missing(self, monkeypatch):
        found = MagicMock(prompt="TEXT")
        get_prompt = MagicMock(side_effect=[RuntimeError("404"), found])
        _registry(monkeypatch, get_prompt)
        lc.fetch_prompt_with_obj("summarizer:extraction_group_job")

        assert lc.fetch_prompt_with_obj("summarizer:plan") is found

    def test_should_ask_the_sdk_for_a_found_prompt_on_every_load(self, monkeypatch):
        """Hits keep the SDK's own cache (60 s TTL, background refresh)."""
        get_prompt = MagicMock(return_value=MagicMock(prompt="TEXT"))
        _registry(monkeypatch, get_prompt)
        lc.fetch_prompt_with_obj("summarizer:plan")

        lc.fetch_prompt_with_obj("summarizer:plan")

        assert get_prompt.call_count == 2
