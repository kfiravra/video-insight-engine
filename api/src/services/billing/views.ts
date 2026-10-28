import { freeWindowState, spendableCredit, spendableLots, videoLimits } from './allocation.js';
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

function iso(date: Date | null): string | null {
  return date ? date.toISOString() : null;
}

export function toAllocationLot(lot: CreditLotDocument): AllocationLot {
  return {
    lotId: lot._id.toHexString(),
    minutesRemaining: lot.minutesRemaining,
    expiresAt: lot.expiresAt,
    createdAt: lot.createdAt,
  };
}

export function toSplitView(split: BucketSplitDocument): BucketSplit {
  return {
    free: split.free,
    plan: split.plan,
    credit: split.credit,
    lots: split.lots.map((slice) => ({ lotId: slice.lotId.toHexString(), minutes: slice.minutes })),
  };
}

export function toHoldView(hold: BillingHoldDocument): HoldView {
  return {
    requestId: hold.requestId,
    userId: hold.userId.toHexString(),
    youtubeId: hold.youtubeId,
    durationSec: hold.durationSec,
    minutes: hold.minutes,
    split: toSplitView(hold.split),
    freeVideoAt: iso(hold.freeVideoAt),
    planCycleStart: iso(hold.planCycleStart),
    createdAt: hold.createdAt.toISOString(),
  };
}

export function toLotView(lot: CreditLotDocument, now: Date): CreditLotView {
  const expired = lot.expiresAt !== null && lot.expiresAt.getTime() <= now.getTime();
  return {
    lotId: lot._id.toHexString(),
    minutesGranted: lot.minutesGranted,
    minutesRemaining: lot.minutesRemaining,
    source: lot.source,
    ...(lot.priceUsdCents !== undefined && { priceUsdCents: lot.priceUsdCents }),
    expiresAt: iso(lot.expiresAt),
    expired,
    createdAt: lot.createdAt.toISOString(),
  };
}

export function toLedgerView(entry: LedgerEntryDocument): LedgerEntryView {
  return {
    id: entry._id.toHexString(),
    userId: entry.userId.toHexString(),
    type: entry.type,
    bucket: entry.bucket,
    minutes: entry.minutes,
    ...(entry.lotId && { lotId: entry.lotId.toHexString() }),
    source: entry.source,
    idempotencyKey: entry.idempotencyKey,
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
    minutes: grant.minutes,
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
  const free = freeWindowState(account.buckets.free.recentFreeVideoAt, account.caps, now);
  const { minutesGranted, minutesUsed, cycleStart, cycleEnd } = account.buckets.plan;
  return {
    accountId: account._id.toHexString(),
    ownerType: account.ownerType,
    ownerId: account.ownerId.toHexString(),
    plan: { ...account.plan, periodStart: iso(account.plan.periodStart), periodEnd: iso(account.plan.periodEnd) },
    free: {
      perWindow: free.cap,
      usedInWindow: free.inWindow.length,
      available: free.available,
      nextFreeAt: iso(free.nextFreeAt),
      maxVideoMinutes: videoLimits(account.caps).freeMaxMinutes,
    },
    planMinutes: {
      cycleStart: iso(cycleStart),
      cycleEnd: iso(cycleEnd),
      granted: minutesGranted,
      used: minutesUsed,
      remaining: planAvailable(account, now),
    },
    credits: { available: spendableCredit(lots.map(toAllocationLot), now) },
    caps: { ...account.caps },
    openHolds: account.holds.length,
    version: account.version,
    updatedAt: account.updatedAt.toISOString(),
  };
}

/** The user's own view (P3.6): minutes only, never a dollar amount. */
export function toUsageSummary(account: BillingAccountDocument, lots: CreditLotDocument[], now: Date): UsageSummary {
  const snapshot = toSnapshot(account, lots, now);
  const limits = videoLimits(account.caps);
  const paidMinutes = snapshot.planMinutes.remaining + snapshot.credits.available;
  const usable = spendableLots(lots.map(toAllocationLot), now);
  return {
    plan: { id: account.plan.id, periodEnd: snapshot.plan.periodEnd },
    free: {
      available: snapshot.free.available,
      nextFreeAt: snapshot.free.nextFreeAt,
      maxVideoMinutes: limits.freeMaxMinutes,
      perWindow: snapshot.free.perWindow,
    },
    planMinutes: {
      granted: snapshot.planMinutes.granted,
      used: snapshot.planMinutes.used,
      remaining: snapshot.planMinutes.remaining,
      cycleEnd: snapshot.planMinutes.cycleEnd,
    },
    credits: {
      available: snapshot.credits.available,
      lots: usable.map((lot) => ({ minutes: lot.minutesRemaining, expiresAt: iso(lot.expiresAt) })),
    },
    holds: account.holds.map((hold) => ({ youtubeId: hold.youtubeId, minutes: hold.minutes })),
    maxVideoMinutes: paidMinutes > 0 || account.plan.id === 'pro' ? limits.paidMaxMinutes : limits.freeMaxMinutes,
    blocked: account.caps.blocked === true,
  };
}
