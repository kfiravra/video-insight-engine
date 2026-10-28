import { Db, ObjectId, Collection } from 'mongodb';

/**
 * Counts captured at the moment a user is hard-deleted. Each step of the
 * cascade contributes its own row count; zero is a legitimate value (e.g.
 * the user never created a folder).
 */
export interface DeletionCounts {
  userVideos: number;
  folders: number;
  userCosts: number;
  userCostAdjustments: number;
  idempotencyKeys: number;
  assistantNotes: number;
  qdrantPoints: number;
  s3Objects: number;
}

export type DeletionInitiator = 'self' | 'admin' | 'scheduler';

/**
 * Who asked for a soft delete. Stored on the user until the scheduler runs the
 * cascade, so the `userDeletions` row can name the admin instead of the
 * scheduler. Holds no PII: `adminId` is an operator id, never an email.
 */
export interface DeletionOrigin {
  initiatedBy: DeletionInitiator;
  adminId: string | null;
  reason: string | null;
}

/**
 * Audit row for a completed account deletion. Retained indefinitely with
 * **no PII**:
 *  - `emailHash` proves the cascade ran for a given email without storing it.
 *  - `originalUserId` is the string-form ObjectId of the deleted user.
 *  - `reason`/`initiatedBy` capture admin/legal context for compliance review.
 */
export interface UserDeletionDocument {
  _id: ObjectId;
  originalUserId: string;
  emailHash: string;
  initiatedBy: DeletionInitiator;
  adminId?: string | null;
  reason?: string | null;
  counts: DeletionCounts;
  startedAt: Date;
  completedAt: Date;
  durationMs: number;
  /** Errors per saga step that did not abort the cascade. */
  warnings?: string[];
}

export interface CreateDeletionAuditData {
  originalUserId: string;
  emailHash: string;
  initiatedBy: DeletionInitiator;
  adminId?: string | null;
  reason?: string | null;
  counts: DeletionCounts;
  startedAt: Date;
  completedAt: Date;
  warnings?: string[];
}

export class UserDeletionRepository {
  private readonly collection: Collection<UserDeletionDocument>;

  constructor(db: Db) {
    this.collection = db.collection('userDeletions');
  }

  async insert(data: CreateDeletionAuditData): Promise<UserDeletionDocument> {
    const doc: Omit<UserDeletionDocument, '_id'> = {
      originalUserId: data.originalUserId,
      emailHash: data.emailHash,
      initiatedBy: data.initiatedBy,
      adminId: data.adminId ?? null,
      reason: data.reason ?? null,
      counts: data.counts,
      startedAt: data.startedAt,
      completedAt: data.completedAt,
      durationMs: data.completedAt.getTime() - data.startedAt.getTime(),
      warnings: data.warnings && data.warnings.length > 0 ? data.warnings : undefined,
    };
    const result = await this.collection.insertOne(doc as UserDeletionDocument);
    return { ...doc, _id: result.insertedId } as UserDeletionDocument;
  }

  async findByOriginalUserId(userId: string): Promise<UserDeletionDocument | null> {
    return this.collection.findOne({ originalUserId: userId });
  }

  async findByEmailHash(emailHash: string): Promise<UserDeletionDocument | null> {
    return this.collection.findOne({ emailHash });
  }
}
