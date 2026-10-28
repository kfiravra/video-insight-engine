import { Db, ObjectId, Collection } from 'mongodb';
import type { BillingShadowDecision, BillingShadowPath, BucketSplit } from '@vie/types';

export interface ShadowNewDecision extends BillingShadowDecision {
  split?: BucketSplit;
  reason?: string;
}

/** `billingShadow` — the old gate's decision beside the ledger's, one row per requestId (Contract 4). */
export interface BillingShadowDocument {
  _id: ObjectId;
  requestId: string;
  userId: ObjectId;
  youtubeId: string;
  path: BillingShadowPath;
  oldDecision: BillingShadowDecision;
  newDecision: ShadowNewDecision;
  classification: 'agree' | 'policy_delta' | 'disagree';
  policyRef?: string;
  createdAt: Date;
  updatedAt: Date;
}

export type ShadowRowFields = Omit<BillingShadowDocument, '_id' | 'createdAt' | 'updatedAt'>;

function isDuplicateKey(err: unknown): boolean {
  return (err as { code?: number }).code === 11000;
}

export class BillingShadowRepository {
  private readonly collection: Collection<BillingShadowDocument>;

  constructor(db: Db) {
    this.collection = db.collection('billingShadow');
  }

  async findByRequestId(requestId: string): Promise<BillingShadowDocument | null> {
    return this.collection.findOne({ requestId });
  }

  /**
   * Write the row's current state (one row per requestId, rewritten on every
   * path transition). Two first writes racing on the unique index resolve to
   * a plain update of the winner's row.
   */
  async upsert(fields: ShadowRowFields, now: Date = new Date()): Promise<void> {
    const { policyRef, ...rest } = fields;
    const update = {
      $set: { ...rest, updatedAt: now, ...(policyRef !== undefined && { policyRef }) },
      $setOnInsert: { createdAt: now },
      ...(policyRef === undefined && { $unset: { policyRef: '' as const } }),
    };
    try {
      await this.collection.updateOne({ requestId: fields.requestId }, update, { upsert: true, ignoreUndefined: true });
    } catch (err) {
      if (!isDuplicateKey(err)) throw err;
      await this.collection.updateOne({ requestId: fields.requestId }, update, { ignoreUndefined: true });
    }
  }
}
