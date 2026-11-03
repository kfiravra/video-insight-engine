import type { ObjectId } from 'mongodb';
import type {
  AccountCaps,
  BillingOwnerType,
  BillingSource,
  ExecutionKind,
  LedgerBucket,
  LedgerEntryType,
  PlanId,
  PlanSource,
  PlanStatus,
} from '@vie/types';

// Stored shapes of the token-ledger collections (ObjectId / Date). The wire
// shapes live in @vie/types billing.ts. Accounts and lots written before the
// token migration still carry minute fields (`buckets.free`,
// `minutesRemaining`, …); the repository reads those as empty token state
// and keeps the old fields for the migration (scripts/migrate-billing-tokens.ts).

export interface LotSliceDocument {
  lotId: ObjectId;
  tokens: number;
}

export interface BucketSplitDocument {
  plan: number;
  credit: number;
  lots: LotSliceDocument[];
}

export interface BillingHoldDocument {
  /** The run id (`billingRunId`). */
  requestId: string;
  userId: ObjectId;
  youtubeId: string | null;
  durationSec: number;
  /** The estimate held. */
  tokens: number;
  split: BucketSplitDocument;
  planCycleStart: Date | null;
  createdAt: Date;
  /** A minute hold's original split, kept through token writes until the token migration (read by nothing else). */
  minuteSplit?: unknown;
}

export interface BillingPlanDocument {
  id: PlanId;
  status: PlanStatus;
  source: PlanSource | null;
  periodStart: Date | null;
  periodEnd: Date | null;
}

export interface PlanBucketDocument {
  cycleStart: Date | null;
  cycleEnd: Date | null;
  tokensGranted: number;
  tokensUsed: number;
}

export interface BillingAccountDocument {
  _id: ObjectId;
  ownerType: BillingOwnerType;
  ownerId: ObjectId;
  plan: BillingPlanDocument;
  buckets: {
    plan: PlanBucketDocument;
    credit: { tokensAvailable: number };
    /** Tokens owed (≥ 0): what runs cost beyond everything the account had. */
    debt: { tokens: number };
  };
  holds: BillingHoldDocument[];
  caps: AccountCaps;
  /** When the one-time signup grant was written (`grant:signup:<userId>`). */
  signupGrantAt?: Date;
  /** The last auditIds `setCaps` applied (bounded), so a retried call is a no-op (I1). */
  capsAuditIds?: string[];
  version: number;
  createdAt: Date;
  updatedAt: Date;
}

/** The fields a conditional account update replaces. */
export type AccountMutableFields = Pick<
  BillingAccountDocument,
  'plan' | 'buckets' | 'holds' | 'caps' | 'capsAuditIds' | 'signupGrantAt'
>;

export interface LedgerEntryDocument {
  _id: ObjectId;
  accountId: ObjectId;
  userId: ObjectId;
  type: LedgerEntryType;
  bucket: LedgerBucket;
  /** `tokens` on every entry since the token migration; absent = a minute entry (history). */
  unit?: 'tokens';
  tokens?: number;
  minutes?: number;
  /** A debit that posts tokens its hold already took: balance-neutral. */
  mirrorsHold?: boolean;
  lotId?: ObjectId;
  source: BillingSource;
  idempotencyKey: string;
  executionKind?: ExecutionKind;
  executionId?: string;
  youtubeId?: string;
  durationSec?: number;
  costUsd?: number;
  actorId?: string;
  auditId?: ObjectId;
  reason?: string;
  createdAt: Date;
}

export type NewLedgerEntry = Omit<LedgerEntryDocument, '_id'>;

export interface CreditLotDocument {
  _id: ObjectId;
  accountId: ObjectId;
  tokensGranted: number;
  tokensRemaining: number;
  source: BillingSource;
  priceUsdCents?: number;
  expiresAt: Date | null;
  createdAt: Date;
}

export interface RecurringGrantDocument {
  _id: ObjectId;
  accountId: ObjectId;
  tokens: number;
  every: 'month';
  active: boolean;
  nextRunAt: Date;
  lastRunAt: Date | null;
  actorId: string;
  auditId: ObjectId;
  reason: string;
  createdAt: Date;
  updatedAt: Date;
  deletedAt: Date | null;
}
