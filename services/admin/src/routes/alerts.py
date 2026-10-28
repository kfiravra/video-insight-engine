"""Alert management endpoints.

Reading is direct. Changing the thresholds is a mutation of operator
configuration, so it goes through vie-api (``POST /api/admin/alerts/config``,
permission ``alerts:write``) as the session's admin, with a required reason:
vie-api writes the same ``llm_alert_config`` document the evaluator reads and
records an ``adminAudit`` row with the thresholds before and after.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from src.auth import AdminActor, current_user_actor
from src.dependencies import get_database
from src.services.vie_api_client import call_vie_api

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("/recent")
async def recent_alerts(limit: int = Query(20, ge=1, le=100)) -> list[dict]:
    """Recent alerts from llm_alerts collection."""
    db = get_database()
    cursor = db.llm_alerts.find().sort("timestamp", -1).limit(limit)
    results = await cursor.to_list(limit)
    for r in results:
        r["_id"] = str(r["_id"])
        if "timestamp" in r:
            r["timestamp"] = r["timestamp"].isoformat()
    return results


@router.get("/config")
async def get_alert_config() -> dict:
    """Current alert thresholds."""
    db = get_database()
    config = await db.llm_alert_config.find_one({"_id": "default"})
    if not config:
        return {
            "cost_threshold_usd": 0.50,
            "daily_spike_multiplier": 2.0,
            "failure_rate_threshold": 0.20,
        }
    config["_id"] = str(config["_id"])
    return config


class AlertConfigInput(BaseModel):
    """The three thresholds, replaced together, and why they changed."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    cost_threshold_usd: float = Field(0.50, ge=0)
    daily_spike_multiplier: float = Field(2.0, ge=1)
    failure_rate_threshold: float = Field(0.20, ge=0, le=1)
    reason: str = Field(..., min_length=1, max_length=500)


@router.post("/config")
async def update_alert_config(
    body: AlertConfigInput,
    actor: Annotated[AdminActor, Depends(current_user_actor)],
) -> dict:
    """Replace the alert thresholds as the session's admin; returns ``{before, after, auditId}``."""
    return await call_vie_api("POST", "alerts/config", actor, body=body.model_dump())
