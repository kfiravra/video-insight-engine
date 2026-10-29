import { Db, ObjectId, Collection } from 'mongodb';
import type { AnyBulkWriteOperation, ClientSession } from 'mongodb';
import { BillingRepository, StaleAccountVersionError } from './billing.repository.js';
import { ErasureIdMap, remapKey, replaceDeep } from './billing-erasure.remap.js';
import type { AdminAuditDocument } from './admin-audit.repository.js';
import type {
  BillingAccountDocument,
  CreditLotDocument,
  LedgerEntryDocument,
  RecurringGrantDocument,
} from './billing.documents.js';

/** Stands in for an erased admin's id wherever it was recorded as an actor (`system:<name>` form). */
export const ERASED_ACTOR_ID = 'system:erased-user';

/** What one user's erasure changed in the billing collections and the admin audit trail. */
export interface BillingErasureCounts {
  /** Accounts re-owned to the tombstone id (0 or 1 for a user account). */
  accounts: number;
  /** Other accounts whose open holds named the user. */
  accountsWithHolds: number;
  ledgerEntries: number;
  creditLots: number;
  /** Ledger entries, recurring grants and adminAudit rows the user wrote as an admin. */
  actorRefs: number;
  /** Recurring grants of the erased account, retired and re-keyed. */
  recurringGrants: number;
  /** adminAudit rows about the user, re-keyed onto the tombstone. */
  auditRows: number;
  /** `billingShadow` rows (P1 diagnostics), deleted. */
  shadowRows: number;
}

function nothingErased(): BillingErasureCounts {
  return {
    accounts: 0, accountsWithHolds: 0, ledgerEntries: 0, creditLots: 0, actorRefs: 0, recurringGrants: 0, auditRows: 0, shadowRows: 0,
  };
}

/** One erasure's state, passed through every stage of the transaction. */
interface ErasureContext {
  user: ObjectId;
  userHex: string;
  tombstone: ObjectId;
  now: Date;
  session: ClientSession;
  ids: ErasureIdMap;
  counts: BillingErasureCounts;
}

/**
 * GDPR erasure for the minutes ledger (brief Q12) and the admin audit rows
 * that join to it (C1). The ledger is append-only and its amounts are
 * accounting records, so a user's rows are pseudonymised, never deleted:
 *
 * - every user link (`billingAccounts.ownerId`, `holds[].userId`,
 *   `ledgerEntries.userId`, `adminAudit.targetId` and any copy of the id in
 *   its `before` / `after`) moves to one fresh tombstone ObjectId;
 * - every id that could be recomputed or looked up from the real id — the
 *   account's ledger `auditId` / `source.ref` / `idempotencyKey`, its lots'
 *   `source.ref`, holds' run ids, `capsAuditIds`, recurring grants'
 *   `auditId`, and the `_id` of its adminAudit rows (the P3.8 migration
 *   derives them from the user id) — gets a fresh random value, one per old
 *   value, so the account's own joins still hold;
 * - where the user acted as an admin, `actorId` becomes `system:erased-user`
 *   and adminAudit's `actorEmail` / `ip` go.
 *
 * `userDeletions.originalUserId` keeps the real id, which is exactly why
 * nothing may be left that it can reach. Amounts, lots and `accountId` links
 * stay; the account's recurring grants are retired; the user's `billingShadow`
 * rows are deleted. Other users' rows keep their ids, so the migration's
 * idempotency for live users is untouched. One transaction: a retry after a
 * failure finds everything or nothing moved. A user with nothing to erase
 * returns at once (and needs no replica set).
 */
export class BillingErasureRepository {
  private readonly billing: BillingRepository;
  private readonly accounts: Collection<BillingAccountDocument>;
  private readonly ledger: Collection<LedgerEntryDocument>;
  private readonly lots: Collection<CreditLotDocument>;
  private readonly recurringGrants: Collection<RecurringGrantDocument>;
  private readonly audit: Collection<AdminAuditDocument>;
  private readonly shadow: Collection<{ userId: ObjectId }>;

  constructor(db: Db) {
    this.billing = new BillingRepository(db);
    this.accounts = db.collection('billingAccounts');
    this.ledger = db.collection('ledgerEntries');
    this.lots = db.collection('creditLots');
    this.recurringGrants = db.collection('recurringGrants');
    this.audit = db.collection('adminAudit');
    this.shadow = db.collection('billingShadow');
  }

  async eraseUser(userId: string, now: Date): Promise<BillingErasureCounts> {
    const user = new ObjectId(userId);
    const userHex = user.toHexString();
    if (!(await this.hasRowsToErase(user, userHex))) return nothingErased();
    const tombstone = new ObjectId();
    return this.billing.withTransaction(async (session) => {
      // Fresh per attempt: a retried transaction starts from the untouched rows.
      const ctx: ErasureContext = { user, userHex, tombstone, now, session, ids: new ErasureIdMap(), counts: nothingErased() };
      const accountId = await this.reownAccount(ctx);
      await this.rewriteOtherHolds(ctx);
      await this.rewriteLedger(ctx, accountId);
      if (accountId) {
        await this.rewriteLots(ctx, accountId);
        await this.retireGrants(ctx, accountId);
      }
      await this.scrubActor(ctx);
      await this.rewriteAuditRows(ctx);
      ctx.counts.shadowRows = (await this.shadow.deleteMany({ userId: user }, { session })).deletedCount;
      return ctx.counts;
    });
  }

  private async hasRowsToErase(user: ObjectId, userHex: string): Promise<boolean> {
    const probes = await Promise.all([
      this.accounts.countDocuments({ $or: [{ ownerType: 'user', ownerId: user }, { 'holds.userId': user }] }, { limit: 1 }),
      this.ledger.countDocuments({ $or: [{ userId: user }, { actorId: userHex }] }, { limit: 1 }),
      this.recurringGrants.countDocuments({ actorId: userHex }, { limit: 1 }),
      this.audit.countDocuments({ $or: [{ targetId: userHex }, { actorId: userHex }] }, { limit: 1 }),
      this.shadow.countDocuments({ userId: user }, { limit: 1 }),
    ]);
    return probes.some((count) => count > 0);
  }

  /** The user's account under the tombstone owner, its holds' run ids and caps audit ids re-keyed; null without one. */
  private async reownAccount(ctx: ErasureContext): Promise<ObjectId | null> {
    const { user, tombstone, now, session, ids } = ctx;
    const account = await this.accounts.findOne({ ownerType: 'user', ownerId: user }, { session });
    if (!account) return null;
    const holds = account.holds.map((hold) => ({
      ...hold,
      requestId: ids.runId(hold.requestId),
      userId: hold.userId.equals(user) ? tombstone : hold.userId,
    }));
    const capsAuditIds = account.capsAuditIds?.map((auditId) => ids.auditId(auditId).toHexString());
    const result = await this.accounts.updateOne(
      { _id: account._id, version: account.version },
      { $set: { ownerId: tombstone, holds, updatedAt: now, ...(capsAuditIds && { capsAuditIds }) }, $inc: { version: 1 } },
      { session },
    );
    if (result.matchedCount === 0) throw new StaleAccountVersionError();
    ctx.counts.accounts = 1;
    return account._id;
  }

  /** Holds the user opened on an account that is not theirs (team accounts, later). */
  private async rewriteOtherHolds(ctx: ErasureContext): Promise<void> {
    const { user, tombstone, now, session } = ctx;
    const result = await this.accounts.updateMany(
      { 'holds.userId': user },
      { $set: { 'holds.$[hold].userId': tombstone, updatedAt: now }, $inc: { version: 1 } },
      { arrayFilters: [{ 'hold.userId': user }], session },
    );
    ctx.counts.accountsWithHolds = result.modifiedCount;
  }

  /** Every entry of the account re-keyed; an entry elsewhere that names the user loses only that link. */
  private async rewriteLedger(ctx: ErasureContext, accountId: ObjectId | null): Promise<void> {
    const { user, tombstone, session } = ctx;
    const entries = accountId ? await this.ledger.find({ accountId }, { session }).toArray() : [];
    const ops: AnyBulkWriteOperation<LedgerEntryDocument>[] = entries.map((entry) => ({
      updateOne: { filter: { _id: entry._id }, update: { $set: this.rewrittenEntry(ctx, entry) } },
    }));
    if (ops.length > 0) await this.ledger.bulkWrite(ops, { session, ordered: true });
    const elsewhere = await this.ledger.updateMany({ userId: user }, { $set: { userId: tombstone } }, { session });
    ctx.counts.ledgerEntries = ops.length + elsewhere.modifiedCount;
  }

  private rewrittenEntry(ctx: ErasureContext, entry: LedgerEntryDocument): Partial<LedgerEntryDocument> & Record<string, unknown> {
    const { user, tombstone, ids } = ctx;
    const ref = ids.sourceRef(entry.source.kind, entry.source.ref);
    let idempotencyKey = remapKey(entry.idempotencyKey, entry.source.ref, ref);
    const set: Partial<LedgerEntryDocument> & Record<string, unknown> = { 'source.ref': ref };
    if (entry.auditId) {
      const auditId = ids.auditId(entry.auditId);
      idempotencyKey = remapKey(idempotencyKey, entry.auditId.toHexString(), auditId.toHexString());
      set.auditId = auditId;
    }
    set.idempotencyKey = idempotencyKey;
    if (entry.userId.equals(user)) set.userId = tombstone;
    return set;
  }

  private async rewriteLots(ctx: ErasureContext, accountId: ObjectId): Promise<void> {
    const { session, ids } = ctx;
    const lots = await this.lots.find({ accountId }, { session }).toArray();
    const ops: AnyBulkWriteOperation<CreditLotDocument>[] = lots.map((lot) => ({
      updateOne: { filter: { _id: lot._id }, update: { $set: { 'source.ref': ids.sourceRef(lot.source.kind, lot.source.ref) } } },
    }));
    if (ops.length > 0) await this.lots.bulkWrite(ops, { session, ordered: true });
    ctx.counts.creditLots = ops.length;
  }

  /** The account's grants retire (an erased owner gets no more lots) and lose their adminAudit id. */
  private async retireGrants(ctx: ErasureContext, accountId: ObjectId): Promise<void> {
    const { now, session, ids } = ctx;
    const grants = await this.recurringGrants.find({ accountId }, { session }).toArray();
    const ops: AnyBulkWriteOperation<RecurringGrantDocument>[] = grants.map((grant) => ({
      updateOne: {
        filter: { _id: grant._id },
        update: { $set: { auditId: ids.auditId(grant.auditId), active: false, deletedAt: grant.deletedAt ?? now, updatedAt: now } },
      },
    }));
    if (ops.length > 0) await this.recurringGrants.bulkWrite(ops, { session, ordered: true });
    ctx.counts.recurringGrants = ops.length;
  }

  /** What the user did as an admin keeps its record but no longer names them. Sequential on the session. */
  private async scrubActor(ctx: ErasureContext): Promise<void> {
    const { userHex, session } = ctx;
    const update = { $set: { actorId: ERASED_ACTOR_ID } };
    const entries = await this.ledger.updateMany({ actorId: userHex }, update, { session });
    const grants = await this.recurringGrants.updateMany({ actorId: userHex }, update, { session });
    ctx.counts.actorRefs = entries.modifiedCount + grants.modifiedCount;
  }

  /**
   * adminAudit rows about the user (any `targetType`: user, role, …) move to
   * the tombstone under a re-keyed `_id` — the ledger's `auditId` of the
   * account follows the same map — and rows the user wrote as an admin lose
   * the actor's id, email and IP (I13). Re-keying is insert + delete: `_id`
   * cannot be updated.
   */
  private async rewriteAuditRows(ctx: ErasureContext): Promise<void> {
    const { userHex, tombstone, session, ids } = ctx;
    const tombstoneHex = tombstone.toHexString();
    const rows = await this.audit.find({ targetId: userHex }, { session }).toArray();
    for (const row of rows) {
      const moved: AdminAuditDocument = {
        ...row,
        _id: ids.auditId(row._id),
        targetId: tombstoneHex,
        ...(row.before !== undefined && { before: replaceDeep(row.before, userHex, tombstoneHex) }),
        ...(row.after !== undefined && { after: replaceDeep(row.after, userHex, tombstoneHex) }),
      };
      await this.audit.insertOne(moved, { session });
      await this.audit.deleteOne({ _id: row._id }, { session });
    }
    ctx.counts.auditRows = rows.length;
    const acted = await this.audit.updateMany(
      { actorId: userHex },
      { $set: { actorId: ERASED_ACTOR_ID }, $unset: { actorEmail: '', ip: '' } },
      { session },
    );
    ctx.counts.actorRefs += acted.modifiedCount;
  }
}
