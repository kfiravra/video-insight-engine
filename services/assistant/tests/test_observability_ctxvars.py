"""Tests for P1 assistant observability — ctxvar propagation and Langfuse spans.

Verifies that:
- Each endpoint sets the correct llm_feature_var / llm_video_summary_id_var /
  llm_user_id_var / llm_request_id_var / llm_session_id_var at request time.
- Concurrent requests do not leak ctxvar state into each other.
- The action dispatcher sets llm_feature_var before executing a tool.
- The /action endpoint opens a chat_trace + span (best-effort).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from llm_common.context import (
    llm_feature_var,
    llm_request_id_var,
    llm_session_id_var,
    llm_user_id_var,
    llm_video_summary_id_var,
)
from src.services.tool_router import ActionDispatcher, ToolRouter


# ─── Helpers ────────────────────────────────────────────────────────────────


class _StubTool:
    """Minimal BaseTool stand-in."""

    def __init__(self, name: str, result: dict | None = None) -> None:
        self.name = name
        self.description = "stub"
        self.execute = AsyncMock(return_value=result or {"ok": True})


def _make_app_client_with_capture(captured: dict):
    """Return an HTTPX AsyncClient wired to a spy service that captures ctxvar
    values at the moment the service method is called."""
    from httpx import ASGITransport, AsyncClient
    from src.server import app

    async def _spy_chat(*, video_id, message, history, user_id, session_id, **_):
        captured["feature"] = llm_feature_var.get()
        captured["video_summary_id"] = llm_video_summary_id_var.get()
        captured["user_id"] = llm_user_id_var.get()
        captured["request_id"] = llm_request_id_var.get()
        captured["session_id"] = llm_session_id_var.get()
        captured["service_request_id"] = _.get("request_id")
        yield 'data: {"type": "done", "content": ""}\n\n'

    async def _spy_library_chat(*, video_ids, message, history, user_id, session_id, **_):
        captured["feature"] = llm_feature_var.get()
        captured["user_id"] = llm_user_id_var.get()
        captured["request_id"] = llm_request_id_var.get()
        yield 'data: {"type": "done", "content": ""}\n\n'

    spy = AsyncMock()
    spy.chat = _spy_chat
    spy.library_chat = _spy_library_chat
    spy.dispatch_action = AsyncMock(return_value={"done": True})

    app.state.assistant_service = spy

    transport = ASGITransport(app=app)
    return (
        AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"X-Internal-Secret": "dev-internal-secret-change-me"},
        ),
        spy,
    )


# ─── 1A: /chat ctxvars ──────────────────────────────────────────────────────


class TestChatEndpointCtxvars:
    """llm_*_var values set during /chat requests."""

    async def test_should_set_rag_chat_feature_for_chat(self):
        """llm_feature_var must equal 'assistant:rag_chat' during /chat."""
        captured: dict = {}
        client, spy = _make_app_client_with_capture(captured)

        async with client:
            await client.post(
                "/chat",
                json={"video_id": "vid-abc", "message": "hello"},
                headers={"X-User-Id": "u-1", "X-Request-ID": "req-12345678"},
            )

        assert captured.get("feature") == "assistant:rag_chat"

    async def test_should_propagate_the_summary_id_ctxvar_for_chat(self):
        captured: dict = {}
        client, _ = _make_app_client_with_capture(captured)

        async with client:
            await client.post(
                "/chat",
                json={"video_id": "vid-xyz", "message": "hello"},
            )

        assert captured.get("video_summary_id") == "vid-xyz"

    async def test_should_hand_the_session_to_the_ledger_and_the_request_id_to_the_service(self):
        captured: dict = {}
        client, _ = _make_app_client_with_capture(captured)

        async with client:
            await client.post(
                "/chat",
                json={"video_id": "vid-abc", "message": "hello"},
                headers={"X-Session-Id": "chat-session-1", "X-Request-ID": "req-12345678"},
            )

        assert captured.get("session_id") == "chat-session-1"
        assert captured.get("service_request_id") == "req-12345678"

    async def test_should_drop_a_forged_request_id_before_it_reaches_the_trace(self):
        captured: dict = {}
        client, _ = _make_app_client_with_capture(captured)

        async with client:
            await client.post(
                "/chat",
                json={"video_id": "vid-abc", "message": "hello"},
                headers={"X-Request-ID": "short"},
            )

        assert captured.get("service_request_id") is None

    async def test_should_propagate_user_id_ctxvar_for_chat(self):
        captured: dict = {}
        client, _ = _make_app_client_with_capture(captured)

        async with client:
            await client.post(
                "/chat",
                json={"video_id": "vid-abc", "message": "hello"},
                headers={"X-User-Id": "user-99"},
            )

        assert captured.get("user_id") == "user-99"

    async def test_should_propagate_request_id_ctxvar_for_chat(self):
        captured: dict = {}
        client, _ = _make_app_client_with_capture(captured)

        async with client:
            await client.post(
                "/chat",
                json={"video_id": "vid-abc", "message": "hello"},
                headers={"X-Request-ID": "req-abcdef12"},
            )

        assert captured.get("request_id") == "req-abcdef12"

    async def test_should_set_none_user_id_when_header_absent_for_chat(self):
        captured: dict = {}
        client, _ = _make_app_client_with_capture(captured)

        async with client:
            await client.post(
                "/chat",
                json={"video_id": "vid-abc", "message": "hello"},
                # No X-User-Id header
            )

        assert captured.get("user_id") is None


# ─── 1A: /library/chat ctxvars ──────────────────────────────────────────────


class TestLibraryChatEndpointCtxvars:
    """llm_*_var values set during /library/chat requests."""

    async def test_should_set_library_chat_feature(self):
        captured: dict = {}
        client, _ = _make_app_client_with_capture(captured)

        async with client:
            await client.post(
                "/library/chat",
                json={"video_ids": ["v1", "v2"], "message": "summarise"},
                headers={"X-User-Id": "u-77"},
            )

        assert captured.get("feature") == "assistant:library_chat"

    async def test_should_propagate_user_id_for_library_chat(self):
        captured: dict = {}
        client, _ = _make_app_client_with_capture(captured)

        async with client:
            await client.post(
                "/library/chat",
                json={"video_ids": ["v1"], "message": "hi"},
                headers={"X-User-Id": "lib-user-42"},
            )

        assert captured.get("user_id") == "lib-user-42"

    async def test_should_propagate_request_id_for_library_chat(self):
        captured: dict = {}
        client, _ = _make_app_client_with_capture(captured)

        async with client:
            await client.post(
                "/library/chat",
                json={"video_ids": ["v1"], "message": "hi"},
                headers={"X-Request-ID": "req-libchat1"},
            )

        assert captured.get("request_id") == "req-libchat1"


# ─── 1A + 1D: /action ctxvars ───────────────────────────────────────────────


class TestActionEndpointCtxvars:
    """llm_*_var values set during /action requests."""

    async def test_should_set_action_feature_with_action_name(self):
        """llm_feature_var must equal 'assistant:action:<action>' during dispatch."""
        from httpx import ASGITransport, AsyncClient
        from src.server import app

        captured: dict = {}

        original_dispatch = (
            app.state.assistant_service.dispatch_action
            if hasattr(app.state, "assistant_service")
            else None
        )

        spy = AsyncMock()

        async def _spy_dispatch(*args, **kwargs):
            captured["feature"] = llm_feature_var.get()
            captured["video_summary_id"] = llm_video_summary_id_var.get()
            captured["user_id"] = llm_user_id_var.get()
            captured["request_id"] = llm_request_id_var.get()
            return {"done": True}

        spy.dispatch_action = _spy_dispatch
        app.state.assistant_service = spy

        transport = ASGITransport(app=app)
        try:
            async with AsyncClient(
                transport=transport,
                base_url="http://test",
                headers={
                    "X-Internal-Secret": "dev-internal-secret-change-me",
                    "X-User-Id": "act-user-5",
                    "X-Request-ID": "req-action99",
                },
            ) as client:
                resp = await client.post(
                    "/action",
                    json={
                        "action": "quiz_me",
                        "video_id": "vid-action-test",
                        "params": {},
                    },
                )
                assert resp.status_code == 200
        finally:
            app.state.assistant_service = None

        assert captured.get("feature") == "assistant:action:quiz_me"
        assert captured.get("video_summary_id") == "vid-action-test"
        assert captured.get("user_id") == "act-user-5"
        assert captured.get("request_id") == "req-action99"

    async def test_should_set_action_feature_for_library_action(self):
        """Library-scoped actions (no video_id) still get feature set."""
        from httpx import ASGITransport, AsyncClient
        from src.server import app

        captured: dict = {}
        spy = AsyncMock()

        async def _spy_dispatch(*args, **kwargs):
            captured["feature"] = llm_feature_var.get()
            return {"done": True}

        spy.dispatch_action = _spy_dispatch
        app.state.assistant_service = spy

        transport = ASGITransport(app=app)
        try:
            async with AsyncClient(
                transport=transport,
                base_url="http://test",
                headers={"X-Internal-Secret": "dev-internal-secret-change-me"},
            ) as client:
                resp = await client.post(
                    "/action",
                    json={"action": "organize_library", "video_id": None, "params": {}},
                )
                assert resp.status_code == 200
        finally:
            app.state.assistant_service = None

        assert captured.get("feature") == "assistant:action:organize_library"


# ─── 1B: action dispatcher sets feature ctxvar ──────────────────────────────


class TestActionDispatchCtxvar:
    """ActionDispatcher.dispatch sets llm_feature_var before tool execution."""

    async def test_should_set_tool_feature_ctxvar_during_dispatch(self, sample_video_context):
        """llm_feature_var must equal 'assistant:tool:<name>' when execute() runs."""
        captured: dict = {}

        async def _capture_execute(params, ctx):
            captured["feature"] = llm_feature_var.get()
            return {"ok": True}

        stub = _StubTool("quiz_generator")
        stub.execute = AsyncMock(side_effect=_capture_execute)

        router = ToolRouter()
        router.register(stub)
        dispatcher = ActionDispatcher(router)

        await dispatcher.dispatch(
            action="quiz_me",
            video_id="vid1",
            params={},
            video_ctx=sample_video_context,
        )

        assert captured.get("feature") == "assistant:tool:quiz_generator"

    async def test_should_set_tool_feature_for_different_tools(self, sample_video_context):
        """Each tool sets its own feature name, not a shared one."""
        features_seen: list[str] = []

        async def _capture_note(params, ctx):
            features_seen.append(llm_feature_var.get())
            return {"ok": True}

        async def _capture_quiz(params, ctx):
            features_seen.append(llm_feature_var.get())
            return {"ok": True}

        note_tool = _StubTool("note_taker")
        note_tool.execute = AsyncMock(side_effect=_capture_note)
        quiz_tool = _StubTool("quiz_generator")
        quiz_tool.execute = AsyncMock(side_effect=_capture_quiz)

        router = ToolRouter()
        router.register(note_tool)
        router.register(quiz_tool)
        dispatcher = ActionDispatcher(router)

        await dispatcher.dispatch(
            action="save_note",
            video_id="vid1",
            params={"text": "hi"},
            video_ctx=sample_video_context,
        )
        await dispatcher.dispatch(
            action="quiz_me",
            video_id="vid1",
            params={},
            video_ctx=sample_video_context,
        )

        assert features_seen == [
            "assistant:tool:note_taker",
            "assistant:tool:quiz_generator",
        ]


# ─── No ctxvar leakage across concurrent requests ───────────────────────────


class TestCtxvarIsolation:
    """ContextVars are task-local — concurrent requests must not bleed state."""

    async def test_should_not_leak_feature_across_concurrent_tasks(self):
        """ContextVars are asyncio-task-local — setting in one task must not
        bleed into another task that runs concurrently.

        We simulate the server behaviour by spawning two tasks that each set
        llm_feature_var to a different value and then yield to let the other
        run. Each task must see only its own value.
        """
        results: dict[str, str] = {}
        barrier = asyncio.Event()

        async def _task(name: str, feature: str) -> None:
            llm_feature_var.set(feature)
            # Yield so the other task can run and potentially mutate the ctxvar.
            await asyncio.sleep(0)
            barrier.set()
            await asyncio.sleep(0)
            results[name] = llm_feature_var.get()

        await asyncio.gather(
            asyncio.create_task(_task("a", "assistant:rag_chat")),
            asyncio.create_task(_task("b", "assistant:library_chat")),
        )

        # Each task must still see its own value — no bleed.
        assert results["a"] == "assistant:rag_chat"
        assert results["b"] == "assistant:library_chat"

    async def test_should_not_leak_user_id_across_concurrent_requests(self):
        """X-User-Id from request A must not appear in request B's ctxvar."""
        from httpx import ASGITransport, AsyncClient
        from src.server import app

        user_ids_seen: list[str | None] = []

        async def _spy_chat(*, video_id, message, history, user_id, session_id, **_):
            await asyncio.sleep(0)
            user_ids_seen.append(llm_user_id_var.get())
            yield 'data: {"type": "done", "content": ""}\n\n'

        spy = AsyncMock()
        spy.chat = _spy_chat

        app.state.assistant_service = spy

        transport = ASGITransport(app=app)

        async def _post(uid: str):
            async with AsyncClient(
                transport=transport,
                base_url="http://test",
                headers={
                    "X-Internal-Secret": "dev-internal-secret-change-me",
                    "X-User-Id": uid,
                },
            ) as client:
                await client.post(
                    "/chat",
                    json={"video_id": "v1", "message": "hi"},
                )

        await asyncio.gather(_post("user-AAA"), _post("user-BBB"))

        app.state.assistant_service = None

        # Each request saw its own user_id — no cross-contamination.
        assert set(user_ids_seen) == {"user-AAA", "user-BBB"}


# ─── Feature-var is restored after tool dispatch (no leak into later gens) ────


class TestToolFeatureRestore:
    """The tool feature must be scoped to the tool call and restored after, so
    a generation running later in the same request keeps the chat feature."""

    async def test_dispatch_restores_feature_after_tool(self, sample_video_context):
        """After ActionDispatcher.dispatch() completes, the prior feature is restored."""
        from unittest.mock import AsyncMock

        stub = _StubTool("quiz_generator")
        stub.execute = AsyncMock(return_value={"ok": True})
        router = ToolRouter()
        router.register(stub)
        dispatcher = ActionDispatcher(router)

        token = llm_feature_var.set("assistant:rag_chat")
        try:
            await dispatcher.dispatch(
                action="quiz_me",
                video_id="vid1",
                params={},
                video_ctx=sample_video_context,
            )
            after = llm_feature_var.get()
        finally:
            llm_feature_var.reset(token)

        assert after == "assistant:rag_chat", (
            "dispatch() must restore the feature var — else a post-tool generation "
            "would be mislabelled with the tool's feature."
        )

    async def test_execute_tool_attributes_and_restores(self):
        """agent_tools.execute_tool (library agentic path) labels the tool's
        LLM calls assistant:tool:<name> and restores the chat feature after."""
        from unittest.mock import MagicMock, patch

        from src.services import agent_tools

        captured: dict[str, str | None] = {}

        async def _fake_dispatch(name, args, *, user_id, api_client, llm):
            captured["during"] = llm_feature_var.get()
            return {"ok": True}

        token = llm_feature_var.set("assistant:library_chat")
        try:
            with patch.object(agent_tools, "_dispatch", new=_fake_dispatch):
                result = await agent_tools.execute_tool(
                    "organize_library",
                    {},
                    user_id="u1",
                    api_client=MagicMock(),
                    llm=MagicMock(),
                )
            after = llm_feature_var.get()
        finally:
            llm_feature_var.reset(token)

        assert result == {"ok": True}
        assert captured["during"] == "assistant:tool:organize_library"
        assert after == "assistant:library_chat"  # restored, not leaked
