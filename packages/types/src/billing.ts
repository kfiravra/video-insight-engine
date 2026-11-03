// ═══════════════════════════════════════════════════
// Billing: plans, the token ledger, credit lots
// ═══════════════════════════════════════════════════
//
// Users see tokens, never dollars. Every LLM execution they trigger (a video
// run, a chat turn, a library chat, an action) spends tokens; the api prices
// them from the execution's real LLM cost (the markup and the estimate live
// in api `services/billing/tokens.ts`, never here). The ledger
// (`ledgerEntries`) is the only source of truth for what an account can
// spend; `billingAccounts` buckets and `creditLots.tokensRemaining` are
// projections of it. vie-api's BillingService is the only writer. Ids are
// ObjectId hex and dates ISO strings on the wire; the collections store
// ObjectId / Date.

// ───────────────────────────────────────────────────
// Plans, packs and token constants (single source — no env overrides)
// ───────────────────────────────────────────────────

export const PLAN_IDS = ['free', 'pro'] as const;

export type PlanId = (typeof PLAN_IDS)[number];

export interface PlanConfig {
  /** Tokens granted per monthly cycle. Resets each cycle, no rollover. */
  periodTokens: number;
  /** Retail price per month in US cents (0 on free). Shown only on the Pro card. */
  priceUsdCents: number;
  /** RabbitMQ message priority of the account's runs (api `queue-topology.ts`): Pro accounts queue at `pro`'s. */
  queuePriority: number;
}

export const PLANS: Readonly<Record<PlanId, PlanConfig>> = {
  free: { periodTokens: 0, priceUsdCents: 0, queuePriority: 1 },
  pro: { periodTokens: 20_000, priceUsdCents: 2_000, queuePriority: 5 },
};

/** One-time grant every new account gets ($1 of retail value). Never expires. */
export const SIGNUP_GRANT_TOKENS = 1_000;

/** Longest video anyone can submit; `caps.maxVideoMinutes` only tightens it. */
export const MAX_VIDEO_MINUTES = 180;

export interface TokenPack {
  id: string;
  priceUsdCents: number;
  tokens: number;
  /** Purchased tokens expire this many months after purchase. */
  expiryMonths: number;
}

/** One pack for now; more sizes are data, not code. */
export const TOKEN_PACKS: readonly TokenPack[] = [
  { id: 'pack_5', priceUsdCents: 500, tokens: 5_000, expiryMonths: 12 },
];

// ───────────────────────────────────────────────────
// Shared vocabulary
// ───────────────────────────────────────────────────

/** `team` is reserved for P5; no team accounts exist yet. */
export type BillingOwnerType = 'user' | 'team';

export type PlanStatus = 'active' | 'past_due' | 'canceled';

/** `manual` = set by an admin; `provider` = a payment-provider subscription (P4). */
export type PlanSource = 'manual' | 'provider';

/**
 * Where tokens sit: the Pro cycle's allowance, credit lots, and `debt` — what
 * a run cost beyond everything the account had (overdraft). Debt blocks every
 * new execution until a grant, pack or Pro cycle repays it.
 */
export type BillingBucket = 'plan' | 'credit' | 'debt';

/** Buckets found in the ledger: `free` appears only on minute entries written before the token migration. */
export type LedgerBucket = BillingBucket | 'free';

/** What an entry counts. Entries without `unit` are minutes (history from before the token migration). */
export type LedgerUnit = 'tokens' | 'minutes';

/**
 * Ledger entry kinds. `tokens` is signed: + adds headroom, − consumes it.
 * A `debit` with `mirrorsHold` posts tokens a hold already took and does NOT
 * move the balance again; every other entry does. `repay` moves tokens from
 * an incoming grant (or a new Pro cycle) into the debt bucket. `convert`
 * closes a minute balance and opens its token counterpart (the migration).
 */
export type LedgerEntryType =
  | 'grant'
  | 'hold'
  | 'debit'
  | 'release'
  | 'expire'
  | 'refund'
  | 'adjust'
  | 'repay'
  | 'convert';

/** Which kind of LLM execution a debit paid for. */
export type ExecutionKind = 'video' | 'chat' | 'library_chat' | 'action';

/** Where an entry or lot came from. `ref` is the requestId, auditId, grant id, provider event id, … */
export type BillingSourceKind = 'run' | 'payment' | 'admin' | 'migration' | 'recurring' | 'system';

export interface BillingSource {
  kind: BillingSourceKind;
  ref: string;
}

/** Admin overrides. A missing (or null) field means "no override". Caps only ever tighten. */
export interface AccountCaps {
  /** Ceiling on video length for this account (below MAX_VIDEO_MINUTES). */
  maxVideoMinutes?: number | null;
  /** Most open holds (runs in flight) the account may have at once. */
  maxConcurrent?: number | null;
  /** Blocked accounts cannot start any execution (the per-account kill switch). */
  blocked?: boolean | null;
}

export interface LotSlice {
  lotId: string;
  tokens: number;
}

/** Where a hold's tokens were taken: the plan cycle first, then credit lots FIFO. */
export interface BucketSplit {
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
  tokensGranted: number;
  tokensUsed: number;
}

/**
 * A video run in flight: the estimate it holds and exactly where it came
 * from, so the settle (on the run's real cost) and a release return the
 * unused part there.
 */
export interface BillingHold {
  /** The run id (`billingRunId`) the hold is keyed by. */
  requestId: string;
  userId: string;
  youtubeId: string | null;
  durationSec: number;
  /** The estimate held. */
  tokens: number;
  split: BucketSplit;
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
    plan: PlanBucketState;
    /** Σ `creditLots.tokensRemaining` (expired-but-unswept lots included). */
    credit: { tokensAvailable: number };
    /** Tokens owed (≥ 0). */
    debt: { tokens: number };
  };
  holds: BillingHold[];
  caps: AccountCaps;
  /** When the one-time signup grant was written; absent on accounts that never got it. */
  signupGrantAt?: string;
  /** The last auditIds `setCaps` applied (bounded), so a retried call is a no-op. */
  capsAuditIds?: string[];
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
  bucket: LedgerBucket;
  /** `tokens` on every entry written since the token migration; absent = minutes. */
  unit?: 'tokens';
  /** Signed token amount (token entries). */
  tokens?: number;
  /** Signed minute amount (entries from before the token migration). */
  minutes?: number;
  /** True on a `debit` that posts tokens its hold already took (balance-neutral). */
  mirrorsHold?: boolean;
  /** Credit entries name their lot. */
  lotId?: string;
  source: BillingSource;
  /** Unique. `hold:<runId>:…`, `debit:<runId>:…`, `debit:exec:<executionId>:…`, `admin:<auditId>[:<suffix>]`, … */
  idempotencyKey: string;
  executionKind?: ExecutionKind;
  /** The api's per-request id of a chat / library chat / action execution. */
  executionId?: string;
  youtubeId?: string;
  durationSec?: number;
  /** Internal only (margin): LLM $ attributed to the entry. Never shown to users. */
  costUsd?: number;
  /** `users._id` hex of the admin, or `system:<name>`. */
  actorId?: string;
  auditId?: string;
  reason?: string;
  createdAt: string;
}

/** `creditLots` — tokens consumed FIFO by `expiresAt` (never-expiring last), then `createdAt`. */
export interface CreditLot {
  _id: string;
  accountId: string;
  tokensGranted: number;
  tokensRemaining: number;
  source: BillingSource;
  /** What the customer paid (refund math). Absent on admin/recurring/signup lots. */
  priceUsdCents?: number;
  /** null = never expires. */
  expiresAt: string | null;
  createdAt: string;
}

/** `recurringGrants` — a lot of `tokens` granted every period (demo / eval accounts). Applied by the repair job. */
export interface RecurringGrant {
  _id: string;
  accountId: string;
  tokens: number;
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

// ───────────────────────────────────────────────────
// Views (BillingService admin API — Contract 1)
// ───────────────────────────────────────────────────

/** Plain-JSON state of an account as of a moment (audit before/after, admin detail). */
export interface AccountSnapshot {
  accountId: string;
  ownerType: BillingOwnerType;
  ownerId: string;
  plan: BillingPlanState;
  planTokens: {
    cycleStart: string | null;
    cycleEnd: string | null;
    granted: number;
    used: number;
    remaining: number;
  };
  /** Spendable credit tokens (unexpired lots only). */
  credits: { available: number };
  /** Tokens owed (≥ 0). */
  debt: number;
  /** plan remaining + credits − debt; negative while in debt. */
  balance: number;
  signupGrantAt: string | null;
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

/** A ledger row for the admin: `amount` in its `unit` (minute rows are pre-migration history). */
export interface LedgerEntryView
  extends Omit<LedgerEntry, '_id' | 'accountId' | 'unit' | 'tokens' | 'minutes'> {
  id: string;
  unit: LedgerUnit;
  amount: number;
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

/** What a user sees about their own tokens — no dollar field anywhere. */
export interface UsageSummary {
  plan: { id: PlanId; periodEnd: string | null };
  /** What the account can spend now: plan remaining + credits − debt (negative while in debt). */
  balance: number;
  /** The Pro cycle; null without one. */
  planTokens: { granted: number; used: number; remaining: number; cycleEnd: string | null } | null;
  credits: { available: number; lots: Array<{ tokens: number; expiresAt: string | null }> };
  /** Tokens owed; every new execution is refused while > 0. */
  debt: number;
  /** Estimates held by runs in flight. */
  holds: Array<{ youtubeId: string | null; tokens: number }>;
  /** Longest video the account can submit. */
  maxVideoMinutes: number;
  blocked: boolean;
  /** Tokens the account got at signup (0 when it never got the grant). */
  signupGrantTokens: number;
}

/**
 * What the requesting user's latest run of a video holds or cost
 * (GET /api/videos/:id `billing`): `held` while it runs (the estimate),
 * `charged` once settled on its real cost, `released` when it failed (given
 * back in full), `free` when this user ran nothing (cached, attached).
 */
export interface VideoBilling {
  state: 'held' | 'charged' | 'released' | 'free';
  estimatedTokens: number;
  /** null while held. */
  chargedTokens: number | null;
}

/** GET /api/videos/estimate — what a submit would hold before it runs. */
export interface VideoEstimateResponse {
  youtubeId: string;
  durationSec: number;
  /** 0 when the video is served free. */
  tokens: number;
  free: boolean;
}

// ───────────────────────────────────────────────────
// Payment-provider events (P3.9) — type reserved, nothing writes it yet
// ───────────────────────────────────────────────────

/** What a provider webhook means to the ledger; anything else is `unhandled` (stored, never acted on). */
export type PaymentEventKind =
  | 'checkout_completed'
  | 'subscription_active'
  | 'subscription_past_due'
  | 'subscription_canceled'
  | 'refunded'
  | 'disputed'
  | 'unhandled';

/**
 * A webhook normalised by a PaymentProviderAdapter. `id` is the provider's
 * event id and will be the unique key of the `paymentEvents` collection, which
 * makes webhook delivery idempotent. P4 creates that collection; until then
 * nothing stores a PaymentEvent.
 */
export interface PaymentEvent {
  id: string;
  /** The adapter's `name`. */
  provider: string;
  kind: PaymentEventKind;
  /** The provider's own event name, kept for the audit trail. */
  providerEventType: string;
  /** `billingAccounts._id` hex, from the metadata `createCheckout` attached; null when absent. */
  accountId: string | null;
  providerCustomerId: string | null;
  providerSubscriptionId: string | null;
  /** Money moved in minor units; null for events that carry none. */
  amountCents: number | null;
  currency: string | null;
  /** When the provider says it happened (ISO 8601). */
  occurredAt: string;
}
