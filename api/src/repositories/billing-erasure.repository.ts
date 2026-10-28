import { Db, ObjectId, Collection } from 'mongodb';
import type { ClientSession } from 'mongodb';
import { BillingRepository } from './billing.repository.js';
import type { BillingAccountDocument, LedgerEntryDocument, RecurringGrantDocument } from './billing.documents.js';

/** Stands in for an erased admin's id wherever it was recorded as an actor (`system:<name>` form). */
export const ERASED_ACTOR_ID = 'system:erased-user';

/** What one user's erasure changed in the billing collections. */
export interface BillingErasureCounts {
  /** Accounts re-owned to the tombstone id (0 or 1 for a user account). */
  accounts: number;
  /** Accounts whose open holds named the user. */
  accountsWithHolds: number;
  ledgerEntries: number;
  /** Ledger entries and recurring grants the user wrote as an admin. */
  actorRefs: number;
  /** Recurring grants of the erased account, retired. */
  recurringGrants: number;
  /** `billingShadow` rows (P1 diagnostics), deleted. */
  shadowRows: number;
}

const NOTHING_ERASED: BillingErasureCounts = {
  accounts: 0, accountsWithHolds: 0, ledgerEntries: 0, actorRefs: 0, recurringGrants: 0, shadowRows: 0,
};

/**
 * GDPR erasure for the minutes ledger (brief Q12). The ledger is append-only
 * and its amounts are accounting records, so a user's rows are pseudonymised,
 * never deleted: every link to the user (`billingAccounts.ownerId`,
 * `holds[].userId`, `ledgerEntries.userId`) moves to one fresh tombstone
 * ObjectId that names nobody — `userDeletions.originalUserId` keeps the real
 * id, so leaving it in place would not be erasure. Amounts, lots and
 * `accountId` links stay. The recurring grants of the account are retired
 * and the user's `billingShadow` rows deleted. One transaction, so a retry
 * after a failure finds everything or nothing moved; a user with no billing
 * rows returns at once (and needs no replica set).
 */
export class BillingErasureRepository {
  private readonly billing: BillingRepository;
  private readonly accounts: Collection<BillingAccountDocument>;
  private readonly ledger: Collection<LedgerEntryDocument>;
  private readonly recurringGrants: Collection<RecurringGrantDocument>;
  private readonly shadow: Collection<{ userId: ObjectId }>;

  constructor(db: Db) {
    this.billing = new BillingRepository(db);
    this.accounts = db.collection('billingAccounts');
    this.ledger = db.collection('ledgerEntries');
    this.recurringGrants = db.collection('recurringGrants');
    this.shadow = db.collection('billingShadow');
  }

  async eraseUser(userId: string, now: Date): Promise<BillingErasureCounts> {
    const user = new ObjectId(userId);
    if (!(await this.hasBillingRows(user, userId))) return NOTHING_ERASED;
    const tombstone = new ObjectId();
    return this.billing.withTransaction(async (session) => {
      const account = await this.reownAccount(user, tombstone, now, session);
      const holds = await this.accounts.updateMany(
        { 'holds.userId': user },
        { $set: { 'holds.$[hold].userId': tombstone, updatedAt: now }, $inc: { version: 1 } },
        { arrayFilters: [{ 'hold.userId': user }], session },
      );
      const entries = await this.ledger.updateMany({ userId: user }, { $set: { userId: tombstone } }, { session });
      const actorRefs = await this.scrubActor(userId, session);
      const grants = account ? await this.retireGrants(account, now, session) : 0;
      const shadow = await this.shadow.deleteMany({ userId: user }, { session });
      return {
        accounts: account ? 1 : 0,
        accountsWithHolds: holds.modifiedCount,
        ledgerEntries: entries.modifiedCount,
        actorRefs,
        recurringGrants: grants,
        shadowRows: shadow.deletedCount,
      };
    });
  }

  private async hasBillingRows(user: ObjectId, userId: string): Promise<boolean> {
    const probes = await Promise.all([
      this.accounts.countDocuments({ $or: [{ ownerType: 'user', ownerId: user }, { 'holds.userId': user }] }, { limit: 1 }),
      this.ledger.countDocuments({ $or: [{ userId: user }, { actorId: userId }] }, { limit: 1 }),
      this.recurringGrants.countDocuments({ actorId: userId }, { limit: 1 }),
      this.shadow.countDocuments({ userId: user }, { limit: 1 }),
    ]);
    return probes.some((count) => count > 0);
  }

  /** The account's id, once its owner is the tombstone; null when the user had none. */
  private async reownAccount(user: ObjectId, tombstone: ObjectId, now: Date, session: ClientSession): Promise<ObjectId | null> {
    const account = await this.accounts.findOneAndUpdate(
      { ownerType: 'user', ownerId: user },
      { $set: { ownerId: tombstone, updatedAt: now }, $inc: { version: 1 } },
      { session, projection: { _id: 1 } },
    );
    return account?._id ?? null;
  }

  /** Sequential: operations of one transaction never run in parallel on its session. */
  private async scrubActor(userId: string, session: ClientSession): Promise<number> {
    const update = { $set: { actorId: ERASED_ACTOR_ID } };
    const entries = await this.ledger.updateMany({ actorId: userId }, update, { session });
    const grants = await this.recurringGrants.updateMany({ actorId: userId }, update, { session });
    return entries.modifiedCount + grants.modifiedCount;
  }

  private async retireGrants(accountId: ObjectId, now: Date, session: ClientSession): Promise<number> {
    const retired = await this.recurringGrants.updateMany(
      { accountId, deletedAt: null },
      { $set: { active: false, deletedAt: now, updatedAt: now } },
      { session },
    );
    return retired.modifiedCount;
  }
}
