import type { ObjectId } from 'mongodb';
import type {
  AccountCaps,
  BillingBucket,
  BillingOwnerType,
  BillingSource,
  LedgerEntryType,
  PlanId,
  PlanSource,
  PlanStatus,
} from '@vie/types';

// Stored shapes of the minutes-ledger collections (ObjectId / Date). The
// wire shapes live in @vie/types billing.ts.

export interface LotSliceDocument {
  lotId: ObjectId;
  minutes: number;
}

export interface BucketSplitDocument {
  free: number;
  plan: number;
  credit: number;
  lots: LotSliceDocument[];
}

export interface BillingHoldDocument {
  requestId: string;
  userId: ObjectId;
  youtubeId: string | null;
  durationSec: number;
  minutes: number;
  split: BucketSplitDocument;
  freeVideoAt: Date | null;
  planCycleStart: Date | null;
  createdAt: Date;
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
  minutesGranted: number;
  minutesUsed: number;
}

export interface BillingAccountDocument {
  _id: ObjectId;
  ownerType: BillingOwnerType;
  ownerId: ObjectId;
  plan: BillingPlanDocument;
  buckets: {
    free: { recentFreeVideoAt: Date[] };
    plan: PlanBucketDocument;
    credit: { minutesAvailable: number };
  };
  holds: BillingHoldDocument[];
  caps: AccountCaps;
  version: number;
  createdAt: Date;
  updatedAt: Date;
}

/** The fields a conditional account update replaces. */
export type AccountMutableFields = Pick<BillingAccountDocument, 'plan' | 'buckets' | 'holds' | 'caps'>;

export interface LedgerEntryDocument {
  _id: ObjectId;
  accountId: ObjectId;
  userId: ObjectId;
  type: LedgerEntryType;
  bucket: BillingBucket;
  minutes: number;
  lotId?: ObjectId;
  source: BillingSource;
  idempotencyKey: string;
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
  minutesGranted: number;
  minutesRemaining: number;
  source: BillingSource;
  priceUsdCents?: number;
  expiresAt: Date | null;
  createdAt: Date;
}

export interface RecurringGrantDocument {
  _id: ObjectId;
  accountId: ObjectId;
  minutes: number;
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
