"""Builds the admin's read-only view of a user's billing account.

Maps the documents vie-api's ``BillingService`` writes into the wire shapes in
``billing_models``. The snapshot arithmetic (rolling free window, plan minutes
left, spendable credit) mirrors ``toSnapshot`` in
``api/src/services/billing/views.ts``; the constants mirror ``FREE_VIDEO`` in
``packages/types/src/billing.ts`` and a test fails if either drifts.

The view is the stored state as of now. Unlike vie-api it does not roll an
ended Pro cycle forward in memory, so a cycle that ended with no request since
shows its last totals and 0 remaining until the user's next request rolls it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from bson import ObjectId

from src.services.billing_models import (
    AccountCaps,
    AccountSnapshot,
    BillingAccountDetail,
    BillingPlanState,
    BillingSource,
    BucketSplit,
    CreditLotView,
    CreditsState,
    FreeVideoState,
    HoldView,
    LedgerEntryView,
    LotSlice,
    PlanMinutesState,
    RecurringGrantView,
)
from src.services.billing_reader import BillingReader

Document = Mapping[str, Any]

# ─── Constants (mirror FREE_VIDEO in packages/types/src/billing.ts) ───

FREE_VIDEOS_PER_WINDOW = 1
FREE_WINDOW_HOURS = 24
FREE_MAX_VIDEO_MINUTES = 30

_FREE_WINDOW = timedelta(hours=FREE_WINDOW_HOURS)


class UserNotFoundError(Exception):
    """The requested user does not exist."""


# ─── Time helpers ───


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    """Mongo hands back naive datetimes that are UTC by convention."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _iso(value: datetime) -> str:
    """ISO 8601 with milliseconds and ``Z``, the format ``Date#toISOString`` produces."""
    utc = _as_utc(value)
    return f"{utc:%Y-%m-%dT%H:%M:%S}.{utc.microsecond // 1000:03d}Z"


def to_iso(value: datetime | None) -> str | None:
    """``_iso`` for a field that may be unset."""
    return None if value is None else _iso(value)


# ─── Snapshot ───


def _free_state(recent: Sequence[datetime], caps: Document, now: datetime) -> FreeVideoState:
    """Rolling window (Q3): a free video counts while it is under 24 h old."""
    configured = caps.get("freeVideosPerDay")
    per_window = FREE_VIDEOS_PER_WINDOW if configured is None else configured
    cutoff = now - _FREE_WINDOW
    in_window = sorted(at for at in map(_as_utc, recent) if at > cutoff)
    available = len(in_window) < per_window
    next_free_at = None
    if per_window > 0 and not available:
        next_free_at = in_window[len(in_window) - per_window] + _FREE_WINDOW
    max_minutes = FREE_MAX_VIDEO_MINUTES
    if caps.get("maxVideoMinutes") is not None:
        max_minutes = min(max_minutes, caps["maxVideoMinutes"])
    return FreeVideoState(
        perWindow=per_window,
        usedInWindow=len(in_window),
        available=available,
        nextFreeAt=to_iso(next_free_at),
        maxVideoMinutes=max_minutes,
    )


def _plan_minutes_state(account: Document, now: datetime) -> PlanMinutesState:
    bucket = account["buckets"]["plan"]
    granted, used = bucket["minutesGranted"], bucket["minutesUsed"]
    cycle_end = bucket.get("cycleEnd")
    running = account["plan"]["id"] == "pro" and cycle_end and _as_utc(cycle_end) > now
    return PlanMinutesState(
        cycleStart=to_iso(bucket.get("cycleStart")),
        cycleEnd=to_iso(cycle_end),
        granted=granted,
        used=used,
        remaining=max(granted - used, 0) if running else 0,
    )


def is_lot_expired(lot: Document, now: datetime) -> bool:
    expires_at = lot.get("expiresAt")
    return expires_at is not None and _as_utc(expires_at) <= now


def spendable_credit(open_lots: Sequence[Document], now: datetime) -> int:
    """Minutes the credit lots can pay now: unexpired lots only."""
    return sum(
        lot["minutesRemaining"]
        for lot in open_lots
        if lot["minutesRemaining"] > 0 and not is_lot_expired(lot, now)
    )


def build_snapshot(
    account: Document, open_lots: Sequence[Document], now: datetime
) -> AccountSnapshot:
    caps = account.get("caps") or {}
    plan = account["plan"]
    return AccountSnapshot(
        accountId=str(account["_id"]),
        ownerType=account["ownerType"],
        ownerId=str(account["ownerId"]),
        plan=BillingPlanState(
            id=plan["id"],
            status=plan["status"],
            source=plan.get("source"),
            periodStart=to_iso(plan.get("periodStart")),
            periodEnd=to_iso(plan.get("periodEnd")),
        ),
        free=_free_state(account["buckets"]["free"].get("recentFreeVideoAt") or [], caps, now),
        planMinutes=_plan_minutes_state(account, now),
        credits=CreditsState(available=spendable_credit(open_lots, now)),
        caps=AccountCaps(**{k: caps.get(k) for k in AccountCaps.model_fields}),
        openHolds=len(account.get("holds") or []),
        version=account["version"],
        updatedAt=_iso(account["updatedAt"]),
    )


# ─── Row views ───


def _source(raw: Document) -> BillingSource:
    return BillingSource(kind=raw["kind"], ref=raw["ref"])


def _opt_id(value: ObjectId | None) -> str | None:
    return None if value is None else str(value)


def build_hold(hold: Document) -> HoldView:
    split = hold["split"]
    return HoldView(
        requestId=hold["requestId"],
        userId=str(hold["userId"]),
        youtubeId=hold.get("youtubeId"),
        durationSec=hold["durationSec"],
        minutes=hold["minutes"],
        split=BucketSplit(
            free=split["free"],
            plan=split["plan"],
            credit=split["credit"],
            lots=[
                LotSlice(lotId=str(s["lotId"]), minutes=s["minutes"]) for s in split.get("lots", [])
            ],
        ),
        freeVideoAt=to_iso(hold.get("freeVideoAt")),
        planCycleStart=to_iso(hold.get("planCycleStart")),
        createdAt=_iso(hold["createdAt"]),
    )


def build_lot(lot: Document, now: datetime) -> CreditLotView:
    return CreditLotView(
        lotId=str(lot["_id"]),
        minutesGranted=lot["minutesGranted"],
        minutesRemaining=lot["minutesRemaining"],
        source=_source(lot["source"]),
        priceUsdCents=lot.get("priceUsdCents"),
        expiresAt=to_iso(lot.get("expiresAt")),
        expired=is_lot_expired(lot, now),
        createdAt=_iso(lot["createdAt"]),
    )


def build_ledger_entry(entry: Document) -> LedgerEntryView:
    return LedgerEntryView(
        id=str(entry["_id"]),
        userId=str(entry["userId"]),
        type=entry["type"],
        bucket=entry["bucket"],
        minutes=entry["minutes"],
        lotId=_opt_id(entry.get("lotId")),
        source=_source(entry["source"]),
        idempotencyKey=entry["idempotencyKey"],
        youtubeId=entry.get("youtubeId"),
        durationSec=entry.get("durationSec"),
        costUsd=entry.get("costUsd"),
        actorId=entry.get("actorId"),
        auditId=_opt_id(entry.get("auditId")),
        reason=entry.get("reason"),
        createdAt=_iso(entry["createdAt"]),
    )


def build_recurring_grant(grant: Document) -> RecurringGrantView:
    return RecurringGrantView(
        grantId=str(grant["_id"]),
        minutes=grant["minutes"],
        every=grant["every"],
        active=grant["active"],
        nextRunAt=_iso(grant["nextRunAt"]),
        lastRunAt=to_iso(grant.get("lastRunAt")),
        actorId=grant["actorId"],
        auditId=str(grant["auditId"]),
        reason=grant["reason"],
        createdAt=_iso(grant["createdAt"]),
        updatedAt=_iso(grant["updatedAt"]),
    )


# ─── Service ───


class BillingAccountService:
    """Assembles a user's account, lots, holds, recurring grants and a ledger page."""

    def __init__(self, reader: BillingReader, clock: Callable[[], datetime] = _utc_now) -> None:
        self._reader = reader
        self._clock = clock

    async def get_detail(
        self, user_id: ObjectId, *, ledger_limit: int, ledger_before: datetime | None
    ) -> BillingAccountDetail:
        """The account view; ``account`` is None for a user with no billing row yet."""
        if not await self._reader.user_exists(user_id):
            raise UserNotFoundError(str(user_id))
        account = await self._reader.find_account(user_id)
        if account is None:
            return BillingAccountDetail(
                account=None,
                lots=[],
                holds=[],
                ledger=[],
                recurringGrants=[],
                ledgerNextBefore=None,
            )
        return await self._detail_for(account, ledger_limit, ledger_before)

    async def _detail_for(
        self, account: Document, ledger_limit: int, ledger_before: datetime | None
    ) -> BillingAccountDetail:
        now = self._clock()
        account_id = account["_id"]
        open_lots, recent_lots, page, grants = await asyncio.gather(
            self._reader.open_lots(account_id),
            self._reader.recent_lots(account_id),
            self._reader.ledger_page(account_id, limit=ledger_limit, before=ledger_before),
            self._reader.recurring_grants(account_id),
        )
        return BillingAccountDetail(
            account=build_snapshot(account, open_lots, now),
            lots=[build_lot(lot, now) for lot in recent_lots],
            holds=[build_hold(hold) for hold in account.get("holds") or []],
            ledger=[build_ledger_entry(entry) for entry in page.entries],
            recurringGrants=[build_recurring_grant(grant) for grant in grants],
            ledgerNextBefore=to_iso(page.next_before),
        )
