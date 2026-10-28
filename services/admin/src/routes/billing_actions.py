"""Billing actions and the audit log — proxies to vie-api's admin billing routes.

vie-api (``BillingService``) is the only writer of the minutes ledger and of
``adminAudit``. These routes validate the request shape, then forward it with
the signed ``X-Admin-Actor`` of the session's admin, so the ledger entries and
the audit row name the real admin and the reason, never a body field. Only a
named admin session may change anything (the machine key gets 403 from the
middleware); the audit log may be read with either credential.

Granting credit minutes lives next to the user's other views, at
``POST /users/{id}/grant-credit`` (``routes/users.py``). The read-only account
view is ``routes/billing.py``.

Send one ``Idempotency-Key`` per form submission: vie-api derives the audit id
from it, so a retry after a timeout applies once.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Path, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.auth import AdminActor, current_actor, current_user_actor
from src.services.vie_api_client import (
    IDEMPOTENCY_KEY_PATTERN,
    OBJECT_ID_PATTERN,
    call_vie_api,
)

router = APIRouter(tags=["billing-actions"])

MAX_MINUTES = 100_000

UserId = Annotated[str, Path(pattern=OBJECT_ID_PATTERN, description="users._id (24-character hex)")]
GrantId = Annotated[str, Path(pattern=OBJECT_ID_PATTERN)]
NamedAdmin = Annotated[AdminActor, Depends(current_user_actor)]
IdempotencyKey = Annotated[
    str | None, Header(alias="Idempotency-Key", pattern=IDEMPOTENCY_KEY_PATTERN)
]


class _Mutation(BaseModel):
    """Every mutation says why; unknown fields (such as an ``adminId``) are rejected."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(..., min_length=1, max_length=500)


class SetPlanRequest(_Mutation):
    planId: Literal["free", "pro"]
    periodEnd: datetime | None = Field(
        None, description="End of a manual Pro term (ISO 8601 with offset); default 12 months"
    )


class SetCapsRequest(_Mutation):
    """A cap that is present is set; an explicit ``null`` clears it; an absent one is left alone."""

    maxVideoMinutes: int | None = None
    freeVideosPerDay: int | None = None
    maxConcurrent: int | None = None
    blocked: bool | None = None


class AdjustRequest(_Mutation):
    bucket: Literal["plan", "credit"]
    minutes: int = Field(..., ge=-MAX_MINUTES, le=MAX_MINUTES, description="Signed, not 0")

    @field_validator("minutes")
    @classmethod
    def _not_zero(cls, value: int) -> int:
        if value == 0:
            raise ValueError("minutes must not be 0")
        return value


class CreateRecurringGrantRequest(_Mutation):
    minutes: int = Field(..., ge=1, le=MAX_MINUTES)
    every: Literal["month"]
    active: bool


class DeleteRecurringGrantRequest(_Mutation):
    pass


async def _forward(
    method: str,
    path: str,
    body: _Mutation,
    actor: AdminActor,
    idempotency_key: str | None,
    *,
    keep_explicit_nulls: bool = False,
) -> dict:
    """Send the validated body on; null caps are only meaningful for the caps route."""
    payload = body.model_dump(
        mode="json", exclude_unset=keep_explicit_nulls, exclude_none=not keep_explicit_nulls
    )
    return await call_vie_api(method, path, actor, body=payload, idempotency_key=idempotency_key)


@router.post("/billing/accounts/{user_id}/plan")
async def set_plan(
    user_id: UserId,
    body: SetPlanRequest,
    actor: NamedAdmin,
    idempotency_key: IdempotencyKey = None,
) -> dict:
    """Set a manual plan: Pro grants 600 minutes now for 12 months; free stops plan minutes."""
    path = f"billing/accounts/{user_id}/plan"
    return await _forward("POST", path, body, actor, idempotency_key)


@router.post("/billing/accounts/{user_id}/caps")
async def set_caps(
    user_id: UserId,
    body: SetCapsRequest,
    actor: NamedAdmin,
    idempotency_key: IdempotencyKey = None,
) -> dict:
    """Set, clear (``null``) or leave per-account caps, including the ``blocked`` switch."""
    path = f"billing/accounts/{user_id}/caps"
    return await _forward("POST", path, body, actor, idempotency_key, keep_explicit_nulls=True)


@router.post("/billing/accounts/{user_id}/adjust")
async def adjust_balance(
    user_id: UserId,
    body: AdjustRequest,
    actor: NamedAdmin,
    idempotency_key: IdempotencyKey = None,
) -> dict:
    """A signed correction to the plan or credit minutes; never below zero."""
    path = f"billing/accounts/{user_id}/adjust"
    return await _forward("POST", path, body, actor, idempotency_key)


@router.post("/billing/accounts/{user_id}/recurring-grants")
async def create_recurring_grant(
    user_id: UserId,
    body: CreateRecurringGrantRequest,
    actor: NamedAdmin,
    idempotency_key: IdempotencyKey = None,
) -> dict:
    """A monthly credit grant (demo and eval accounts); applied by vie-api's repair job."""
    path = f"billing/accounts/{user_id}/recurring-grants"
    return await _forward("POST", path, body, actor, idempotency_key)


@router.delete("/billing/accounts/{user_id}/recurring-grants/{grant_id}")
async def delete_recurring_grant(
    user_id: UserId,
    grant_id: GrantId,
    body: DeleteRecurringGrantRequest,
    actor: NamedAdmin,
    idempotency_key: IdempotencyKey = None,
) -> dict:
    """Stop a recurring grant; its past lots stay."""
    path = f"billing/accounts/{user_id}/recurring-grants/{grant_id}"
    return await _forward("DELETE", path, body, actor, idempotency_key)


@router.get("/audit")
async def list_audit(
    actor: Annotated[AdminActor, Depends(current_actor)],
    target_id: Annotated[str | None, Query(alias="targetId", min_length=1, max_length=200)] = None,
    actor_id: Annotated[str | None, Query(alias="actorId", min_length=1, max_length=200)] = None,
    limit: Annotated[int | None, Query(ge=1, le=200)] = None,
    before: Annotated[str | None, Query(max_length=40, description="ISO 8601 cursor")] = None,
) -> dict:
    """The admin action log, newest first; a user's history is ``?targetId=<userId>``.

    Returns vie-api's ``{entries, nextBefore}``; pass ``nextBefore`` as ``before`` to page.
    """
    params = {"targetId": target_id, "actorId": actor_id, "limit": limit, "before": before}
    return await call_vie_api(
        "GET", "audit", actor, params={k: v for k, v in params.items() if v is not None}
    )
