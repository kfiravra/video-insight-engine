import { setTimeout as sleep } from 'node:timers/promises';
import { Db, ObjectId, Collection, MongoError } from 'mongodb';
import type { ClientSession } from 'mongodb';
import type { BillingOwnerType } from '@vie/types';
import type {
  BillingAccountDocument,
  BillingHoldDocument,
  AccountMutableFields,
  CreditLotDocument,
  LedgerEntryDocument,
  NewLedgerEntry,
  RecurringGrantDocument,
} from './billing.documents.js';

// The stored shapes live in billing.documents.ts; re-exported so callers keep one import path.
export type {
  LotSliceDocument,
  BucketSplitDocument,
  BillingHoldDocument,
  BillingPlanDocument,
  PlanBucketDocument,
  BillingAccountDocument,
  AccountMutableFields,
  LedgerEntryDocument,
  NewLedgerEntry,
  CreditLotDocument,
  RecurringGrantDocument,
} from './billing.documents.js';

export interface InsertLedgerResult {
  entry: LedgerEntryDocument;
  /** False when an entry with the same idempotency key already existed (returned instead). */
  inserted: boolean;
}

export interface LedgerPageOptions {
  limit: number;
  before?: Date;
}

/**
 * A conditional write found the account (or a lot) changed since it was read.
 * `withTransaction` retries the whole unit on it, like a transient error.
 */
export class StaleAccountVersionError extends Error {
  constructor(message = 'billing account changed concurrently') {
    super(message);
    this.name = 'StaleAccountVersionError';
  }
}

/** Bounded: a hot account under contention fails loudly rather than spinning. */
export const MAX_TRANSACTION_ATTEMPTS = 8;
const MAX_COMMIT_ATTEMPTS = 3;
const RETRY_BASE_DELAY_MS = 5;
const RETRY_MAX_DELAY_MS = 200;

function isDuplicateKey(err: unknown): boolean {
  return (err as { code?: number }).code === 11000;
}

function isRetryableTransactionError(err: unknown): boolean {
  if (err instanceof StaleAccountVersionError) return true;
  return err instanceof MongoError && err.hasErrorLabel('TransientTransactionError');
}

function isUnknownCommitResult(err: unknown): boolean {
  return err instanceof MongoError && err.hasErrorLabel('UnknownTransactionCommitResult');
}

/** Exponential backoff with full jitter, so contending writers spread out instead of colliding again. */
function retryDelay(attempt: number): Promise<void> {
  const ceiling = Math.min(RETRY_BASE_DELAY_MS * 2 ** attempt, RETRY_MAX_DELAY_MS);
  return sleep(Math.floor(Math.random() * ceiling));
}

/** State of a fresh account, before its signup grant. */
export function emptyAccountState(now: Date): Omit<BillingAccountDocument, '_id' | 'ownerType' | 'ownerId'> {
  return {
    plan: { id: 'free', status: 'active', source: null, periodStart: null, periodEnd: null },
    buckets: {
      plan: { cycleStart: null, cycleEnd: null, tokensGranted: 0, tokensUsed: 0 },
      credit: { tokensAvailable: 0 },
      debt: { tokens: 0 },
    },
    holds: [],
    caps: {},
    version: 0,
    createdAt: now,
    updatedAt: now,
  };
}

/** What an account written before the token migration may lack. */
type StoredAccount = Omit<BillingAccountDocument, 'buckets' | 'holds'> & {
  buckets: Partial<{
    plan: Partial<BillingAccountDocument['buckets']['plan']>;
    credit: Partial<BillingAccountDocument['buckets']['credit']>;
    debt: Partial<BillingAccountDocument['buckets']['debt']>;
  }>;
  holds?: Array<Partial<BillingHoldDocument> & Pick<BillingHoldDocument, 'requestId' | 'userId' | 'createdAt'>>;
};

function normalizeHold(hold: NonNullable<StoredAccount['holds']>[number]): BillingHoldDocument {
  if (typeof hold.tokens === 'number' && hold.split) return hold as BillingHoldDocument;
  // A minute hold from before the migration: holds nothing in tokens.
  return {
    ...hold,
    youtubeId: hold.youtubeId ?? null,
    durationSec: hold.durationSec ?? 0,
    tokens: 0,
    split: { plan: 0, credit: 0, lots: [] },
    planCycleStart: hold.planCycleStart ?? null,
  };
}

/**
 * An account as the token ledger reads it. One written before the token
 * migration lacks the token fields and reads as empty (0 tokens, no debt);
 * the spreads keep its minute fields (`buckets.free`, `minutesUsed`, …), so a
 * write before the migration does not drop what the migration converts.
 */
export function normalizeAccount(stored: BillingAccountDocument): BillingAccountDocument {
  const doc = stored as unknown as StoredAccount;
  const { plan = {}, credit = {}, debt = {} } = doc.buckets ?? {};
  return {
    ...doc,
    buckets: {
      ...doc.buckets,
      plan: {
        ...plan,
        cycleStart: plan.cycleStart ?? null,
        cycleEnd: plan.cycleEnd ?? null,
        tokensGranted: plan.tokensGranted ?? 0,
        tokensUsed: plan.tokensUsed ?? 0,
      },
      credit: { ...credit, tokensAvailable: credit.tokensAvailable ?? 0 },
      debt: { tokens: debt.tokens ?? 0 },
    },
    holds: (doc.holds ?? []).map(normalizeHold),
  };
}

function normalized(doc: BillingAccountDocument | null): BillingAccountDocument | null {
  return doc ? normalizeAccount(doc) : null;
}

/** Lots and recurring grants written in tokens (the migration converts the minute ones). */
const TOKEN_LOT = { tokensGranted: { $exists: true } } as const;

function escapeRegex(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/**
 * Data access for the token ledger. No business rules: BillingService
 * decides what to write; this class writes it (idempotently where the
 * collection has a unique key) and owns the transaction/retry mechanics.
 */
export class BillingRepository {
  private readonly accounts: Collection<BillingAccountDocument>;
  private readonly ledger: Collection<LedgerEntryDocument>;
  private readonly lots: Collection<CreditLotDocument>;
  private readonly recurringGrants: Collection<RecurringGrantDocument>;

  constructor(private readonly db: Db) {
    this.accounts = db.collection('billingAccounts');
    this.ledger = db.collection('ledgerEntries');
    this.lots = db.collection('creditLots');
    this.recurringGrants = db.collection('recurringGrants');
  }

  // ─── Transactions ───

  /**
   * Run `fn` in a multi-document transaction (needs a replica set). The whole
   * unit is retried on `TransientTransactionError` (write conflicts) and on
   * `StaleAccountVersionError`; the commit alone is retried on
   * `UnknownTransactionCommitResult`. Both are bounded — after
   * `maxAttempts` the last error propagates.
   */
  async withTransaction<T>(
    fn: (session: ClientSession) => Promise<T>,
    maxAttempts: number = MAX_TRANSACTION_ATTEMPTS,
  ): Promise<T> {
    const session = this.db.client.startSession();
    try {
      for (let attempt = 1; ; attempt++) {
        try {
          return await this.runTransactionOnce(session, fn);
        } catch (err) {
          if (!isRetryableTransactionError(err) || attempt >= maxAttempts) throw err;
          await retryDelay(attempt);
        }
      }
    } finally {
      await session.endSession();
    }
  }

  private async runTransactionOnce<T>(
    session: ClientSession,
    fn: (session: ClientSession) => Promise<T>,
  ): Promise<T> {
    session.startTransaction({
      readConcern: { level: 'snapshot' },
      writeConcern: { w: 'majority' },
      readPreference: 'primary',
    });
    let result: T;
    try {
      result = await fn(session);
    } catch (err) {
      if (session.inTransaction()) await session.abortTransaction();
      throw err;
    }
    await this.commitWithRetry(session);
    return result;
  }

  private async commitWithRetry(session: ClientSession): Promise<void> {
    for (let attempt = 1; ; attempt++) {
      try {
        await session.commitTransaction();
        return;
      } catch (err) {
        if (!isUnknownCommitResult(err) || attempt >= MAX_COMMIT_ATTEMPTS) {
          if (session.inTransaction()) await session.abortTransaction();
          throw err;
        }
        await retryDelay(attempt);
      }
    }
  }

  // ─── Accounts ───

  /**
   * Insert a new account with its first lots and entries (the signup grant)
   * in one transaction. False when an account for the owner already exists
   * (a concurrent first request won the unique `{ownerType, ownerId}` index).
   */
  async createAccount(
    account: BillingAccountDocument,
    lots: CreditLotDocument[],
    entries: NewLedgerEntry[],
  ): Promise<boolean> {
    try {
      await this.withTransaction(async (session) => {
        await this.accounts.insertOne(account, { session, ignoreUndefined: true });
        await this.insertLots(lots, session);
        for (const entry of entries) {
          await this.ledger.insertOne({ _id: new ObjectId(), ...entry }, { session, ignoreUndefined: true });
        }
      });
      return true;
    } catch (err) {
      if (isDuplicateKey(err)) return false;
      throw err;
    }
  }

  async findAccount(
    ownerType: BillingOwnerType,
    ownerId: string,
    session?: ClientSession,
  ): Promise<BillingAccountDocument | null> {
    return normalized(await this.accounts.findOne({ ownerType, ownerId: new ObjectId(ownerId) }, { session }));
  }

  async findAccountById(accountId: ObjectId, session?: ClientSession): Promise<BillingAccountDocument | null> {
    return normalized(await this.accounts.findOne({ _id: accountId }, { session }));
  }

  async findAccountByHold(requestId: string, session?: ClientSession): Promise<BillingAccountDocument | null> {
    return normalized(await this.accounts.findOne({ 'holds.requestId': requestId }, { session }));
  }

  /**
   * Replace the mutable state only if nobody wrote since `expectedVersion`
   * was read; bumps `version`. Returns null on a stale version.
   */
  async updateAccountConditional(
    accountId: ObjectId,
    expectedVersion: number,
    fields: AccountMutableFields,
    session?: ClientSession,
  ): Promise<BillingAccountDocument | null> {
    const { plan, buckets, holds, caps, capsAuditIds, signupGrantAt } = fields;
    const set = { plan, buckets, holds, caps, capsAuditIds, signupGrantAt, updatedAt: new Date() };
    return normalized(
      await this.accounts.findOneAndUpdate(
        { _id: accountId, version: expectedVersion },
        { $set: set, $inc: { version: 1 } },
        { returnDocument: 'after', session, ignoreUndefined: true },
      ),
    );
  }

  // ─── Ledger ───

  /**
   * Append one entry, idempotent on `idempotencyKey`: a duplicate returns the
   * existing entry with `inserted: false`, never throws. Inside a transaction
   * a duplicate-key write would abort the transaction, so the key is checked
   * first there (a concurrent insert surfaces as a retryable write conflict).
   */
  async insertLedgerEntry(entry: NewLedgerEntry, session?: ClientSession): Promise<InsertLedgerResult> {
    if (session?.inTransaction()) {
      const existing = await this.ledger.findOne({ idempotencyKey: entry.idempotencyKey }, { session });
      if (existing) return { entry: existing, inserted: false };
    }
    const doc: LedgerEntryDocument = { _id: new ObjectId(), ...entry };
    try {
      // Optional fields left undefined are omitted, not stored as null.
      await this.ledger.insertOne(doc, { session, ignoreUndefined: true });
      return { entry: doc, inserted: true };
    } catch (err) {
      if (!isDuplicateKey(err) || session?.inTransaction()) throw err;
      const existing = await this.ledger.findOne({ idempotencyKey: entry.idempotencyKey });
      if (!existing) throw err;
      return { entry: existing, inserted: false };
    }
  }

  async findLedgerByKeys(keys: string[], session?: ClientSession): Promise<LedgerEntryDocument[]> {
    if (keys.length === 0) return [];
    return this.ledger.find({ idempotencyKey: { $in: keys } }, { session }).toArray();
  }

  /** The debits already written for an execution (`debit:exec:<executionId>:…`). */
  async findExecutionDebits(executionId: string, session?: ClientSession): Promise<LedgerEntryDocument[]> {
    const prefix = new RegExp(`^debit:exec:${escapeRegex(executionId)}:`);
    return this.ledger.find({ idempotencyKey: prefix }, { session }).toArray();
  }

  /** Every entry whose `source.ref` is `ref` (a run's requestId, an admin auditId, …). */
  async findLedgerBySourceRef(ref: string, session?: ClientSession): Promise<LedgerEntryDocument[]> {
    return this.ledger.find({ 'source.ref': ref }, { session }).toArray();
  }

  /**
   * Newest first; `before` pages backwards by `createdAt`. One write unit
   * stamps all its entries with the same instant, so a page never ends in the
   * middle of one: the boundary timestamp's remaining entries are appended
   * (a page can run a few rows past `limit`) and `before = last.createdAt`
   * is an exact cursor for the next page.
   */
  async listLedger(accountId: ObjectId, options: LedgerPageOptions): Promise<LedgerEntryDocument[]> {
    const { limit, before } = options;
    const filter = before ? { accountId, createdAt: { $lt: before } } : { accountId };
    const sort = { createdAt: -1, _id: -1 } as const;
    const page = await this.ledger.find(filter).sort(sort).limit(limit).toArray();
    const last = page.at(-1);
    if (!last || page.length < limit) return page;
    const seen = page.filter((e) => e.createdAt.getTime() === last.createdAt.getTime()).map((e) => e._id);
    const rest = await this.ledger
      .find({ accountId, createdAt: last.createdAt, _id: { $nin: seen } })
      .sort(sort)
      .toArray();
    return [...page, ...rest];
  }

  /** The whole ledger of one account, oldest first (projection rebuilds). */
  async listAllLedger(accountId: ObjectId, session?: ClientSession): Promise<LedgerEntryDocument[]> {
    return this.ledger.find({ accountId }, { session }).sort({ createdAt: 1, _id: 1 }).toArray();
  }

  // ─── Credit lots ───

  async insertLots(lots: CreditLotDocument[], session?: ClientSession): Promise<void> {
    if (lots.length === 0) return;
    await this.lots.insertMany(lots, { session, ignoreUndefined: true });
  }

  /**
   * Lots with tokens left, in spend order: dated lots by `expiresAt` asc,
   * then never-expiring ones (Mongo sorts null FIRST, so they are a second
   * query), each by `createdAt` then `_id`. Expired-but-unswept lots are
   * included — spendability is the caller's rule. Sequential on purpose: a
   * transaction's session must not run operations in parallel.
   */
  async listOpenLots(accountId: ObjectId, session?: ClientSession): Promise<CreditLotDocument[]> {
    const open = { accountId, tokensRemaining: { $gt: 0 } };
    const order = { expiresAt: 1, createdAt: 1, _id: 1 } as const;
    const dated = await this.lots.find({ ...open, expiresAt: { $type: 'date' } }, { session }).sort(order).toArray();
    const undated = await this.lots.find({ ...open, expiresAt: null }, { session }).sort(order).toArray();
    return [...dated, ...undated];
  }

  async findLotsByIds(lotIds: ObjectId[], session?: ClientSession): Promise<CreditLotDocument[]> {
    if (lotIds.length === 0) return [];
    return this.lots.find({ _id: { $in: lotIds } }, { session }).toArray();
  }

  /** Every token lot of an account, spent ones included, newest first; `limit` for the admin view. */
  async listLots(accountId: ObjectId, limit?: number, session?: ClientSession): Promise<CreditLotDocument[]> {
    const cursor = this.lots.find({ accountId, ...TOKEN_LOT }, { session }).sort({ createdAt: -1, _id: -1 });
    return (limit === undefined ? cursor : cursor.limit(limit)).toArray();
  }

  /**
   * Move a lot's `tokensRemaining` by `delta`, refusing to take it below
   * zero. Throws `StaleAccountVersionError` when the lot no longer has the
   * tokens (changed since it was read), so the transaction retries.
   */
  async changeLotRemaining(lotId: ObjectId, delta: number, session?: ClientSession): Promise<void> {
    if (delta === 0) return;
    const filter = delta < 0 ? { _id: lotId, tokensRemaining: { $gte: -delta } } : { _id: lotId };
    const result = await this.lots.updateOne(filter, { $inc: { tokensRemaining: delta } }, { session });
    if (result.matchedCount === 0) throw new StaleAccountVersionError(`credit lot ${lotId.toHexString()} changed concurrently`);
  }

  // ─── Recurring grants ───

  async insertRecurringGrant(grant: RecurringGrantDocument, session?: ClientSession): Promise<void> {
    await this.recurringGrants.insertOne(grant, { session });
  }

  async findRecurringGrant(
    accountId: ObjectId,
    grantId: ObjectId,
    session?: ClientSession,
  ): Promise<RecurringGrantDocument | null> {
    return this.recurringGrants.findOne({ _id: grantId, accountId }, { session });
  }

  async findRecurringGrantByAudit(
    accountId: ObjectId,
    auditId: ObjectId,
    session?: ClientSession,
  ): Promise<RecurringGrantDocument | null> {
    return this.recurringGrants.findOne({ accountId, auditId }, { session });
  }

  /** Live (not deleted) token grants, oldest first. */
  async listRecurringGrants(accountId: ObjectId): Promise<RecurringGrantDocument[]> {
    const filter = { accountId, deletedAt: null, tokens: { $exists: true } };
    return this.recurringGrants.find(filter).sort({ createdAt: 1, _id: 1 }).toArray();
  }

  /** Move a grant to its next run, only if nobody did since `expectedNextRunAt` was read. */
  async advanceRecurringGrant(
    grantId: ObjectId,
    expectedNextRunAt: Date,
    next: { lastRunAt: Date; nextRunAt: Date },
    session?: ClientSession,
  ): Promise<boolean> {
    const result = await this.recurringGrants.updateOne(
      { _id: grantId, nextRunAt: expectedNextRunAt },
      { $set: { ...next, updatedAt: next.lastRunAt } },
      { session },
    );
    return result.matchedCount === 1;
  }

  async softDeleteRecurringGrant(grantId: ObjectId, now: Date, session?: ClientSession): Promise<void> {
    await this.recurringGrants.updateOne(
      { _id: grantId, deletedAt: null },
      { $set: { deletedAt: now, active: false, updatedAt: now } },
      { session },
    );
  }
}
