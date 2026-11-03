"""Tests for the outbound vie-api ApiClient (httpx MockTransport)."""

from __future__ import annotations

import httpx
import pytest

from src.exceptions import AppError, ServiceUnavailableError
from src.services.api_client import ApiClient


def _client_with_handler(handler) -> ApiClient:
    """Build an ApiClient whose lazy httpx client uses *handler*."""
    api = ApiClient("http://vie-api:3000", "secret")
    api._client = httpx.AsyncClient(  # noqa: SLF001 — test wiring
        base_url="http://vie-api:3000",
        transport=httpx.MockTransport(handler),
    )
    return api


class TestApiClient:
    """ApiClient sends internal-auth headers and unwraps {success,data}."""

    async def test_should_send_internal_headers_and_unwrap_data(self) -> None:
        # Arrange
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["secret"] = request.headers.get("X-Internal-Secret")
            seen["user"] = request.headers.get("X-User-Id")
            seen["path"] = request.url.path
            return httpx.Response(200, json={"success": True, "data": [{"id": "f1"}]})

        api = _client_with_handler(handler)

        # Act
        result = await api.list_folders("user42")

        # Assert
        assert result == [{"id": "f1"}]
        assert seen["secret"] == "secret"
        assert seen["user"] == "user42"
        assert seen["path"] == "/internal/assistant/folders"
        await api.aclose()

    async def test_should_forward_the_chat_request_and_session_ids(self) -> None:
        from llm_common.context import usage_scope

        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["request"] = request.headers.get("X-Request-ID")
            seen["session"] = request.headers.get("X-Session-Id")
            return httpx.Response(200, json={"success": True, "data": []})

        api = _client_with_handler(handler)

        with usage_scope(request_id="req-chat-1234", session_id="chat-session-1"):
            await api.list_folders("user42")

        assert seen == {"request": "req-chat-1234", "session": "chat-session-1"}
        await api.aclose()

    async def test_should_send_no_correlation_headers_outside_a_request(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["headers"] = {k.lower() for k in request.headers}
            return httpx.Response(200, json={"success": True, "data": []})

        api = _client_with_handler(handler)
        await api.list_folders("user42")

        assert {"x-request-id", "x-session-id"}.isdisjoint(seen["headers"])
        await api.aclose()

    async def test_should_post_create_folder_with_filtered_fields(self) -> None:
        # Arrange
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            import json as _json

            seen["body"] = _json.loads(request.content)
            return httpx.Response(200, json={"success": True, "data": {"id": "f9"}})

        api = _client_with_handler(handler)

        # Act — icon/parentId None should be dropped
        result = await api.create_folder("user42", "Recipes", color="blue", icon=None, parentId=None)

        # Assert
        assert result == {"id": "f9"}
        assert seen["body"] == {"name": "Recipes", "color": "blue"}
        await api.aclose()

    async def test_should_move_folder_under_parent(self) -> None:
        # Arrange
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            import json as _json

            seen["method"] = request.method
            seen["path"] = request.url.path
            seen["body"] = _json.loads(request.content)
            return httpx.Response(200, json={"success": True, "data": {"id": "f1"}})

        api = _client_with_handler(handler)

        # Act
        await api.move_folder("user42", "f1", "p1")

        # Assert
        assert seen["method"] == "PATCH"
        assert seen["path"] == "/internal/assistant/folders/f1"
        assert seen["body"] == {"parentId": "p1"}
        await api.aclose()

    async def test_should_move_folder_to_root_with_explicit_null(self) -> None:
        # Arrange
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            import json as _json

            seen["body"] = _json.loads(request.content)
            return httpx.Response(200, json={"success": True, "data": {"id": "f1"}})

        api = _client_with_handler(handler)

        # Act — parent_id None must reach vie-api as null, not be dropped
        await api.move_folder("user42", "f1", None)

        # Assert
        assert seen["body"] == {"parentId": None}
        await api.aclose()

    async def test_should_pass_delete_content_query(self) -> None:
        # Arrange
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["query"] = dict(request.url.params)
            return httpx.Response(200, json={"success": True, "data": {"deleted": True}})

        api = _client_with_handler(handler)

        # Act
        await api.delete_folder("user42", "f1", delete_content=True)

        # Assert
        assert seen["query"] == {"deleteContent": "true"}
        await api.aclose()

    async def test_should_raise_app_error_on_non_2xx(self) -> None:
        # Arrange
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"error": "folder not found"})

        api = _client_with_handler(handler)

        # Act & Assert
        with pytest.raises(AppError, match="folder not found"):
            await api.update_folder("user42", "missing", name="x")
        await api.aclose()

    async def test_should_raise_service_unavailable_on_transport_error(self) -> None:
        # Arrange
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        api = _client_with_handler(handler)

        # Act & Assert
        with pytest.raises(ServiceUnavailableError, match="vie-api request failed"):
            await api.list_videos("user42")
        await api.aclose()

    async def test_should_return_body_when_no_data_envelope(self) -> None:
        # Arrange — some endpoints may return a bare object
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[{"id": "v1"}])

        api = _client_with_handler(handler)

        # Act
        result = await api.list_videos("user42")

        # Assert
        assert result == [{"id": "v1"}]
        await api.aclose()
