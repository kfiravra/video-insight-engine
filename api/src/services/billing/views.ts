import { SIGNUP_GRANT_TOKENS } from '@vie/types';
import { maxVideoMinutes, spendableCredit, spendableLots } from './allocation.js';
import { planAvailable } from './plan-cycle.js';
import type {
  AccountSnapshot,
  BucketSplit,
  CreditLotView,
  HoldView,
  LedgerEntryView,
  RecurringGrantView,
  UsageSummary,
} from '@vie/types';
import type { AllocationLot } from './allocation.js';
import type {
  BillingAccountDocument,
  BillingHoldDocument,
  BucketSplitDocument,
  CreditLotDocument,
  LedgerEntryDocument,
  RecurringGrantDocument,
} from '../../repositories/billing.repository.js';

// Stored documents (ObjectId / Date) → plain-JSON views (hex / ISO strings).

function iso(date: Date | null | undefined): string | null {
  return date ? date.toISOString() : null;
}

export function toAllocationLot(lot: CreditLotDocument): AllocationLot {
  return {
    lotId: lot._id.toHexString(),
    tokensRemaining: lot.tokensRemaining,
    expiresAt: lot.expiresAt,
    createdAt: lot.createdAt,
  };
}

export function toSplitView(split: BucketSplitDocument): BucketSplit {
  return {
    plan: split.plan,
    credit: split.credit,
    lots: split.lots.map((slice) => ({ lotId: slice.lotId.toHexString(), tokens: slice.tokens })),
  };
}

export function toHoldView(hold: BillingHoldDocument): HoldView {
  return {
    requestId: hold.requestId,
    userId: hold.userId.toHexString(),
    youtubeId: hold.youtubeId,
    durationSec: hold.durationSec,
    tokens: hold.tokens,
    split: toSplitView(hold.split),
    planCycleStart: iso(hold.planCycleStart),
    createdAt: hold.createdAt.toISOString(),
  };
}

export function toLotView(lot: CreditLotDocument, now: Date): CreditLotView {
  const expired = lot.expiresAt !== null && lot.expiresAt.getTime() <= now.getTime();
  return {
    lotId: lot._id.toHexString(),
    tokensGranted: lot.tokensGranted,
    tokensRemaining: lot.tokensRemaining,
    source: lot.source,
    ...(lot.priceUsdCents !== undefined && { priceUsdCents: lot.priceUsdCents }),
    expiresAt: iso(lot.expiresAt),
    expired,
    createdAt: lot.createdAt.toISOString(),
  };
}

/** A ledger row in its own unit: token entries, or minute history from before the migration. */
export function toLedgerView(entry: LedgerEntryDocument): LedgerEntryView {
  const unit = entry.unit === 'tokens' ? 'tokens' : 'minutes';
  return {
    id: entry._id.toHexString(),
    userId: entry.userId.toHexString(),
    type: entry.type,
    bucket: entry.bucket,
    unit,
    amount: (unit === 'tokens' ? entry.tokens : entry.minutes) ?? 0,
    ...(entry.mirrorsHold && { mirrorsHold: true }),
    ...(entry.lotId && { lotId: entry.lotId.toHexString() }),
    source: entry.source,
    idempotencyKey: entry.idempotencyKey,
    ...(entry.executionKind !== undefined && { executionKind: entry.executionKind }),
    ...(entry.executionId !== undefined && { executionId: entry.executionId }),
    ...(entry.youtubeId !== undefined && { youtubeId: entry.youtubeId }),
    ...(entry.durationSec !== undefined && { durationSec: entry.durationSec }),
    ...(entry.costUsd !== undefined && { costUsd: entry.costUsd }),
    ...(entry.actorId !== undefined && { actorId: entry.actorId }),
    ...(entry.auditId && { auditId: entry.auditId.toHexString() }),
    ...(entry.reason !== undefined && { reason: entry.reason }),
    createdAt: entry.createdAt.toISOString(),
  };
}

export function toRecurringGrantView(grant: RecurringGrantDocument): RecurringGrantView {
  return {
    grantId: grant._id.toHexString(),
    tokens: grant.tokens,
    every: grant.every,
    active: grant.active,
    nextRunAt: grant.nextRunAt.toISOString(),
    lastRunAt: iso(grant.lastRunAt),
    actorId: grant.actorId,
    auditId: grant.auditId.toHexString(),
    reason: grant.reason,
    createdAt: grant.createdAt.toISOString(),
    updatedAt: grant.updatedAt.toISOString(),
  };
}

/** Snapshot of an account already rolled to `now`; `lots` = its open lots. */
export function toSnapshot(account: BillingAccountDocument, lots: CreditLotDocument[], now: Date): AccountSnapshot {
  const { tokensGranted, tokensUsed, cycleStart, cycleEnd } = account.buckets.plan;
  const remaining = planAvailable(account, now);
  const credits = spendableCredit(lots.map(toAllocationLot), now);
  const debt = account.buckets.debt.tokens;
  return {
    accountId: account._id.toHexString(),
    ownerType: account.ownerType,
    ownerId: account.ownerId.toHexString(),
    plan: { ...account.plan, periodStart: iso(account.plan.periodStart), periodEnd: iso(account.plan.periodEnd) },
    planTokens: {
      cycleStart: iso(cycleStart),
      cycleEnd: iso(cycleEnd),
      granted: tokensGranted,
      used: tokensUsed,
      remaining,
    },
    credits: { available: credits },
    debt,
    balance: remaining + credits - debt,
    signupGrantAt: iso(account.signupGrantAt),
    caps: { ...account.caps },
    openHolds: account.holds.length,
    version: account.version,
    updatedAt: account.updatedAt.toISOString(),
  };
}

/** The user's own view: tokens only, never a dollar amount. */
export function toUsageSummary(account: BillingAccountDocument, lots: CreditLotDocument[], now: Date): UsageSummary {
  const snapshot = toSnapshot(account, lots, now);
  const usable = spendableLots(lots.map(toAllocationLot), now);
  const onPro = account.plan.id === 'pro' && snapshot.planTokens.cycleEnd !== null;
  return {
    plan: { id: account.plan.id, periodEnd: snapshot.plan.periodEnd },
    balance: snapshot.balance,
    planTokens: onPro
      ? {
          granted: snapshot.planTokens.granted,
          used: snapshot.planTokens.used,
          remaining: snapshot.planTokens.remaining,
          cycleEnd: snapshot.planTokens.cycleEnd,
        }
      : null,
    credits: {
      available: snapshot.credits.available,
      lots: usable.map((lot) => ({ tokens: lot.tokensRemaining, expiresAt: iso(lot.expiresAt) })),
    },
    debt: snapshot.debt,
    holds: account.holds.map((hold) => ({ youtubeId: hold.youtubeId, tokens: hold.tokens })),
    maxVideoMinutes: maxVideoMinutes(account.caps),
    blocked: account.caps.blocked === true,
    signupGrantTokens: account.signupGrantAt ? SIGNUP_GRANT_TOKENS : 0,
  };
}
