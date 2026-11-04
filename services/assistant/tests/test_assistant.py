"""Tests for AssistantService — core chat orchestrator."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from src.models.requests import ChatMessage


class TestAssistantChat:
    """AssistantService.chat() behaviour."""

    async def test_should_stream_response_tokens(self, assistant_service):
        events = []
        async for event in assistant_service.chat(
            video_id="abc123",
            message="What is this video about?",
            history=[],
        ):
            events.append(event)

        assert len(events) > 0
        text_events = [e for e in events if '"type":"text"' in e]
        assert len(text_events) > 0

    async def test_should_call_video_repo(self, assistant_service, mock_video_repo):
        async for _ in assistant_service.chat(
            video_id="abc123",
            message="Explain.",
            history=[],
        ):
            pass

        mock_video_repo.get_video_context.assert_called_once_with("abc123")

    async def test_should_search_rag_with_message(self, assistant_service, mock_rag):
        async for _ in assistant_service.chat(
            video_id="abc123",
            message="What is backpropagation?",
            history=[],
        ):
            pass

        mock_rag.search.assert_called_once()
        call_kwargs = mock_rag.search.call_args.kwargs
        assert call_kwargs["query"] == "What is backpropagation?"

    async def test_should_search_rag_with_youtube_id_not_mongo_id(
        self,
        assistant_service,
        mock_rag,
    ):
        # Incoming video_id is the Mongo _id; Qdrant is keyed by YouTube ID,
        # so the RAG search must use video_ctx.youtube_id ("abc123"), not "vid123".
        async for _ in assistant_service.chat(
            video_id="vid123",
            message="Summarize the main ideas",
            history=[],
        ):
            pass

        assert mock_rag.search.await_args.kwargs["video_id"] == "abc123"

    async def test_should_include_history(self, assistant_service):
        history = [
            ChatMessage(role="user", content="What are neural networks?"),
            ChatMessage(role="assistant", content="Neural networks are..."),
        ]

        events = []
        async for event in assistant_service.chat(
            video_id="abc123",
            message="Tell me more",
            history=history,
        ):
            events.append(event)

        assert len(events) > 0

    async def test_should_enforce_max_turns(self, assistant_service):
        history = [
            ChatMessage(role="user" if i % 2 == 0 else "assistant", content=f"msg {i}")
            for i in range(50)
        ]

        events = []
        async for event in assistant_service.chat(
            video_id="abc123",
            message="Another question",
            history=history,
        ):
            events.append(event)

        assert len(events) > 0

    async def test_should_cache_video_context(self, assistant_service, mock_video_repo):
        async for _ in assistant_service.chat(video_id="abc123", message="Q1", history=[]):
            pass
        async for _ in assistant_service.chat(video_id="abc123", message="Q2", history=[]):
            pass

        assert mock_video_repo.get_video_context.call_count == 1

    async def test_should_raise_for_unknown_video(self, mock_rag, mock_llm, mock_settings):
        from src.services.assistant import AssistantService
        from src.services.context_builder import ContextBuilder

        missing_repo = AsyncMock()
        missing_repo.get_video_context.return_value = None

        assistant = AssistantService(
            llm=mock_llm,
            rag=mock_rag,
            video_repo=missing_repo,
            context_builder=ContextBuilder(),
            settings=mock_settings,
        )

        with pytest.raises(Exception):
            async for _ in assistant.chat(video_id="nonexistent", message="Hello", history=[]):
                pass

    async def test_should_yield_sources_before_text(self, assistant_service):
        events = []
        async for event in assistant_service.chat(
            video_id="abc123",
            message="What about layers?",
            history=[],
        ):
            events.append(event)

        source_idxs = [i for i, e in enumerate(events) if '"type":"source"' in e]
        text_idxs = [i for i, e in enumerate(events) if '"type":"text"' in e]
        if source_idxs and text_idxs:
            assert max(source_idxs) < min(text_idxs)

    async def test_should_yield_done_at_end(self, assistant_service):
        events = []
        async for event in assistant_service.chat(
            video_id="abc123",
            message="Summarize.",
            history=[],
        ):
            events.append(event)

        assert len(events) > 0
        assert '"type":"done"' in events[-1]

    async def test_should_handle_llm_error(self, mock_video_repo, mock_rag, mock_settings):
        from src.services.assistant import AssistantService
        from src.services.context_builder import ContextBuilder

        failing_llm = AsyncMock()

        async def _failing_stream(*_a, **_k):
            raise RuntimeError("LLM unavailable")
            yield  # noqa: unreachable

        failing_llm.stream_with_messages = _failing_stream
        failing_llm.model = "test"

        assistant = AssistantService(
            llm=failing_llm,
            rag=mock_rag,
            video_repo=mock_video_repo,
            context_builder=ContextBuilder(),
            settings=mock_settings,
        )

        events = []
        async for event in assistant.chat(video_id="abc123", message="Hello", history=[]):
            events.append(event)

        error_events = [e for e in events if '"type":"error"' in e]
        assert len(error_events) > 0


class TestAssistantLibraryChat:
    """AssistantService.library_chat() behaviour."""

    async def test_should_forward_video_ids_to_search_library(
        self,
        assistant_service,
        mock_rag,
    ):
        mock_rag.search_library = AsyncMock(return_value=[])

        async for _ in assistant_service.library_chat(
            video_ids=["v1", "v2", "v3"],
            message="What did I learn?",
            history=[],
        ):
            pass

        mock_rag.search_library.assert_called_once()
        assert mock_rag.search_library.call_args.kwargs["video_ids"] == ["v1", "v2", "v3"]

    async def test_should_search_library_with_message(
        self,
        assistant_service,
        mock_rag,
    ):
        mock_rag.search_library = AsyncMock(return_value=[])

        async for _ in assistant_service.library_chat(
            video_ids=["v1"],
            message="Compare React and Vue",
            history=[],
        ):
            pass

        assert mock_rag.search_library.call_args.kwargs["query"] == "Compare React and Vue"

    async def test_should_stream_source_text_and_done(
        self,
        assistant_service,
        mock_rag,
        sample_rag_sources,
    ):
        mock_rag.search_library = AsyncMock(return_value=sample_rag_sources)

        events = []
        async for event in assistant_service.library_chat(
            video_ids=["v1", "v2"],
            message="Summarize my library",
            history=[],
        ):
            events.append(event)

        assert any('"type":"source"' in e for e in events)
        assert any('"type":"text"' in e for e in events)
        assert '"type":"done"' in events[-1]

    async def test_should_yield_sources_before_text(
        self,
        assistant_service,
        mock_rag,
        sample_rag_sources,
    ):
        mock_rag.search_library = AsyncMock(return_value=sample_rag_sources)

        events = []
        async for event in assistant_service.library_chat(
            video_ids=["v1"],
            message="What about layers?",
            history=[],
        ):
            events.append(event)

        source_idxs = [i for i, e in enumerate(events) if '"type":"source"' in e]
        text_idxs = [i for i, e in enumerate(events) if '"type":"text"' in e]
        assert source_idxs and text_idxs
        assert max(source_idxs) < min(text_idxs)

    async def test_should_handle_empty_video_ids_gracefully(
        self,
        assistant_service,
        mock_rag,
    ):
        mock_rag.search_library = AsyncMock(return_value=[])

        events = []
        async for event in assistant_service.library_chat(
            video_ids=[],
            message="Anything?",
            history=[],
        ):
            events.append(event)

        # No source event when nothing retrieved, but still streams + done.
        assert not any('"type":"source"' in e for e in events)
        assert '"type":"done"' in events[-1]

    async def test_should_handle_llm_error(
        self,
        mock_video_repo,
        mock_rag,
        mock_settings,
    ):
        from src.services.assistant import AssistantService
        from src.services.context_builder import ContextBuilder

        mock_rag.search_library = AsyncMock(return_value=[])
        failing_llm = AsyncMock()

        async def _failing_stream(*_a, **_k):
            raise RuntimeError("LLM unavailable")
            yield  # noqa: unreachable

        failing_llm.stream_with_messages = _failing_stream
        failing_llm.model = "test"

        assistant = AssistantService(
            llm=failing_llm,
            rag=mock_rag,
            video_repo=mock_video_repo,
            context_builder=ContextBuilder(),
            settings=mock_settings,
        )

        events = []
        async for event in assistant.library_chat(
            video_ids=["v1"],
            message="Hello",
            history=[],
        ):
            events.append(event)

        assert any('"type":"error"' in e for e in events)
        assert '"type":"done"' in events[-1]


class TestAssistantLibraryAgenticLoop:
    """library_chat drives an agentic tool-calling loop when api_client is set."""

    @staticmethod
    def _tool_completion(calls):
        from src.services.llm_provider import ToolCompletion

        return ToolCompletion(content=None, tool_calls=calls)

    @staticmethod
    def _final(content):
        from src.services.llm_provider import ToolCompletion

        return ToolCompletion(content=content, tool_calls=[])

    async def test_should_execute_tools_in_order_then_answer(
        self,
        agentic_assistant_service,
        mock_llm,
        mock_rag,
        mock_api_client,
    ):
        mock_rag.search_library = AsyncMock(return_value=[])
        mock_api_client.create_folder.return_value = {"id": "f9", "name": "Series"}

        steps = [
            self._tool_completion(
                [
                    {"id": "c1", "name": "create_folder", "arguments": {"name": "Series"}},
                ]
            ),
            self._tool_completion(
                [
                    {
                        "id": "c2",
                        "name": "move_video",
                        "arguments": {"video_id": "v1", "folder_id": "f9"},
                    },
                ]
            ),
            self._final("Done — moved your video into the Series folder."),
        ]
        mock_llm.complete_with_tools = AsyncMock(side_effect=steps)

        events = []
        async for event in agentic_assistant_service.library_chat(
            video_ids=["v1"],
            message="Make a Series folder and move my video",
            history=[],
            user_id="u1",
        ):
            events.append(event)

        # Tools executed in order with injected user_id.
        mock_api_client.create_folder.assert_awaited_once_with(
            "u1",
            "Series",
            parentId=None,
            color=None,
            icon=None,
        )
        mock_api_client.move_video.assert_awaited_once_with("u1", "v1", "f9")

        # "tool" SSE events emitted (start + done per call).
        tool_events = [e for e in events if '"type":"tool"' in e]
        assert len(tool_events) == 4
        assert any('"status":"start"' in e for e in tool_events)
        assert any('"status":"done"' in e for e in tool_events)

        # Final answer streamed as text, then done.
        assert any('"type":"text"' in e and "Series folder" in e for e in events)
        assert '"type":"done"' in events[-1]

    async def test_should_skip_tools_when_no_user_id(
        self,
        agentic_assistant_service,
        mock_llm,
        mock_rag,
    ):
        # Without a user_id the loop degrades to a plain stream (no tools).
        mock_rag.search_library = AsyncMock(return_value=[])
        mock_llm.complete_with_tools = AsyncMock(
            side_effect=AssertionError("tools must not be called without user_id")
        )

        events = []
        async for event in agentic_assistant_service.library_chat(
            video_ids=["v1"],
            message="Hi",
            history=[],
        ):
            events.append(event)

        assert any('"type":"text"' in e for e in events)
        assert '"type":"done"' in events[-1]

    async def test_should_respect_max_tool_iters(
        self,
        agentic_assistant_service,
        mock_llm,
        mock_rag,
        mock_api_client,
    ):
        from src.services.assistant import MAX_TOOL_ITERS
        from src.services.agent_tools import AGENT_TOOL_SCHEMAS

        mock_rag.search_library = AsyncMock(return_value=[])
        # Model never stops calling tools — loop must cap and fall back to stream.
        looping = self._tool_completion(
            [
                {"id": "c", "name": "list_folders", "arguments": {}},
            ]
        )
        mock_llm.complete_with_tools = AsyncMock(return_value=looping)

        # Capture the kwargs of the tool-free fallback stream: it must re-declare
        # the tool schemas with tool_choice="none" or Anthropic 400s on the
        # dangling tool-call history (regression guard).
        stream_kwargs: dict = {}

        async def _capturing_stream(*_args, **kwargs):
            stream_kwargs.update(kwargs)
            for token in ["Final", " answer"]:
                yield token

        mock_llm.stream_with_messages = _capturing_stream

        events = []
        async for event in agentic_assistant_service.library_chat(
            video_ids=["v1"],
            message="loop forever",
            history=[],
            user_id="u1",
        ):
            events.append(event)

        assert mock_llm.complete_with_tools.await_count == MAX_TOOL_ITERS
        # After exhausting the budget it streams a final answer + done.
        assert any('"type":"text"' in e for e in events)
        assert '"type":"done"' in events[-1]
        # The fallback stream must keep tools declared but disallow further calls.
        assert stream_kwargs.get("tools") is AGENT_TOOL_SCHEMAS
        assert stream_kwargs.get("tool_choice") == "none"

    async def test_should_answer_directly_without_tools(
        self,
        agentic_assistant_service,
        mock_llm,
        mock_rag,
    ):
        mock_rag.search_library = AsyncMock(return_value=[])
        mock_llm.complete_with_tools = AsyncMock(
            return_value=self._final("You have 3 videos saved.")
        )

        events = []
        async for event in agentic_assistant_service.library_chat(
            video_ids=["v1"],
            message="How many videos do I have?",
            history=[],
            user_id="u1",
        ):
            events.append(event)

        assert mock_llm.complete_with_tools.await_count == 1
        assert not any('"type":"tool"' in e for e in events)
        assert any("3 videos" in e for e in events)
        assert '"type":"done"' in events[-1]


class TestAssistantSingleVideoAgenticLoop:
    """chat() (single open video) also exposes the library action tools, so the
    assistant can organize the collection without the user leaving the video."""

    @staticmethod
    def _tool_completion(calls):
        from src.services.llm_provider import ToolCompletion

        return ToolCompletion(content=None, tool_calls=calls)

    @staticmethod
    def _final(content):
        from src.services.llm_provider import ToolCompletion

        return ToolCompletion(content=content, tool_calls=[])

    async def test_should_execute_library_tools_while_a_video_is_open(
        self,
        agentic_assistant_service,
        mock_llm,
        mock_api_client,
    ):
        mock_api_client.create_folder.return_value = {"id": "f9", "name": "Series"}
        steps = [
            self._tool_completion(
                [
                    {"id": "c1", "name": "create_folder", "arguments": {"name": "Series"}},
                ]
            ),
            self._tool_completion(
                [
                    {
                        "id": "c2",
                        "name": "move_video",
                        "arguments": {"video_id": "v1", "folder_id": "f9"},
                    },
                ]
            ),
            self._final("Done — created Series and moved your video in."),
        ]
        mock_llm.complete_with_tools = AsyncMock(side_effect=steps)

        events = []
        async for event in agentic_assistant_service.chat(
            video_id="abc123",
            message="Make a Series folder and move my video into it",
            history=[],
            user_id="u1",
        ):
            events.append(event)

        # Library tools executed in order with the caller's user_id injected.
        mock_api_client.create_folder.assert_awaited_once_with(
            "u1",
            "Series",
            parentId=None,
            color=None,
            icon=None,
        )
        mock_api_client.move_video.assert_awaited_once_with("u1", "v1", "f9")

        # Tool steps surfaced (start + done per call) and a final answer + done.
        tool_events = [e for e in events if '"type":"tool"' in e]
        assert len(tool_events) == 4
        assert any('"type":"text"' in e and "Series" in e for e in events)
        assert '"type":"done"' in events[-1]

    async def test_should_not_call_tools_without_api_client(
        self,
        assistant_service,
        mock_llm,
    ):
        # No api_client wired → degrades to a plain video-grounded stream.
        mock_llm.complete_with_tools = AsyncMock(
            side_effect=AssertionError("tools must not run without an api_client")
        )

        events = []
        async for event in assistant_service.chat(
            video_id="abc123",
            message="Organize my collection",
            history=[],
            user_id="u1",
        ):
            events.append(event)

        assert any('"type":"text"' in e for e in events)
        assert '"type":"done"' in events[-1]

    async def test_should_not_call_tools_without_user_id(
        self,
        agentic_assistant_service,
        mock_llm,
    ):
        # api_client present but no user_id (header missing) → no tools offered.
        mock_llm.complete_with_tools = AsyncMock(
            side_effect=AssertionError("tools must not run without a user_id")
        )

        events = []
        async for event in agentic_assistant_service.chat(
            video_id="abc123",
            message="Organize my collection",
            history=[],
        ):
            events.append(event)

        assert any('"type":"text"' in e for e in events)
        assert '"type":"done"' in events[-1]

    async def test_should_still_ground_in_the_open_video(
        self,
        agentic_assistant_service,
        mock_llm,
        mock_rag,
    ):
        # A plain question runs the loop once, answers directly, no tools, and
        # still searches the open video's chunks for grounding.
        mock_llm.complete_with_tools = AsyncMock(
            return_value=self._final("It is about neural networks.")
        )

        events = []
        async for event in agentic_assistant_service.chat(
            video_id="abc123",
            message="What is this video about?",
            history=[],
            user_id="u1",
        ):
            events.append(event)

        mock_rag.search.assert_awaited()
        assert not any('"type":"tool"' in e for e in events)
        assert any("neural networks" in e for e in events)
        assert '"type":"done"' in events[-1]


class TestLedgerVideoIds:
    """D9: assistant ledger rows name the video by its YouTube id, the row by its summary id."""

    async def test_should_bind_the_youtube_id_when_an_action_loads_the_video(
        self, assistant_service, sample_video_context
    ):
        from unittest.mock import AsyncMock

        from llm_common.context import llm_video_id_var, llm_video_summary_id_var, usage_scope

        seen: dict = {}

        async def _dispatch(**_kwargs):
            seen["video_id"] = llm_video_id_var.get()
            seen["video_summary_id"] = llm_video_summary_id_var.get()
            return {"ok": True}

        assistant_service._action_dispatcher.dispatch = AsyncMock(side_effect=_dispatch)
        with usage_scope():
            await assistant_service.dispatch_action(
                action="quiz_me", video_id="vsum-1", params={}, user_id="u1"
            )

        assert seen == {
            "video_id": sample_video_context.youtube_id,
            "video_summary_id": "vsum-1",
        }

    async def test_should_name_the_youtube_id_of_a_known_video(
        self, assistant_service, sample_video_context
    ):
        assert await assistant_service.youtube_id_of("vsum-1") == sample_video_context.youtube_id
        assert await assistant_service.youtube_id_of(None) is None

    async def test_should_give_no_youtube_id_for_an_unknown_video(
        self, assistant_service, mock_video_repo
    ):
        mock_video_repo.get_video_context.return_value = None
        assert await assistant_service.youtube_id_of("missing") is None

    async def test_should_bind_the_youtube_id_for_a_video_chat_turn(
        self, assistant_service, sample_video_context
    ):
        from llm_common.context import llm_video_id_var, llm_video_summary_id_var, usage_scope

        seen: dict = {}

        async def _spy_rag_chat(*_args, **_kwargs):
            seen["video_id"] = llm_video_id_var.get()
            seen["video_summary_id"] = llm_video_summary_id_var.get()
            yield 'data: {"type": "done"}\n\n'

        assistant_service._rag_chat = _spy_rag_chat
        with usage_scope():
            _ = [event async for event in assistant_service.chat("vsum-1", "hi", [])]

        assert seen == {
            "video_id": sample_video_context.youtube_id,
            "video_summary_id": "vsum-1",
        }
