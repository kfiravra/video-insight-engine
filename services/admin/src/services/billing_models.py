"""Wire shapes for the read-only billing account view.

These mirror ``AccountDetail`` and the view types in
``packages/types/src/billing.ts`` (ids as hex strings, dates as ISO 8601
strings) so the admin UI can later switch to vie-api's
``GET /api/admin/billing/accounts/:userId`` without changing. Two differences:

- Fields vie-api omits when absent (``lotId``, ``reason``, ``costUsd`` ...) are
  ``null`` here; the UI types accept both.
- ``account`` is ``null`` when the user has no ``billingAccounts`` row yet (vie-api
  creates accounts lazily and this service never writes), and ``ledgerNextBefore``
  is the cursor for the next ledger page (``null`` on the last page).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

# ``free`` appears only on minute entries written before the token migration.
LedgerBucket = Literal["plan", "credit", "debt", "free"]
LedgerEntryType = Literal[
    "grant", "hold", "debit", "release", "expire", "refund", "adjust", "repay", "convert"
]
LedgerUnit = Literal["tokens", "minutes"]
ExecutionKind = Literal["video", "chat", "library_chat", "action"]
BillingSourceKind = Literal["run", "payment", "admin", "migration", "recurring", "system"]


class BillingSource(BaseModel):
    kind: BillingSourceKind
    ref: str


class AccountCaps(BaseModel):
    """Admin overrides; ``None`` = no override."""

    maxVideoMinutes: int | None = None
    maxConcurrent: int | None = None
    blocked: bool | None = None


class BillingPlanState(BaseModel):
    id: Literal["free", "pro"]
    status: Literal["active", "past_due", "canceled"]
    source: Literal["manual", "provider"] | None
    periodStart: str | None
    periodEnd: str | None


class PlanTokensState(BaseModel):
    cycleStart: str | None
    cycleEnd: str | None
    granted: int
    used: int
    remaining: int


class CreditsState(BaseModel):
    available: int


class AccountSnapshot(BaseModel):
    accountId: str
    ownerType: Literal["user", "team"]
    ownerId: str
    plan: BillingPlanState
    planTokens: PlanTokensState
    credits: CreditsState
    # Tokens owed (>= 0); balance = plan remaining + credits - debt.
    debt: int
    balance: int
    signupGrantAt: str | None
    caps: AccountCaps
    openHolds: int
    version: int
    updatedAt: str


class LotSlice(BaseModel):
    lotId: str
    tokens: int


class BucketSplit(BaseModel):
    plan: int
    credit: int
    lots: list[LotSlice]


class HoldView(BaseModel):
    requestId: str
    userId: str
    youtubeId: str | None
    durationSec: float
    # The estimate held.
    tokens: int
    split: BucketSplit
    planCycleStart: str | None
    createdAt: str


class CreditLotView(BaseModel):
    lotId: str
    tokensGranted: int
    tokensRemaining: int
    source: BillingSource
    priceUsdCents: int | None = None
    expiresAt: str | None
    expired: bool
    createdAt: str


class LedgerEntryView(BaseModel):
    id: str
    userId: str
    type: LedgerEntryType
    bucket: LedgerBucket
    # The amount in its unit: tokens, or minutes on pre-migration history rows.
    unit: LedgerUnit
    amount: int
    mirrorsHold: bool | None = None
    lotId: str | None = None
    source: BillingSource
    idempotencyKey: str
    executionKind: ExecutionKind | None = None
    executionId: str | None = None
    youtubeId: str | None = None
    durationSec: float | None = None
    # Internal margin data (LLM $ of the run); fine for admins, never for users.
    costUsd: float | None = None
    actorId: str | None = None
    auditId: str | None = None
    reason: str | None = None
    createdAt: str


class RecurringGrantView(BaseModel):
    grantId: str
    tokens: int
    every: Literal["month"]
    active: bool
    nextRunAt: str
    lastRunAt: str | None
    actorId: str
    auditId: str
    reason: str
    createdAt: str
    updatedAt: str


class BillingAccountDetail(BaseModel):
    """``AccountDetail`` plus the ledger cursor; ``account`` is None until vie-api creates it."""

    account: AccountSnapshot | None
    lots: list[CreditLotView]
    holds: list[HoldView]
    # Newest first.
    ledger: list[LedgerEntryView]
    recurringGrants: list[RecurringGrantView]
    ledgerNextBefore: str | None
