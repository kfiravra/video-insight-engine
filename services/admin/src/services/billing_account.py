"""Builds the admin's read-only view of a user's billing account.

Maps the documents vie-api's ``BillingService`` writes into the wire shapes in
``billing_models``. The snapshot arithmetic (plan tokens left, spendable
credit, debt, balance) mirrors ``toSnapshot`` in
``api/src/services/billing/views.ts``. An account written before the token
migration reads as an empty token account, as it does in vie-api; minute
ledger rows show in their own unit.

The view is the stored state as of now. Unlike vie-api it does not roll an
ended Pro cycle forward in memory, so a cycle that ended with no request since
shows its last totals and 0 remaining until the user's next request rolls it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
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
    HoldView,
    LedgerEntryView,
    LotSlice,
    PlanTokensState,
    RecurringGrantView,
)
from src.services.billing_reader import BillingReader

Document = Mapping[str, Any]


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


def _plan_tokens_state(account: Document, now: datetime) -> PlanTokensState:
    bucket = account["buckets"].get("plan") or {}
    granted, used = bucket.get("tokensGranted", 0), bucket.get("tokensUsed", 0)
    cycle_end = bucket.get("cycleEnd")
    running = account["plan"]["id"] == "pro" and cycle_end and _as_utc(cycle_end) > now
    return PlanTokensState(
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
    """Tokens the credit lots can pay now: unexpired lots only."""
    return sum(
        lot["tokensRemaining"]
        for lot in open_lots
        if lot.get("tokensRemaining", 0) > 0 and not is_lot_expired(lot, now)
    )


def build_snapshot(
    account: Document, open_lots: Sequence[Document], now: datetime
) -> AccountSnapshot:
    caps = account.get("caps") or {}
    plan = account["plan"]
    plan_tokens = _plan_tokens_state(account, now)
    credits = spendable_credit(open_lots, now)
    debt = (account["buckets"].get("debt") or {}).get("tokens", 0)
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
        planTokens=plan_tokens,
        credits=CreditsState(available=credits),
        debt=debt,
        balance=plan_tokens.remaining + credits - debt,
        signupGrantAt=to_iso(account.get("signupGrantAt")),
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
    """A hold; one from before the token migration (minutes) shows as a 0-token hold."""
    tokens = hold.get("tokens")
    split = hold["split"] if isinstance(tokens, int) else {}
    return HoldView(
        requestId=hold["requestId"],
        userId=str(hold["userId"]),
        youtubeId=hold.get("youtubeId"),
        durationSec=hold.get("durationSec", 0),
        tokens=tokens if isinstance(tokens, int) else 0,
        split=BucketSplit(
            plan=split.get("plan", 0),
            credit=split.get("credit", 0),
            lots=[
                LotSlice(lotId=str(s["lotId"]), tokens=s["tokens"]) for s in split.get("lots", [])
            ],
        ),
        planCycleStart=to_iso(hold.get("planCycleStart")),
        createdAt=_iso(hold["createdAt"]),
    )


def build_lot(lot: Document, now: datetime) -> CreditLotView:
    return CreditLotView(
        lotId=str(lot["_id"]),
        tokensGranted=lot["tokensGranted"],
        tokensRemaining=lot["tokensRemaining"],
        source=_source(lot["source"]),
        priceUsdCents=lot.get("priceUsdCents"),
        expiresAt=to_iso(lot.get("expiresAt")),
        expired=is_lot_expired(lot, now),
        createdAt=_iso(lot["createdAt"]),
    )


def build_ledger_entry(entry: Document) -> LedgerEntryView:
    """A ledger row in its own unit: tokens, or minutes on pre-migration history."""
    in_tokens = entry.get("unit") == "tokens"
    return LedgerEntryView(
        id=str(entry["_id"]),
        userId=str(entry["userId"]),
        type=entry["type"],
        bucket=entry["bucket"],
        unit="tokens" if in_tokens else "minutes",
        amount=entry.get("tokens" if in_tokens else "minutes", 0),
        mirrorsHold=entry.get("mirrorsHold"),
        lotId=_opt_id(entry.get("lotId")),
        source=_source(entry["source"]),
        idempotencyKey=entry["idempotencyKey"],
        executionKind=entry.get("executionKind"),
        executionId=entry.get("executionId"),
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
        tokens=grant["tokens"],
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
