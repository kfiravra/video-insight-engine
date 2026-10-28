"""Read-only billing account view (minutes ledger).

vie-api's ``BillingService`` is the only writer of the billing collections; this
router never writes. See docs/DATA-MODELS.md ("Billing Collections").
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Annotated

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Path, Query
from motor.motor_asyncio import AsyncIOMotorDatabase

from src.dependencies import get_database
from src.services.billing_account import BillingAccountService, UserNotFoundError
from src.services.billing_models import BillingAccountDetail
from src.services.billing_reader import BillingReader

router = APIRouter(prefix="/billing", tags=["billing"])

DEFAULT_LEDGER_LIMIT = 50
MAX_LEDGER_LIMIT = 200

_OBJECT_ID_PATTERN = re.compile(r"^[0-9a-fA-F]{24}$")


def get_billing_service(
    db: Annotated[AsyncIOMotorDatabase, Depends(get_database)],
) -> BillingAccountService:
    return BillingAccountService(BillingReader(db))


def _parse_user_id(value: str) -> ObjectId:
    if not _OBJECT_ID_PATTERN.match(value):
        raise HTTPException(status_code=400, detail="Invalid id format")
    return ObjectId(value)


def _parse_before(value: str | None) -> datetime | None:
    """The ``before`` cursor: an ISO 8601 timestamp (naive means UTC)."""
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid before cursor") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


@router.get("/accounts/{user_id}", response_model=BillingAccountDetail)
async def get_billing_account(
    user_id: Annotated[str, Path(description="users._id (24-character hex)")],
    service: Annotated[BillingAccountService, Depends(get_billing_service)],
    limit: Annotated[int, Query(ge=1, le=MAX_LEDGER_LIMIT)] = DEFAULT_LEDGER_LIMIT,
    before: Annotated[
        str | None, Query(max_length=40, description="ISO 8601 createdAt cursor")
    ] = None,
) -> BillingAccountDetail:
    """A user's minutes account: plan, buckets, caps, holds, lots, grants, ledger page.

    ``account`` is null for a user with no billing row yet (vie-api creates it on
    the first video). Page the ledger by passing the previous page's
    ``ledgerNextBefore`` as ``before``.
    """
    user_oid = _parse_user_id(user_id)
    cursor = _parse_before(before)
    try:
        return await service.get_detail(user_oid, ledger_limit=limit, ledger_before=cursor)
    except UserNotFoundError as exc:
        raise HTTPException(status_code=404, detail="User not found") from exc
