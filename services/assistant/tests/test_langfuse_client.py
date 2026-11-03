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


def test_init_should_return_none_when_keys_are_missing(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "LANGFUSE_PUBLIC_KEY", None)
    monkeypatch.setattr(settings, "LANGFUSE_SECRET_KEY", None)
    assert observability.init_langfuse() is None
    assert observability.is_enabled() is False


def test_init_should_build_the_client_from_assistant_settings_when_keys_are_set(
    monkeypatch: pytest.MonkeyPatch,
):
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
def test_package_should_re_export_the_shared_helper_when_imported(name: str):
    if name in {"init_langfuse", "chat_trace", "chat_turn"}:
        pytest.skip("assistant-specific")
    assert getattr(observability, name) is getattr(shared, name)


# ─── chat_trace ─────────────────────────────────────────────────────────


async def test_chat_trace_should_yield_none_when_disabled():
    async with chat_trace(video_summary_id="v1") as root:
        assert root is None


async def test_chat_trace_should_name_the_turn_and_group_the_session_when_a_session_id_is_sent(
    langfuse_capture: LangfuseCapture,
):
    async with chat_trace(
        video_summary_id="yt1", user_id="u1", session_id="s1", metadata={"historyLen": 2}
    ) as root:
        assert root is not None

    turn = langfuse_capture.named("chat:yt1")
    assert turn.attributes["session.id"] == "chat-s1"
    assert turn.attributes["user.id"] == "u1"
    assert turn.attributes["langfuse.trace.tags"] == ("videoSummaryId:yt1",)
    assert turn.attributes["langfuse.trace.metadata.historyLen"] == "2"


async def test_chat_trace_should_tag_the_ids_and_turn_the_pipeline_traces_use(
    langfuse_capture: LangfuseCapture,
):
    async with chat_trace(
        video_summary_id="vs1", youtube_id="yt1", request_id="req-12345678", turn=3
    ):
        pass

    turn = langfuse_capture.named("chat:vs1")
    assert turn.attributes["langfuse.trace.tags"] == (
        "videoSummaryId:vs1",
        "youtubeId:yt1",
        "requestId:req-12345678",
        "turn:3",
    )


def test_chat_turn_should_count_the_user_messages_before_this_one():
    from src.models.requests import ChatMessage
    from src.services.observability import chat_turn

    history = [
        ChatMessage(role="user", content="a"),
        ChatMessage(role="assistant", content="b"),
        ChatMessage(role="user", content="c"),
        ChatMessage(role="assistant", content="d"),
    ]
    assert chat_turn([]) == 1
    assert chat_turn(history) == 3


async def test_chat_trace_should_be_named_library_when_there_is_no_video(
    langfuse_capture: LangfuseCapture,
):
    async with chat_trace(video_summary_id=None, tags=["library"]):
        pass

    turn = langfuse_capture.named("chat:library")
    assert turn.attributes["langfuse.trace.tags"] == ("library",)
    assert "session.id" not in turn.attributes


async def test_chat_trace_should_send_no_user_when_the_mode_is_omit(
    monkeypatch: pytest.MonkeyPatch, langfuse_capture: LangfuseCapture
):
    monkeypatch.setattr(shared_client._settings, "LANGFUSE_USER_ID_MODE", "omit")

    async with chat_trace(video_summary_id="yt1", user_id="u1"):
        pass

    assert "user.id" not in langfuse_capture.named("chat:yt1").attributes


async def test_spans_should_nest_under_the_turn_when_opened_inside_it(
    langfuse_capture: LangfuseCapture,
):
    async with chat_trace(video_summary_id="yt1"):
        async with span("library_agent"):
            async with span("tool:quiz_generator", as_type="tool"):
                pass

    agent = langfuse_capture.named("library_agent")
    tool = langfuse_capture.named("tool:quiz_generator")
    assert agent.parent == "chat:yt1"
    assert (tool.parent, tool.type) == ("library_agent", "tool")


async def test_span_should_yield_none_when_outside_a_turn(langfuse_capture: LangfuseCapture):
    async with span("tool:orphan") as handle:
        assert handle is None
    assert langfuse_capture.observations() == []
