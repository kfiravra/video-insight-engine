import type { InfiniteData } from '@tanstack/react-query';
import { formatNumber } from './format';

// Wire types for GET /billing/accounts/{user_id}. They mirror `AccountDetail` in
// packages/types/src/billing.ts. Fields vie-api omits when absent are `null` from
// vie-admin, so optional fields accept both.

export type BillingBucket = 'free' | 'plan' | 'credit';
export type BillingLedgerType = 'grant' | 'hold' | 'debit' | 'release' | 'expire' | 'refund' | 'adjust';
export type BillingSourceKind = 'run' | 'payment' | 'admin' | 'migration' | 'recurring' | 'system';

export interface BillingSource {
  kind: BillingSourceKind;
  ref: string;
}

/** Admin overrides; null/absent = the plan default. */
export interface BillingCaps {
  maxVideoMinutes?: number | null;
  freeVideosPerDay?: number | null;
  maxConcurrent?: number | null;
  blocked?: boolean | null;
}

export interface BillingAccountSnapshot {
  accountId: string;
  ownerType: 'user' | 'team';
  ownerId: string;
  plan: {
    id: 'free' | 'pro';
    status: 'active' | 'past_due' | 'canceled';
    source: 'manual' | 'provider' | null;
    periodStart: string | null;
    periodEnd: string | null;
  };
  free: {
    perWindow: number;
    usedInWindow: number;
    available: boolean;
    nextFreeAt: string | null;
    maxVideoMinutes: number;
  };
  planMinutes: {
    cycleStart: string | null;
    cycleEnd: string | null;
    granted: number;
    used: number;
    remaining: number;
  };
  credits: { available: number };
  caps: BillingCaps;
  openHolds: number;
  version: number;
  updatedAt: string;
}

export interface BillingCreditLot {
  lotId: string;
  minutesGranted: number;
  minutesRemaining: number;
  source: BillingSource;
  priceUsdCents?: number | null;
  expiresAt: string | null;
  expired: boolean;
  createdAt: string;
}

export interface BillingHold {
  requestId: string;
  userId: string;
  youtubeId: string | null;
  durationSec: number;
  minutes: number;
  split: { free: number; plan: number; credit: number; lots: Array<{ lotId: string; minutes: number }> };
  freeVideoAt: string | null;
  planCycleStart: string | null;
  createdAt: string;
}

export interface BillingLedgerEntry {
  id: string;
  userId: string;
  type: BillingLedgerType;
  bucket: BillingBucket;
  /** Signed: + adds headroom, - consumes it. */
  minutes: number;
  lotId?: string | null;
  source: BillingSource;
  idempotencyKey: string;
  youtubeId?: string | null;
  durationSec?: number | null;
  /** Internal margin data; admin-only. */
  costUsd?: number | null;
  actorId?: string | null;
  auditId?: string | null;
  reason?: string | null;
  createdAt: string;
}

export interface BillingRecurringGrant {
  grantId: string;
  minutes: number;
  every: 'month';
  active: boolean;
  nextRunAt: string;
  lastRunAt: string | null;
  actorId: string;
  auditId: string;
  reason: string;
  createdAt: string;
  updatedAt: string;
}

export interface BillingAccountDetail {
  /** null = the user has no billing account yet (vie-api creates it on the first video). */
  account: BillingAccountSnapshot | null;
  lots: BillingCreditLot[];
  holds: BillingHold[];
  /** Newest first. */
  ledger: BillingLedgerEntry[];
  recurringGrants: BillingRecurringGrant[];
  /** Pass as `before` for the next ledger page; null/absent on the last page. */
  ledgerNextBefore?: string | null;
}

export interface BillingAccountQuery {
  limit?: number;
  before?: string;
}

// --- Helpers ---

/** "1,250 min". */
export function formatMinutes(minutes: number): string {
  return `${formatNumber(minutes)} min`;
}

/** "+150 min" / "-12 min": the sign is what tells a grant from a spend. */
export function formatSignedMinutes(minutes: number): string {
  const sign = minutes > 0 ? '+' : minutes < 0 ? '-' : '';
  return `${sign}${formatNumber(Math.abs(minutes))} min`;
}

/** Cents as dollars, e.g. 500 -> "$5.00". */
export function formatCents(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`;
}

/** "admin:6f1a…" — where a lot or entry came from, with a long ref shortened. */
export function describeSource(source: BillingSource): string {
  const ref = source.ref.length > 14 ? `${source.ref.slice(0, 8)}…` : source.ref;
  return `${source.kind}:${ref}`;
}

/**
 * Fold the pages of a paged account read into one detail: account, lots, holds
 * and grants come from the first page (the freshest read), the ledger is every
 * page's entries in order, and the cursor is the last page's.
 */
export function mergeLedgerPages(data: InfiniteData<BillingAccountDetail, string | undefined>): BillingAccountDetail {
  const [first] = data.pages;
  const last = data.pages[data.pages.length - 1];
  return {
    ...first,
    ledger: data.pages.flatMap((page) => page.ledger),
    ledgerNextBefore: last.ledgerNextBefore ?? null,
  };
}
