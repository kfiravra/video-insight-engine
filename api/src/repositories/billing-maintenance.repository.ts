import { Db, ObjectId, Collection } from 'mongodb';
import type {
  BillingAccountDocument,
  CreditLotDocument,
  RecurringGrantDocument,
} from './billing.documents.js';

/** The fields of a `videoSummaryCache` row the stale-hold rule reads. */
export interface RunState {
  status: 'pending' | 'processing' | 'completed' | 'failed';
  updatedAt: Date;
}

/**
 * The cross-account scans of the billing repair job (P1.6). Each is bounded
 * by `limit`; whatever is left is picked up on the next tick. Kept apart
 * from BillingRepository, which serves the per-account request path.
 */
export class BillingMaintenanceRepository {
  private readonly accounts: Collection<BillingAccountDocument>;
  private readonly lots: Collection<CreditLotDocument>;
  private readonly recurringGrants: Collection<RecurringGrantDocument>;
  private readonly videoCache: Collection<{ _id: ObjectId; youtubeId: string; requestId?: string } & RunState>;

  constructor(db: Db) {
    this.accounts = db.collection('billingAccounts');
    this.lots = db.collection('creditLots');
    this.recurringGrants = db.collection('recurringGrants');
    this.videoCache = db.collection('videoSummaryCache');
  }

  /** Accounts holding at least one hold opened before `cutoff`. */
  async listAccountsWithHoldsBefore(cutoff: Date, limit: number): Promise<BillingAccountDocument[]> {
    return this.accounts.find({ 'holds.createdAt': { $lt: cutoff } }).limit(limit).toArray();
  }

  /**
   * The run a hold paid for: the version of `youtubeId` whose dispatch stamp
   * is `requestId` (read-only). Null when no dispatch stamped it — never
   * dispatched, the guard was held, or a later dispatch overwrote the stamp.
   */
  async findRun(youtubeId: string, requestId: string): Promise<RunState | null> {
    return this.videoCache.findOne({ youtubeId, requestId }, { projection: { _id: 0, status: 1, updatedAt: 1 } });
  }

  /** Accounts with an open lot whose expiry has passed. */
  async listAccountIdsWithExpiredLots(now: Date, limit: number): Promise<ObjectId[]> {
    const rows = await this.lots
      .aggregate<{ _id: ObjectId }>([
        { $match: { minutesRemaining: { $gt: 0 }, expiresAt: { $lte: now } } },
        { $group: { _id: '$accountId' } },
        { $limit: limit },
      ])
      .toArray();
    return rows.map((row) => row._id);
  }

  async listDueRecurringGrants(now: Date, limit: number): Promise<RecurringGrantDocument[]> {
    return this.recurringGrants
      .find({ active: true, deletedAt: null, nextRunAt: { $lte: now } })
      .sort({ nextRunAt: 1 })
      .limit(limit)
      .toArray();
  }

  /** Accounts written at or after `since`, oldest first. */
  async listAccountsUpdatedSince(since: Date, limit: number): Promise<BillingAccountDocument[]> {
    return this.accounts.find({ updatedAt: { $gte: since } }).sort({ updatedAt: 1 }).limit(limit).toArray();
  }
}
