"""Tests for the assistant's Langfuse wiring (``src.services.observability``).

The shared client is tested in ``packages/llm-common/tests``; this file covers
what the assistant adds on top:
- ``init_langfuse()`` reads the assistant's settings
- the package re-exports the shared helpers
- ``chat_trace``: one trace per turn (``chat:<videoId>`` / ``chat:library``),
  session ``chat-<sessionId>``, no raw user id in the tags, the user-id policy
- tool and loop spans nest under the turn's trace
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from llm_common import observability as shared
from llm_common.observability import client as shared_client
from llm_common.observability.testing import LangfuseCapture

from src.config import settings
from src.services import observability
from src.services.observability import chat_trace, span

# ─── init_langfuse ──────────────────────────────────────────────────────


def test_init_returns_none_when_keys_missing(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "LANGFUSE_PUBLIC_KEY", None)
    monkeypatch.setattr(settings, "LANGFUSE_SECRET_KEY", None)
    assert observability.init_langfuse() is None
    assert observability.is_enabled() is False


def test_init_builds_the_client_from_assistant_settings(monkeypatch: pytest.MonkeyPatch):
    sdk = MagicMock(name="Langfuse")
    monkeypatch.setattr(shared_client, "_load_sdk", lambda: (sdk, MagicMock()))
    monkeypatch.setattr(settings, "LANGFUSE_PUBLIC_KEY", "pk_test")
    monkeypatch.setattr(settings, "LANGFUSE_SECRET_KEY", "sk_test")
    monkeypatch.setattr(settings, "LANGFUSE_BASE_URL", "https://cloud.langfuse.com")
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")

    assert observability.init_langfuse() is sdk.return_value
    assert sdk.call_args.kwargs["base_url"] == "https://cloud.langfuse.com"
    assert sdk.call_args.kwargs["environment"] == "development"


@pytest.mark.parametrize("name", observability.__all__)
def test_package_re_exports_the_shared_helpers(name: str):
    if name in {"init_langfuse", "chat_trace"}:
        pytest.skip("assistant-specific")
    assert getattr(observability, name) is getattr(shared, name)


# ─── chat_trace ─────────────────────────────────────────────────────────


async def test_chat_trace_yields_none_when_disabled():
    async with chat_trace(video_id="v1") as root:
        assert root is None


async def test_chat_trace_names_the_turn_and_groups_the_session(
    langfuse_capture: LangfuseCapture,
):
    async with chat_trace(
        video_id="yt1", user_id="u1", session_id="s1", metadata={"historyLen": 2}
    ) as root:
        assert root is not None

    turn = langfuse_capture.named("chat:yt1")
    assert turn.attributes["session.id"] == "chat-s1"
    assert turn.attributes["user.id"] == "u1"
    assert turn.attributes["langfuse.trace.tags"] == ("videoId:yt1",)
    assert turn.attributes["langfuse.trace.metadata.historyLen"] == "2"


async def test_library_chat_trace_has_no_video(langfuse_capture: LangfuseCapture):
    async with chat_trace(video_id=None, tags=["library"]):
        pass

    turn = langfuse_capture.named("chat:library")
    assert turn.attributes["langfuse.trace.tags"] == ("library",)
    assert "session.id" not in turn.attributes


async def test_user_id_omit_mode_sends_no_user(
    monkeypatch: pytest.MonkeyPatch, langfuse_capture: LangfuseCapture
):
    monkeypatch.setattr(shared_client._settings, "LANGFUSE_USER_ID_MODE", "omit")

    async with chat_trace(video_id="yt1", user_id="u1"):
        pass

    assert "user.id" not in langfuse_capture.named("chat:yt1").attributes


async def test_tool_and_loop_spans_nest_under_the_turn(langfuse_capture: LangfuseCapture):
    async with chat_trace(video_id="yt1"):
        async with span("library_agent"):
            async with span("tool:quiz_generator", as_type="tool"):
                pass

    agent = langfuse_capture.named("library_agent")
    tool = langfuse_capture.named("tool:quiz_generator")
    assert agent.parent == "chat:yt1"
    assert (tool.parent, tool.type) == ("library_agent", "tool")


async def test_span_yields_none_outside_a_turn(langfuse_capture: LangfuseCapture):
    async with span("tool:orphan") as handle:
        assert handle is None
    assert langfuse_capture.observations() == []
