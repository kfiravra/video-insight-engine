"""Outbound HTTP client for vie-api internal assistant endpoints.

The assistant's action tools (folder management, library generation) cannot
touch MongoDB directly — folder/video ownership and cost-reservation logic
lives in vie-api. This client calls the ``/internal/assistant/*`` endpoints,
authenticating with ``X-Internal-Secret`` and scoping every request to a user
via ``X-User-Id``. Responses follow the ``{success, data}`` envelope; this
client unwraps to ``data`` and raises a domain error on any non-2xx.

Mirrors :mod:`src.services.llm_provider`: a single configured client, specific
exception handling, structured logging, and domain errors (never HTTP types).
"""

from __future__ import annotations

from typing import Any

import httpx

from llm_common.context import llm_request_id_var, llm_session_id_var
from src.exceptions import AppError, ServiceUnavailableError, ValidationError
from src.logging_config import get_logger

logger = get_logger(__name__)


class ApiClient:
    """Thin async client over vie-api ``/internal/assistant/*`` endpoints.

    All methods scope to ``user_id`` (forwarded as ``X-User-Id``) and trust
    vie-api to enforce ownership at the data layer. The underlying
    ``httpx.AsyncClient`` is created lazily on first use and reused across
    calls; close it with :meth:`aclose` during application shutdown.
    """

    def __init__(
        self,
        base_url: str,
        internal_secret: str,
        timeout: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._internal_secret = internal_secret
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        """Return the shared client, creating it on first use."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
            )
        return self._client

    def _headers(self, user_id: str) -> dict[str, str]:
        """Build internal-auth headers scoped to *user_id*.

        The chat's request and session ids ride along (D8), so a video the
        agent generates is logged under the same request and its pipeline run
        carries ``chatSession:<id>``.
        """
        headers = {
            "X-Internal-Secret": self._internal_secret,
            "X-User-Id": user_id,
        }
        request_id = llm_request_id_var.get()
        if request_id:
            headers["X-Request-ID"] = request_id
        session_id = llm_session_id_var.get()
        if session_id:
            headers["X-Session-Id"] = session_id
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        user_id: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Send a request and unwrap the ``{success, data}`` envelope.

        Raises:
            ServiceUnavailableError: On transport failure / timeout.
            AppError: On any non-2xx response.
        """
        client = self._get_client()
        try:
            response = await client.request(
                method,
                path,
                headers=self._headers(user_id),
                json=json,
                params=params,
            )
        except httpx.HTTPError as exc:
            logger.error(
                "api_client_request_failed",
                method=method,
                path=path,
                user_id=user_id,
                error=str(exc),
            )
            raise ServiceUnavailableError(
                f"vie-api request failed: {exc}"
            ) from exc

        return self._unwrap(response, method, path, user_id)

    def _unwrap(
        self,
        response: httpx.Response,
        method: str,
        path: str,
        user_id: str,
    ) -> Any:
        """Validate the status code and unwrap ``data`` from the envelope."""
        if response.status_code >= 400:
            message = _extract_error_message(response)
            logger.error(
                "api_client_response_error",
                method=method,
                path=path,
                user_id=user_id,
                status_code=response.status_code,
                error=message,
            )
            raise AppError(
                f"vie-api {path} failed: {message}",
                status_code=502,
                code="UPSTREAM_ERROR",
            )

        body = _safe_json(response)
        if isinstance(body, dict) and "data" in body:
            return body["data"]
        return body

    # ─── Folders ────────────────────────────────────────────────────────

    async def list_folders(self, user_id: str) -> Any:
        """GET /internal/assistant/folders — the user's folders."""
        return await self._request("GET", "/internal/assistant/folders", user_id)

    async def create_folder(self, user_id: str, name: str, **fields: Any) -> Any:
        """POST /internal/assistant/folders — create a folder.

        Optional *fields*: ``color``, ``icon``, ``parentId``.
        """
        payload = {"name": name, **{k: v for k, v in fields.items() if v is not None}}
        return await self._request(
            "POST", "/internal/assistant/folders", user_id, json=payload
        )

    async def update_folder(self, user_id: str, folder_id: str, **fields: Any) -> Any:
        """PATCH /internal/assistant/folders/:id — rename/move/restyle."""
        payload = {k: v for k, v in fields.items() if v is not None}
        return await self._request(
            "PATCH",
            f"/internal/assistant/folders/{folder_id}",
            user_id,
            json=payload,
        )

    async def move_folder(
        self,
        user_id: str,
        folder_id: str,
        parent_id: str | None,
    ) -> Any:
        """PATCH /internal/assistant/folders/:id — move under a parent or to root.

        Unlike :meth:`update_folder`, ``parentId`` is sent unconditionally:
        ``None`` is forwarded as an explicit JSON ``null`` so vie-api moves the
        folder to the top level. (An absent ``parentId`` would instead leave the
        parent unchanged — which is why move can't reuse the None-stripping
        :meth:`update_folder`.)
        """
        return await self._request(
            "PATCH",
            f"/internal/assistant/folders/{folder_id}",
            user_id,
            json={"parentId": parent_id},
        )

    async def delete_folder(
        self,
        user_id: str,
        folder_id: str,
        delete_content: bool = False,
    ) -> Any:
        """DELETE /internal/assistant/folders/:id — delete a folder.

        ``delete_content`` maps to the ``deleteContent`` query flag.
        """
        return await self._request(
            "DELETE",
            f"/internal/assistant/folders/{folder_id}",
            user_id,
            params={"deleteContent": str(delete_content).lower()},
        )

    # ─── Videos ─────────────────────────────────────────────────────────

    async def list_videos(self, user_id: str) -> Any:
        """GET /internal/assistant/videos — the user's videos."""
        return await self._request("GET", "/internal/assistant/videos", user_id)

    async def move_video(self, user_id: str, video_id: str, folder_id: str) -> Any:
        """PATCH /internal/assistant/videos/:id/move — move into a folder."""
        return await self._request(
            "PATCH",
            f"/internal/assistant/videos/{video_id}/move",
            user_id,
            json={"folderId": folder_id},
        )

    async def generate_video(
        self,
        user_id: str,
        url: str,
        folder_id: str | None = None,
    ) -> Any:
        """POST /internal/assistant/generate — start a video generation.

        Routes through vie-api's ``videoService.createVideo`` so cost
        reservation and the dispatch guard are honoured.
        """
        payload: dict[str, Any] = {"url": url}
        if folder_id is not None:
            payload["folderId"] = folder_id
        return await self._request(
            "POST", "/internal/assistant/generate", user_id, json=payload
        )

    async def aclose(self) -> None:
        """Close the underlying HTTP client (call on shutdown)."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None


def _safe_json(response: httpx.Response) -> Any:
    """Parse a JSON body, raising ValidationError on malformed payloads."""
    try:
        return response.json()
    except ValueError as exc:
        raise ValidationError(f"vie-api returned non-JSON body: {exc}") from exc


def _extract_error_message(response: httpx.Response) -> str:
    """Pull a human-readable error out of an error response body."""
    try:
        body = response.json()
    except ValueError:
        return response.text[:200] or f"HTTP {response.status_code}"
    if isinstance(body, dict):
        return str(body.get("error") or body.get("message") or body)
    return str(body)
