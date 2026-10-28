import { Db, ObjectId, Collection } from 'mongodb';
import { isRole, rolesGrant } from '@vie/types';
import type { Permission, Role, RoleSource } from '@vie/types';

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
  // Legacy admin flag. Superseded by `roles`: nothing writes it any more, but a
  // user with `role: 'admin'` and no `roles` array still reads as an admin
  // (`effectiveRoles`) until the billing migration unsets it. `setRoles`
  // clears it on demotion so it can never resurrect an admin.
  role?: 'admin';
  // RBAC. An array (even empty) is authoritative; absent means "fall back to
  // the legacy `role`". Written only by `setRoles` — never by a request.
  roles?: Role[];
  // `env` = reconciled with ADMIN_EMAILS at boot; `granted` = set by an admin
  // or a migration, never touched by boot seeding. Unset on non-admins.
  roleSource?: RoleSource;
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

/**
 * Slim role view of a user for seeding and admin listings. Carries the email
 * (the seeder matches on it) but no hash or profile data.
 */
export interface UserRoleSummary {
  id: string;
  email: string;
  roles: Role[];
  /** `null` for users without roles; a legacy `role: 'admin'` reads as `granted`. */
  roleSource: RoleSource | null;
  deletedAt: Date | null;
}

type RoleFields = Pick<UserDocument, 'roles' | 'role'>;
type PermissionFields = RoleFields & Pick<UserDocument, 'deletedAt'>;

// Permission checks read only what decides them; the summaries add the email the seeder matches on.
const PERMISSION_PROJECTION = { roles: 1, role: 1, deletedAt: 1 } as const;
const ROLE_SUMMARY_PROJECTION = { ...PERMISSION_PROJECTION, roleSource: 1, email: 1 } as const;
const OBJECT_ID_HEX = /^[a-f0-9]{24}$/i;

/**
 * Roles a user effectively holds. `roles` wins whenever it is an array, so a
 * demoted user (`roles: []`) is not revived by a leftover legacy `role`;
 * otherwise a legacy `role: 'admin'` reads as `['admin']`. Unknown values in
 * `roles` are dropped.
 */
export function effectiveRoles(doc: RoleFields | null | undefined): Role[] {
  if (!doc) return [];
  if (Array.isArray(doc.roles)) return doc.roles.filter(isRole);
  return doc.role === 'admin' ? ['admin'] : [];
}

/** A soft-deleted account holds no permissions: its sessions are dead and it must not act as an admin. */
function grantsPermission(doc: PermissionFields | null, permission: Permission): boolean {
  return doc !== null && !doc.deletedAt && rolesGrant(effectiveRoles(doc), permission);
}

function toRoleSummary(doc: UserDocument): UserRoleSummary {
  const roles = effectiveRoles(doc);
  return {
    id: doc._id.toString(),
    email: doc.email,
    roles,
    roleSource: roles.length === 0 ? null : doc.roleSource === 'env' ? 'env' : 'granted',
    deletedAt: doc.deletedAt ?? null,
  };
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

  /**
   * Whether the user may submit a cold-media benchmark run (1a.7): the
   * `cold:run` permission (held by the admin role) or the eval account, both
   * DB-only flags no request can set. Same strict `isEvalUser` check as above.
   */
  async mayRunCold(userId: string): Promise<boolean> {
    const user = await this.collection.findOne(
      { _id: new ObjectId(userId) },
      { projection: { ...PERMISSION_PROJECTION, isEvalUser: 1 } }
    );
    return user?.isEvalUser === true || grantsPermission(user, 'cold:run');
  }

  /**
   * Whether the user's roles grant `permission`. A malformed id, an unknown
   * user and a soft-deleted account all answer false.
   */
  async hasPermission(userId: string, permission: Permission): Promise<boolean> {
    if (!OBJECT_ID_HEX.test(userId)) return false;
    const user = await this.collection.findOne(
      { _id: new ObjectId(userId) },
      { projection: PERMISSION_PROJECTION }
    );
    return grantsPermission(user, permission);
  }

  /** Every account that effectively holds the admin role, soft-deleted ones included (see `deletedAt`). */
  async listAdmins(): Promise<UserRoleSummary[]> {
    const docs = await this.collection
      .find(
        { $or: [{ roles: 'admin' }, { roles: { $exists: false }, role: 'admin' }] },
        { projection: ROLE_SUMMARY_PROJECTION }
      )
      .toArray();
    return docs.map(toRoleSummary);
  }

  /**
   * Role summaries for the given emails, matched ignoring case (collation
   * strength 2) so a hand-inserted mixed-case address still resolves. A boot-time
   * lookup over a handful of emails; the users collection is small.
   */
  async findRoleSummariesByEmails(emails: string[]): Promise<UserRoleSummary[]> {
    if (emails.length === 0) return [];
    const docs = await this.collection
      .find(
        { email: { $in: emails } },
        { projection: ROLE_SUMMARY_PROJECTION, collation: { locale: 'en', strength: 2 } }
      )
      .toArray();
    return docs.map(toRoleSummary);
  }

  /**
   * Replace the user's roles. A non-empty list is stored with its source; an
   * empty list demotes: `roleSource` and the legacy `role` are unset so
   * neither can revive the admin role. Returns false when the user is unknown.
   */
  async setRoles(userId: string, roles: Role[], roleSource: RoleSource): Promise<boolean> {
    const unique = [...new Set(roles)];
    const updatedAt = new Date();
    const update =
      unique.length > 0
        ? { $set: { roles: unique, roleSource, updatedAt } }
        : { $set: { roles: [], updatedAt }, $unset: { roleSource: '' as const, role: '' as const } };
    const result = await this.collection.updateOne({ _id: new ObjectId(userId) }, update);
    return result.matchedCount === 1;
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
