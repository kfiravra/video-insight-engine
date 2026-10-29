import { Db, ObjectId, Collection } from 'mongodb';
import type { Filter } from 'mongodb';
import type { AdminAuditTargetType } from '@vie/types';

/**
 * One `adminAudit` row. Append-only: nothing in the code base updates or
 * deletes these. Retained indefinitely, like the other audit collections.
 * `before` / `after` are plain JSON (the service normalises them) and carry no
 * secrets.
 */
export interface AdminAuditDocument {
  _id: ObjectId;
  actorId: string;
  actorEmail?: string;
  action: string;
  targetType: AdminAuditTargetType;
  targetId: string;
  before?: unknown;
  after?: unknown;
  reason?: string;
  requestId?: string;
  ip?: string;
  requestHash?: string;
  createdAt: Date;
}

export type CreateAdminAuditData = Omit<AdminAuditDocument, '_id'> & {
  /** Pre-generated ObjectId hex, for callers (ledger writes) that reference the row. */
  auditId?: string;
};

/**
 * Where the previous page ended. With an `id`, rows sharing the cursor's
 * millisecond are split by `_id` (the sort's tie-break), so none are skipped or
 * repeated; without one, every row created strictly before `createdAt` follows.
 */
export interface AdminAuditCursor {
  createdAt: Date;
  id?: string;
}

export interface FindAdminAuditFilter {
  targetId?: string;
  actorId?: string;
  before?: AdminAuditCursor;
  limit: number;
}

export class AdminAuditRepository {
  private readonly collection: Collection<AdminAuditDocument>;

  constructor(db: Db) {
    this.collection = db.collection('adminAudit');
  }

  async insert(data: CreateAdminAuditData): Promise<AdminAuditDocument> {
    const { auditId, ...fields } = data;
    const doc: AdminAuditDocument = {
      ...withoutUndefined(fields),
      _id: auditId ? new ObjectId(auditId) : new ObjectId(),
    };
    await this.collection.insertOne(doc);
    return doc;
  }

  /** The request hash stored on the row `auditId`, or null when there is no such row or it has none. */
  async findRequestHash(auditId: string): Promise<string | null> {
    const row = await this.collection.findOne({ _id: new ObjectId(auditId) }, { projection: { requestHash: 1 } });
    return row?.requestHash ?? null;
  }

  /** Newest first. */
  async find(filter: FindAdminAuditFilter): Promise<AdminAuditDocument[]> {
    const query: Filter<AdminAuditDocument> = {};
    if (filter.targetId !== undefined) query.targetId = filter.targetId;
    if (filter.actorId !== undefined) query.actorId = filter.actorId;
    if (filter.before !== undefined) Object.assign(query, cursorFilter(filter.before));
    return this.collection
      .find(query)
      .sort({ createdAt: -1, _id: -1 })
      .limit(filter.limit)
      .toArray();
  }
}

function cursorFilter({ createdAt, id }: AdminAuditCursor): Filter<AdminAuditDocument> {
  if (id === undefined) return { createdAt: { $lt: createdAt } };
  return { $or: [{ createdAt: { $lt: createdAt } }, { createdAt, _id: { $lt: new ObjectId(id) } }] };
}

/** The driver stores `undefined` as `null` unless told otherwise; absent fields should stay absent. */
function withoutUndefined<T extends object>(value: T): T {
  return Object.fromEntries(Object.entries(value).filter(([, v]) => v !== undefined)) as T;
}
