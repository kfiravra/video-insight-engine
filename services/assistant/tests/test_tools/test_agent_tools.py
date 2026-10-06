"""Tests for the agentic tool dispatcher (execute_tool)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.services.agent_tools import AGENT_TOOL_SCHEMAS, execute_tool


@pytest.fixture
def mock_llm():
    return AsyncMock()


class TestAgentToolSchemas:
    """AGENT_TOOL_SCHEMAS exposes every supported library operation."""

    def test_should_expose_all_expected_tools(self) -> None:
        names = {schema["function"]["name"] for schema in AGENT_TOOL_SCHEMAS}
        assert names == {
            "list_folders",
            "list_videos",
            "create_folder",
            "rename_folder",
            "move_folder",
            "delete_folder",
            "move_video",
            "generate_video",
            "organize_library",
        }

    def test_should_use_openai_function_envelope(self) -> None:
        for schema in AGENT_TOOL_SCHEMAS:
            assert schema["type"] == "function"
            fn = schema["function"]
            assert fn["name"] and fn["description"]
            assert fn["parameters"]["type"] == "object"


class TestExecuteToolRouting:
    """execute_tool dispatches to the right api_client method with injected user_id."""

    async def test_should_route_list_folders(self, mock_api_client, mock_llm) -> None:
        mock_api_client.list_folders.return_value = [{"id": "f1", "name": "Tech"}]

        res = await execute_tool(
            "list_folders",
            {},
            user_id="u1",
            api_client=mock_api_client,
            llm=mock_llm,
        )

        mock_api_client.list_folders.assert_awaited_once_with("u1")
        assert res == {"ok": True, "folders": [{"id": "f1", "name": "Tech"}]}

    async def test_should_route_list_videos(self, mock_api_client, mock_llm) -> None:
        mock_api_client.list_videos.return_value = [{"id": "v1", "title": "Pasta"}]

        res = await execute_tool(
            "list_videos",
            {},
            user_id="u1",
            api_client=mock_api_client,
            llm=mock_llm,
        )

        mock_api_client.list_videos.assert_awaited_once_with("u1")
        assert res["videos"] == [{"id": "v1", "title": "Pasta"}]

    async def test_should_route_create_folder_with_optional_fields(
        self,
        mock_api_client,
        mock_llm,
    ) -> None:
        await execute_tool(
            "create_folder",
            {"name": "Cooking", "parent_id": "p1", "color": "red"},
            user_id="u1",
            api_client=mock_api_client,
            llm=mock_llm,
        )

        mock_api_client.create_folder.assert_awaited_once_with(
            "u1",
            "Cooking",
            parentId="p1",
            color="red",
            icon=None,
        )

    async def test_should_route_rename_folder(self, mock_api_client, mock_llm) -> None:
        await execute_tool(
            "rename_folder",
            {"folder_id": "f1", "name": "Renamed"},
            user_id="u1",
            api_client=mock_api_client,
            llm=mock_llm,
        )

        mock_api_client.update_folder.assert_awaited_once_with("u1", "f1", name="Renamed")

    async def test_should_route_move_folder_to_root(self, mock_api_client, mock_llm) -> None:
        await execute_tool(
            "move_folder",
            {"folder_id": "f1"},
            user_id="u1",
            api_client=mock_api_client,
            llm=mock_llm,
        )

        mock_api_client.move_folder.assert_awaited_once_with("u1", "f1", None)

    async def test_should_route_delete_folder_with_content_flag(
        self,
        mock_api_client,
        mock_llm,
    ) -> None:
        await execute_tool(
            "delete_folder",
            {"folder_id": "f1", "delete_content": True},
            user_id="u1",
            api_client=mock_api_client,
            llm=mock_llm,
        )

        mock_api_client.delete_folder.assert_awaited_once_with("u1", "f1", True)

    async def test_should_route_move_video(self, mock_api_client, mock_llm) -> None:
        await execute_tool(
            "move_video",
            {"video_id": "v1", "folder_id": "f1"},
            user_id="u1",
            api_client=mock_api_client,
            llm=mock_llm,
        )

        mock_api_client.move_video.assert_awaited_once_with("u1", "v1", "f1")

    async def test_should_route_generate_video(self, mock_api_client, mock_llm) -> None:
        await execute_tool(
            "generate_video",
            {"url": "https://youtu.be/x", "folder_id": "f1"},
            user_id="u1",
            api_client=mock_api_client,
            llm=mock_llm,
        )

        mock_api_client.generate_video.assert_awaited_once_with(
            "u1",
            "https://youtu.be/x",
            "f1",
        )

    async def test_should_resolve_a_folder_name_to_its_id_when_generating(
        self, mock_api_client, mock_llm
    ) -> None:
        folder_id = "6ac4dfb850b583ff7b57c748"
        mock_api_client.list_folders.return_value = [{"id": folder_id, "name": "Card Games"}]

        await execute_tool(
            "generate_video",
            {"url": "https://youtu.be/x", "folder_id": "card games"},
            user_id="u1",
            api_client=mock_api_client,
            llm=mock_llm,
        )

        mock_api_client.generate_video.assert_awaited_once_with(
            "u1", "https://youtu.be/x", folder_id
        )

    async def test_should_generate_without_a_folder_when_none_is_given(
        self, mock_api_client, mock_llm
    ) -> None:
        await execute_tool(
            "generate_video",
            {"url": "https://youtu.be/x"},
            user_id="u1",
            api_client=mock_api_client,
            llm=mock_llm,
        )

        mock_api_client.generate_video.assert_awaited_once_with("u1", "https://youtu.be/x", None)

    async def test_should_not_look_up_folders_when_given_an_object_id(
        self, mock_api_client, mock_llm
    ) -> None:
        folder_id = "6ac4dfb850b583ff7b57c748"

        await execute_tool(
            "move_video",
            {"video_id": "v1", "folder_id": folder_id},
            user_id="u1",
            api_client=mock_api_client,
            llm=mock_llm,
        )

        mock_api_client.list_folders.assert_not_awaited()

    async def test_should_route_organize_library(self, mock_api_client, mock_llm) -> None:
        # LibraryOrganizerTool needs videos+an LLM proposal; stub both.
        mock_api_client.list_videos.return_value = []
        mock_api_client.list_folders.return_value = []

        res = await execute_tool(
            "organize_library",
            {},
            user_id="u1",
            api_client=mock_api_client,
            llm=mock_llm,
        )

        # No videos -> tool no-ops but still routes and reports counts.
        assert res == {"ok": True, "result": {"folders_created": 0, "videos_moved": 0}}

    async def test_should_never_take_user_id_from_args(
        self,
        mock_api_client,
        mock_llm,
    ) -> None:
        # A malicious model supplies a user_id in args; it must be ignored.
        await execute_tool(
            "list_folders",
            {"user_id": "attacker"},
            user_id="victim",
            api_client=mock_api_client,
            llm=mock_llm,
        )

        mock_api_client.list_folders.assert_awaited_once_with("victim")


class TestExecuteToolErrors:
    """execute_tool returns {error} so the model can self-correct."""

    async def test_should_return_error_for_unknown_tool(
        self,
        mock_api_client,
        mock_llm,
    ) -> None:
        res = await execute_tool(
            "frobnicate",
            {},
            user_id="u1",
            api_client=mock_api_client,
            llm=mock_llm,
        )

        assert "error" in res
        assert "unknown tool" in res["error"]

    async def test_should_return_error_on_missing_required_arg(
        self,
        mock_api_client,
        mock_llm,
    ) -> None:
        # create_folder requires "name"; omitting it must not crash the loop.
        res = await execute_tool(
            "create_folder",
            {},
            user_id="u1",
            api_client=mock_api_client,
            llm=mock_llm,
        )

        assert "error" in res
        mock_api_client.create_folder.assert_not_awaited()

    async def test_should_return_error_when_api_client_raises(
        self,
        mock_api_client,
        mock_llm,
    ) -> None:
        mock_api_client.move_video.side_effect = RuntimeError("upstream down")

        res = await execute_tool(
            "move_video",
            {"video_id": "v1", "folder_id": "f1"},
            user_id="u1",
            api_client=mock_api_client,
            llm=mock_llm,
        )

        assert "error" in res
        assert "move_video failed" in res["error"]
