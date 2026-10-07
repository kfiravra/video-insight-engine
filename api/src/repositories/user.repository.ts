import { Db, ObjectId, Collection } from 'mongodb';

export interface UserDocument {
  _id: ObjectId;
  email: string;
  passwordHash: string;
  name: string;
  preferences: {
    defaultSummarizedFolder: ObjectId | null;
    theme: 'light' | 'dark' | 'system';
  };
  usage: {
    videosThisMonth: number;
    videosResetAt: Date;
  };
  lastLoginAt?: Date;
  createdAt: Date;
  updatedAt: Date;
  // Grants access to the vie-admin panel's email/password login. Only ever
  // set by scripts/create-admin.ts — the public register flow never writes it.
  role?: 'admin';
  // Marks the golden-eval account (D25). Set ONLY by an admin in the DB
  // (mongosh) — no API route writes it and no request input can set it. Every
  // video this user submits becomes an eval-flagged version that other users
  // are never served and that never prunes their versions.
  isEvalUser?: boolean;
  // Profile fields (V1.5)
  username?: string;
  displayName?: string;
  referralSlug?: string;
  // Payment/tier fields (V1.4)
  tier?: string;
  paddleCustomerId?: string;
  paddleSubscriptionId?: string;
  tierUpdatedAt?: Date;
  // GDPR soft-delete (Art. 17)
  // `deletedAt` set → user has requested erasure. Auth rejects login.
  // `hardDeleteAt` is the scheduled wall-clock time the cascade fires (default +30d).
  // `legalHold: true` blocks the scheduler from running the cascade even after hardDeleteAt.
  deletedAt?: Date | null;
  hardDeleteAt?: Date | null;
  legalHold?: boolean;
}

export interface CreateUserData {
  email: string;
  passwordHash: string;
  name: string;
}

/**
 * Slim projection returned by `findExpiredSoftDeletes`. Carries only `_id`
 * so callers cannot accidentally log password hashes or other PII.
 */
export interface ExpiredSoftDeleteSummary {
  _id: ObjectId;
}

export class UserRepository {
  private readonly collection: Collection<UserDocument>;

  constructor(db: Db) {
    this.collection = db.collection('users');
  }

  async findById(userId: string): Promise<UserDocument | null> {
    return this.collection.findOne({ _id: new ObjectId(userId) });
  }

  async findByEmail(email: string): Promise<UserDocument | null> {
    return this.collection.findOne({ email });
  }

  async findByUsername(username: string): Promise<UserDocument | null> {
    return this.collection.findOne({ username });
  }

  async create(data: CreateUserData): Promise<UserDocument> {
    const now = new Date();
    const doc: Omit<UserDocument, '_id'> = {
      email: data.email,
      passwordHash: data.passwordHash,
      name: data.name,
      preferences: {
        defaultSummarizedFolder: null,
        theme: 'system',
      },
      usage: {
        videosThisMonth: 0,
        videosResetAt: now,
      },
      createdAt: now,
      updatedAt: now,
    };

    const result = await this.collection.insertOne(doc as UserDocument);
    return { ...doc, _id: result.insertedId } as UserDocument;
  }

  async updateLastLogin(userId: string): Promise<void> {
    await this.collection.updateOne(
      { _id: new ObjectId(userId) },
      { $set: { lastLoginAt: new Date() } }
    );
  }

  async update(userId: string, updates: Partial<Omit<UserDocument, '_id'>>): Promise<void> {
    await this.collection.updateOne(
      { _id: new ObjectId(userId) },
      { $set: { ...updates, updatedAt: new Date() } }
    );
  }

  /**
   * Whether the user is the DB-flagged eval account. Strict `=== true` so a
   * malformed value (string, number) never enables eval behaviour; an unknown
   * user is simply not an eval user.
   */
  async isEvalUser(userId: string): Promise<boolean> {
    const user = await this.collection.findOne(
      { _id: new ObjectId(userId) },
      { projection: { isEvalUser: 1 } }
    );
    return user?.isEvalUser === true;
  }

  async findByPaddleCustomerId(customerId: string): Promise<UserDocument | null> {
    return this.collection.findOne({ paddleCustomerId: customerId });
  }

  async getTier(userId: string): Promise<string | undefined> {
    const user = await this.collection.findOne(
      { _id: new ObjectId(userId) },
      { projection: { tier: 1 } }
    );
    return user?.tier;
  }

  /** Atomic update that returns the previous document state (for tier transitions) */
  async updateAndReturnPrevious(userId: string, updates: Partial<Omit<UserDocument, '_id'>>): Promise<UserDocument | null> {
    return this.collection.findOneAndUpdate(
      { _id: new ObjectId(userId) },
      { $set: { ...updates, updatedAt: new Date() } },
      { returnDocument: 'before' }
    );
  }

  /**
   * Mark a user for deletion. Idempotent: a second call returns null without
   * resetting the timer. `deletedAt` is supplied by the caller so the service
   * can use an injected clock in tests.
   */
  async markSoftDeleted(
    userId: string,
    hardDeleteAt: Date,
    deletedAt: Date = new Date(),
  ): Promise<UserDocument | null> {
    return this.collection.findOneAndUpdate(
      { _id: new ObjectId(userId), deletedAt: { $in: [null, undefined] } },
      { $set: { deletedAt, hardDeleteAt, updatedAt: deletedAt } },
      { returnDocument: 'after' }
    );
  }

  /** Restore a soft-deleted account if the 30-day window has not closed. */
  async clearSoftDelete(userId: string): Promise<UserDocument | null> {
    return this.collection.findOneAndUpdate(
      { _id: new ObjectId(userId) },
      { $set: { deletedAt: null, hardDeleteAt: null, updatedAt: new Date() } },
      { returnDocument: 'after' }
    );
  }

  /**
   * Users whose hard-delete window has elapsed and who aren't on legal hold.
   * Projects to `_id` only — the scheduler only needs IDs to feed into the
   * cascade, and never touching `passwordHash` here eliminates a logging-leak
   * surface (any downstream `logger.info({ users })` would have dumped them).
   */
  async findExpiredSoftDeletes(
    now: Date,
    limit = 50,
  ): Promise<ExpiredSoftDeleteSummary[]> {
    const cursor = this.collection.find(
      {
        deletedAt: { $ne: null },
        hardDeleteAt: { $lte: now },
        legalHold: { $ne: true },
      },
      { projection: { _id: 1 } },
    );
    const docs = (await cursor.limit(limit).toArray()) as unknown as ExpiredSoftDeleteSummary[];
    return docs;
  }

  /** Final hard delete. Repositories below the saga should already have run. */
  async hardDelete(userId: string): Promise<boolean> {
    const result = await this.collection.deleteOne({ _id: new ObjectId(userId) });
    return result.deletedCount === 1;
  }
}
