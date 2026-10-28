"""Queue observability endpoints — proxy to vie-api's admin queue routes.

vie-api is the source of truth for RabbitMQ topology and the management-API
auth. The admin service forwards calls so the UI keeps a single base URL and
auth model. Each forwarded call names the admin who made it
(``X-Admin-Actor``, see ``vie_api_client``); the replay is a mutation, so only
a named admin session may trigger it.
"""

from __future__ import annotations

from typing import Annotated

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src.auth import AdminActor, current_actor, current_user_actor
from src.config import settings
from src.services.vie_api_client import admin_headers, encode_json_body, translate_upstream

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/queue", tags=["queue"])

_DEFAULT_TIMEOUT = httpx.Timeout(5.0, connect=2.0)


def _upstream_url(path: str) -> str:
    return f"{settings.VIE_API_URL.rstrip('/')}/api/admin/queue/{path.lstrip('/')}"


async def _proxy_get(path: str, actor: AdminActor, params: dict | None = None) -> dict:
    try:
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
            resp = await client.get(
                _upstream_url(path), headers=admin_headers(actor), params=params or {}
            )
    except httpx.RequestError as exc:
        logger.warning("queue_proxy_unreachable", path=path, error=str(exc))
        raise HTTPException(status_code=502, detail="vie-api unreachable") from exc
    return translate_upstream(resp)


async def _proxy_post(path: str, body: dict, actor: AdminActor, timeout: httpx.Timeout) -> dict:
    # The signature covers these exact bytes, so they are what goes on the wire.
    content = encode_json_body(body)
    headers = {**admin_headers(actor, content), "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(_upstream_url(path), headers=headers, content=content)
    except httpx.RequestError as exc:
        logger.warning("queue_proxy_unreachable", path=path, error=str(exc))
        raise HTTPException(status_code=502, detail="vie-api unreachable") from exc
    return translate_upstream(resp)


@router.get("/stats")
async def queue_stats(actor: Annotated[AdminActor, Depends(current_actor)]) -> dict:
    """Return main + DLQ depth and consumer count for the pipeline queue."""
    return await _proxy_get("stats", actor)


@router.get("/dlq")
async def queue_dlq(
    actor: Annotated[AdminActor, Depends(current_actor)],
    limit: int = Query(20, ge=1, le=100),
) -> dict:
    """Peek at messages currently in the DLQ. Messages are not consumed."""
    return await _proxy_get("dlq", actor, {"limit": limit})


class ReplayRequest(BaseModel):
    """Body for POST /queue/replay — mirrors vie-api's cap (1–500, default 100)."""

    max: int = Field(100, ge=1, le=500)


# Replay drains up to 500 messages with publisher confirms — give it room.
_REPLAY_TIMEOUT = httpx.Timeout(30.0, connect=2.0)


@router.post("/replay")
async def queue_replay(
    body: ReplayRequest, actor: Annotated[AdminActor, Depends(current_user_actor)]
) -> dict:
    """Re-publish DLQ messages to the main queue (attempt reset to 1).

    vie-api owns the AMQP channel and does the confirm-then-ack drain; this
    proxy only exists so the UI keeps one base URL + auth model. Returns
    ``{"replayed": N}``.
    """
    logger.info("queue_replay_requested", max=body.max, user_id=actor.user_id)
    return await _proxy_post("replay", {"max": body.max}, actor, _REPLAY_TIMEOUT)
