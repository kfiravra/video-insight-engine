// ═══════════════════════════════════════════════════
// Billing: plans, the minutes ledger, credit lots
// ═══════════════════════════════════════════════════
//
// Users see video-minutes, never dollars. The ledger (`ledgerEntries`) is the
// only source of truth for what an account can spend; `billingAccounts`
// buckets and `creditLots.minutesRemaining` are projections of it. vie-api's
// BillingService is the only writer. Ids are ObjectId hex and dates ISO
// strings on the wire; the collections store ObjectId / Date.

// ───────────────────────────────────────────────────
// Plans and minute constants (single source — no env overrides)
// ───────────────────────────────────────────────────

export const PLAN_IDS = ['free', 'pro'] as const;

export type PlanId = (typeof PLAN_IDS)[number];

export interface PlanConfig {
  /** Minutes granted per monthly period. Resets each period, no rollover (Q5). */
  periodMinutes: number;
  /** Longest video the plan allows: free 30 (the free video), pro 180 (Q2). */
  maxVideoMinutes: number;
  /** RabbitMQ message priority for runs on this plan. */
  queuePriority: number;
}

export const PLANS: Readonly<Record<PlanId, PlanConfig>> = {
  free: { periodMinutes: 0, maxVideoMinutes: 30, queuePriority: 1 },
  pro: { periodMinutes: 600, maxVideoMinutes: 180, queuePriority: 5 },
};

/**
 * The free video (Q3): `perWindow` videos of at most `maxVideoMinutes` per
 * rolling `windowHours`, counted from each free video's own timestamp.
 * `caps.freeVideosPerDay` overrides `perWindow` per account.
 */
export const FREE_VIDEO = { perWindow: 1, windowHours: 24, maxVideoMinutes: 30 } as const;

/** Longest video any paid minutes (plan or credits) can pay for (Q2). */
export const PAID_MAX_VIDEO_MINUTES = 180;

/** Every video costs at least this many minutes (Q8). */
export const MIN_MINUTES_PER_VIDEO = 3;

export interface CreditPack {
  id: string;
  priceUsdCents: number;
  minutes: number;
  /** Purchased minutes expire this many months after purchase (Q5). */
  expiryMonths: number;
}

/** v1 sells one pack (Q4); more packs are data, not code. */
export const CREDIT_PACKS: readonly CreditPack[] = [
  { id: 'pack_5', priceUsdCents: 500, minutes: 150, expiryMonths: 12 },
];

// ───────────────────────────────────────────────────
// Shared vocabulary
// ───────────────────────────────────────────────────

/** `team` is reserved for P5; no team accounts exist yet. */
export type BillingOwnerType = 'user' | 'team';

export type PlanStatus = 'active' | 'past_due' | 'canceled';

/** `manual` = set by an admin; `provider` = a payment-provider subscription (P4). */
export type PlanSource = 'manual' | 'provider';

export type BillingBucket = 'free' | 'plan' | 'credit';

/**
 * Ledger entry kinds. `minutes` is signed: + adds headroom, − consumes it.
 * A `debit` posts a hold and repeats its minutes for reporting, but does NOT
 * move the balance again — balance = Σ minutes of every entry except `debit`.
 */
export type LedgerEntryType = 'grant' | 'hold' | 'debit' | 'release' | 'expire' | 'refund' | 'adjust';

/** Where an entry or lot came from. `ref` is the requestId, auditId, grant id, provider event id, … */
export type BillingSourceKind = 'run' | 'payment' | 'admin' | 'migration' | 'recurring' | 'system';

export interface BillingSource {
  kind: BillingSourceKind;
  ref: string;
}

/** Admin overrides. A missing (or null) field means "use the plan default". Caps only ever tighten video length. */
export interface AccountCaps {
  /** Ceiling on video length for this account (applies to free and paid minutes). */
  maxVideoMinutes?: number | null;
  /** Free videos per rolling window; 0 disables the free video. */
  freeVideosPerDay?: number | null;
  /** Most open holds (runs in flight) the account may have at once. */
  maxConcurrent?: number | null;
  /** Blocked accounts cannot start a run (the per-account kill switch). */
  blocked?: boolean | null;
}

export interface LotSlice {
  lotId: string;
  minutes: number;
}

/** How one video's minutes were taken: whole video free, or plan then credits FIFO by lot. */
export interface BucketSplit {
  free: number;
  plan: number;
  credit: number;
  lots: LotSlice[];
}

// ───────────────────────────────────────────────────
// Collections (stored shapes, wire-encoded)
// ───────────────────────────────────────────────────

export interface BillingPlanState {
  id: PlanId;
  status: PlanStatus;
  source: PlanSource | null;
  /** The plan term (manual Pro: 12 months). null on free. */
  periodStart: string | null;
  periodEnd: string | null;
}

/** The current monthly allowance cycle inside the plan term. */
export interface PlanBucketState {
  cycleStart: string | null;
  cycleEnd: string | null;
  minutesGranted: number;
  minutesUsed: number;
}

/** A run in flight. Records exactly where its minutes came from, so a release returns them there. */
export interface BillingHold {
  requestId: string;
  userId: string;
  youtubeId: string | null;
  durationSec: number;
  minutes: number;
  split: BucketSplit;
  /** The timestamp this hold added to `buckets.free.recentFreeVideoAt`, if it used the free video. */
  freeVideoAt: string | null;
  /** `buckets.plan.cycleStart` the plan slice was taken from, if any. */
  planCycleStart: string | null;
  createdAt: string;
}

/** `billingAccounts` — one per user (one per team later). Unique `{ownerType, ownerId}`. */
export interface BillingAccount {
  _id: string;
  ownerType: BillingOwnerType;
  ownerId: string;
  plan: BillingPlanState;
  buckets: {
    /** Free videos inside the rolling window (pruned lazily). */
    free: { recentFreeVideoAt: string[] };
    plan: PlanBucketState;
    /** Σ `creditLots.minutesRemaining` (expired-but-unswept lots included). */
    credit: { minutesAvailable: number };
  };
  holds: BillingHold[];
  caps: AccountCaps;
  /** Optimistic-concurrency counter, bumped by every write. */
  version: number;
  createdAt: string;
  updatedAt: string;
}

/** `ledgerEntries` — append-only; never updated, never deleted (GDPR pseudonymises, Q12). */
export interface LedgerEntry {
  _id: string;
  accountId: string;
  /** Who consumed or was acted on (the run's user, the admin's target). */
  userId: string;
  type: LedgerEntryType;
  bucket: BillingBucket;
  minutes: number;
  /** Credit entries name their lot. */
  lotId?: string;
  source: BillingSource;
  /** Unique. `hold:<requestId>:…`, `debit:<requestId>:…`, `admin:<auditId>[:<suffix>]`, … */
  idempotencyKey: string;
  youtubeId?: string;
  durationSec?: number;
  /** Internal only (margin): LLM $ attributed to the run. Never shown to users. */
  costUsd?: number;
  /** `users._id` hex of the admin, or `system:<name>`. */
  actorId?: string;
  auditId?: string;
  reason?: string;
  createdAt: string;
}

/** `creditLots` — minutes consumed FIFO by `expiresAt` (never-expiring last), then `createdAt`. */
export interface CreditLot {
  _id: string;
  accountId: string;
  minutesGranted: number;
  minutesRemaining: number;
  source: BillingSource;
  /** What the customer paid (refund math). Absent on admin/recurring lots. */
  priceUsdCents?: number;
  /** null = never expires. */
  expiresAt: string | null;
  createdAt: string;
}

/** `recurringGrants` — a lot of `minutes` granted every period (demo / eval accounts, Q20). Applied by the repair job. */
export interface RecurringGrant {
  _id: string;
  accountId: string;
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
  /** Soft delete keeps the history next to its ledger lots. */
  deletedAt: string | null;
}

export type BillingShadowPath = 'created' | 'cached' | 'attached' | 'failed' | 'retry' | 'completed';

export interface BillingShadowDecision {
  admitted: boolean;
  charged: boolean;
}

/** `billingShadow` — the old gate's decision next to the ledger's, one row per requestId (Contract 4). */
export interface BillingShadow {
  _id: string;
  requestId: string;
  userId: string;
  youtubeId: string;
  path: BillingShadowPath;
  oldDecision: BillingShadowDecision;
  newDecision: BillingShadowDecision & { split?: BucketSplit; reason?: string };
  classification: 'agree' | 'policy_delta' | 'disagree';
  /** The decision that explains a `policy_delta`, e.g. `Q3 one free video per 24 h vs old $1/day`. */
  policyRef?: string;
  createdAt: string;
  updatedAt: string;
}

// ───────────────────────────────────────────────────
// Views (BillingService admin API — Contract 1)
// ───────────────────────────────────────────────────

/** Plain-JSON state of an account as of a moment (audit before/after, admin detail). */
export interface AccountSnapshot {
  accountId: string;
  ownerType: BillingOwnerType;
  ownerId: string;
  plan: BillingPlanState;
  free: {
    perWindow: number;
    usedInWindow: number;
    available: boolean;
    /** When the next free video opens; null when one is open now or the free video is disabled. */
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
  /** Spendable credit minutes (unexpired lots only). */
  credits: { available: number };
  caps: AccountCaps;
  openHolds: number;
  version: number;
  updatedAt: string;
}

export interface CreditLotView extends Omit<CreditLot, '_id' | 'accountId'> {
  lotId: string;
  expired: boolean;
}

export type HoldView = BillingHold;

export interface LedgerEntryView extends Omit<LedgerEntry, '_id' | 'accountId'> {
  id: string;
}

export interface RecurringGrantView extends Omit<RecurringGrant, '_id' | 'accountId' | 'deletedAt'> {
  grantId: string;
}

export interface AccountDetail {
  account: AccountSnapshot;
  lots: CreditLotView[];
  holds: HoldView[];
  /** Newest first. */
  ledger: LedgerEntryView[];
  recurringGrants: RecurringGrantView[];
}

/** What a user sees about their own minutes — no dollar field anywhere. */
export interface UsageSummary {
  plan: { id: PlanId; periodEnd: string | null };
  free: { available: boolean; nextFreeAt: string | null; maxVideoMinutes: number };
  planMinutes: { granted: number; used: number; remaining: number; cycleEnd: string | null };
  credits: { available: number; lots: Array<{ minutes: number; expiresAt: string | null }> };
  holds: Array<{ youtubeId: string | null; minutes: number }>;
  /** Longest video the account can submit with what it has now. */
  maxVideoMinutes: number;
  blocked: boolean;
}
