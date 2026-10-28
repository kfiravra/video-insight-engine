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
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from src.auth import AdminActor, current_actor, current_user_actor
from src.services.vie_api_client import call_vie_api

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/queue", tags=["queue"])

_READ_TIMEOUT = httpx.Timeout(5.0, connect=2.0)
# Replay drains up to 500 messages with publisher confirms — give it room.
_REPLAY_TIMEOUT = httpx.Timeout(30.0, connect=2.0)


@router.get("/stats")
async def queue_stats(actor: Annotated[AdminActor, Depends(current_actor)]) -> dict:
    """Return main + DLQ depth and consumer count for the pipeline queue."""
    return await call_vie_api("GET", "queue/stats", actor, timeout=_READ_TIMEOUT)


@router.get("/dlq")
async def queue_dlq(
    actor: Annotated[AdminActor, Depends(current_actor)],
    limit: int = Query(20, ge=1, le=100),
) -> dict:
    """Peek at messages currently in the DLQ. Messages are not consumed."""
    return await call_vie_api(
        "GET", "queue/dlq", actor, params={"limit": limit}, timeout=_READ_TIMEOUT
    )


class ReplayRequest(BaseModel):
    """Body for POST /queue/replay — mirrors vie-api's cap (1–500, default 100)."""

    max: int = Field(100, ge=1, le=500)


@router.post("/replay")
async def queue_replay(
    body: ReplayRequest, actor: Annotated[AdminActor, Depends(current_user_actor)]
) -> dict:
    """Re-publish DLQ messages to the main queue (attempt reset to 1).

    vie-api owns the AMQP channel and does the confirm-then-ack drain; this
    proxy only exists so the UI keeps one base URL + auth model. Returns
    ``{"replayed": N}``. A timeout is a 504 saying part of the drain may have
    run: reload the DLQ depth before replaying again.
    """
    logger.info("queue_replay_requested", max=body.max, user_id=actor.user_id)
    return await call_vie_api(
        "POST", "queue/replay", actor, body={"max": body.max}, timeout=_REPLAY_TIMEOUT
    )
