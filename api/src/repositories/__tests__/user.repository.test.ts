import { describe, it, expect, beforeAll, afterAll, beforeEach } from 'vitest';
import { MongoMemoryServer } from 'mongodb-memory-server';
import { MongoClient, Db, ObjectId } from 'mongodb';
import { UserRepository, CreateUserData } from '../user.repository.js';

describe('UserRepository', () => {
  let mongod: MongoMemoryServer;
  let client: MongoClient;
  let db: Db;
  let repository: UserRepository;

  beforeAll(async () => {
    mongod = await MongoMemoryServer.create();
    const uri = mongod.getUri();
    client = new MongoClient(uri);
    await client.connect();
    db = client.db('test');
    repository = new UserRepository(db);
  });

  afterAll(async () => {
    await client.close();
    await mongod.stop();
  });

  beforeEach(async () => {
    await db.collection('users').deleteMany({});
  });

  // Test data factory
  function createUserData(overrides: Partial<CreateUserData> = {}): CreateUserData {
    const uniqueId = new ObjectId().toString().slice(0, 8);
    return {
      email: `test-${uniqueId}@example.com`,
      passwordHash: '$2b$10$hashedpassword',
      name: 'Test User',
      ...overrides,
    };
  }

  describe('create', () => {
    it('should create a user with required fields', async () => {
      const data = createUserData({
        email: 'john@example.com',
        name: 'John Doe',
      });

      const user = await repository.create(data);

      expect(user._id).toBeDefined();
      expect(user.email).toBe('john@example.com');
      expect(user.name).toBe('John Doe');
      expect(user.passwordHash).toBe(data.passwordHash);
      expect(user.createdAt).toBeInstanceOf(Date);
      expect(user.updatedAt).toBeInstanceOf(Date);
    });

    it('should initialize default preferences', async () => {
      const user = await repository.create(createUserData());

      expect(user.preferences).toEqual({
        defaultSummarizedFolder: null,
        theme: 'system',
      });
    });

    it('should initialize usage tracking', async () => {
      const user = await repository.create(createUserData());

      expect(user.usage.videosThisMonth).toBe(0);
      expect(user.usage.videosResetAt).toBeInstanceOf(Date);
    });

    it('should not have lastLoginAt initially', async () => {
      const user = await repository.create(createUserData());

      expect(user.lastLoginAt).toBeUndefined();
    });
  });

  describe('findById', () => {
    it('should find user by id', async () => {
      const created = await repository.create(createUserData());

      const found = await repository.findById(created._id.toString());

      expect(found).not.toBeNull();
      expect(found?._id.toString()).toBe(created._id.toString());
    });

    it('should return null when user not found', async () => {
      const nonExistentId = new ObjectId().toString();

      const found = await repository.findById(nonExistentId);

      expect(found).toBeNull();
    });
  });

  describe('findByEmail', () => {
    it('should find user by email', async () => {
      const data = createUserData({ email: 'findme@example.com' });
      await repository.create(data);

      const found = await repository.findByEmail('findme@example.com');

      expect(found).not.toBeNull();
      expect(found?.email).toBe('findme@example.com');
    });

    it('should return null when email not found', async () => {
      const found = await repository.findByEmail('nonexistent@example.com');

      expect(found).toBeNull();
    });

    it('should be case sensitive for email lookup', async () => {
      await repository.create(createUserData({ email: 'Test@Example.com' }));

      const found = await repository.findByEmail('test@example.com');

      expect(found).toBeNull();
    });
  });

  describe('updateLastLogin', () => {
    it('should set lastLoginAt timestamp', async () => {
      const created = await repository.create(createUserData());
      expect(created.lastLoginAt).toBeUndefined();

      await repository.updateLastLogin(created._id.toString());

      const updated = await repository.findById(created._id.toString());
      expect(updated?.lastLoginAt).toBeInstanceOf(Date);
    });

    it('should update lastLoginAt to current time', async () => {
      const created = await repository.create(createUserData());
      const beforeUpdate = new Date();

      await repository.updateLastLogin(created._id.toString());

      const updated = await repository.findById(created._id.toString());
      expect(updated?.lastLoginAt?.getTime()).toBeGreaterThanOrEqual(beforeUpdate.getTime());
    });
  });

  describe('update', () => {
    it('should update user name', async () => {
      const created = await repository.create(createUserData({ name: 'Old Name' }));

      await repository.update(created._id.toString(), { name: 'New Name' });

      const updated = await repository.findById(created._id.toString());
      expect(updated?.name).toBe('New Name');
    });

    it('should update user email', async () => {
      const created = await repository.create(createUserData());

      await repository.update(created._id.toString(), { email: 'newemail@example.com' });

      const updated = await repository.findById(created._id.toString());
      expect(updated?.email).toBe('newemail@example.com');
    });

    it('should update user preferences', async () => {
      const created = await repository.create(createUserData());
      const folderId = new ObjectId();

      await repository.update(created._id.toString(), {
        preferences: {
          defaultSummarizedFolder: folderId,
            theme: 'dark',
        },
      });

      const updated = await repository.findById(created._id.toString());
      expect(updated?.preferences.defaultSummarizedFolder?.toString()).toBe(folderId.toString());
      expect(updated?.preferences.theme).toBe('dark');
    });

    it('should update usage tracking', async () => {
      const created = await repository.create(createUserData());

      await repository.update(created._id.toString(), {
        usage: {
          videosThisMonth: 10,
          videosResetAt: new Date('2024-01-01'),
        },
      });

      const updated = await repository.findById(created._id.toString());
      expect(updated?.usage.videosThisMonth).toBe(10);
    });

    it('should update updatedAt timestamp', async () => {
      const created = await repository.create(createUserData());
      const originalUpdatedAt = created.updatedAt;

      // Wait a bit to ensure timestamp difference
      await new Promise(resolve => setTimeout(resolve, 10));
      await repository.update(created._id.toString(), { name: 'Updated' });

      const updated = await repository.findById(created._id.toString());
      expect(updated?.updatedAt.getTime()).toBeGreaterThan(originalUpdatedAt.getTime());
    });

    it('should update multiple fields at once', async () => {
      const created = await repository.create(createUserData());

      await repository.update(created._id.toString(), {
        name: 'New Name',
        email: 'new@example.com',
      });

      const updated = await repository.findById(created._id.toString());
      expect(updated?.name).toBe('New Name');
      expect(updated?.email).toBe('new@example.com');
    });

    it('should preserve other fields when updating', async () => {
      const created = await repository.create(createUserData({
        name: 'Original Name',
        email: 'original@example.com',
      }));

      await repository.update(created._id.toString(), { name: 'New Name' });

      const updated = await repository.findById(created._id.toString());
      expect(updated?.name).toBe('New Name');
      expect(updated?.email).toBe('original@example.com'); // Should remain unchanged
    });
  });

  describe('concurrent operations', () => {
    it('should handle multiple users with unique emails', async () => {
      const user1 = await repository.create(createUserData({ email: 'user1@example.com' }));
      const user2 = await repository.create(createUserData({ email: 'user2@example.com' }));

      expect(user1._id.toString()).not.toBe(user2._id.toString());
      expect(user1.email).not.toBe(user2.email);
    });
  });

  describe('soft-delete (GDPR)', () => {
    it('markSoftDeleted should set deletedAt and hardDeleteAt', async () => {
      const user = await repository.create(createUserData());
      const hardDelete = new Date('2026-06-20T00:00:00Z');

      const result = await repository.markSoftDeleted(user._id.toString(), hardDelete);

      expect(result).not.toBeNull();
      expect(result!.deletedAt).toBeInstanceOf(Date);
      expect(result!.hardDeleteAt).toEqual(hardDelete);
    });

    it('markSoftDeleted should honor an explicit deletedAt for clock injection in tests', async () => {
      const user = await repository.create(createUserData());
      const frozenNow = new Date('2026-05-20T12:00:00Z');
      const hardDelete = new Date('2026-06-19T12:00:00Z');

      const result = await repository.markSoftDeleted(
        user._id.toString(),
        hardDelete,
        frozenNow,
      );

      expect(result!.deletedAt).toEqual(frozenNow);
      expect(result!.updatedAt).toEqual(frozenNow);
    });

    it('markSoftDeleted should be a no-op when deletedAt is already set', async () => {
      const user = await repository.create(createUserData());
      await repository.markSoftDeleted(user._id.toString(), new Date('2026-06-01'));

      const result = await repository.markSoftDeleted(
        user._id.toString(),
        new Date('2026-07-01'),
      );

      // The conditional filter must not overwrite an existing soft-delete
      // timestamp — otherwise a second cancel→re-delete race would reset
      // the clock and extend the grace window.
      expect(result).toBeNull();
      const refreshed = await repository.findById(user._id.toString());
      expect(refreshed?.hardDeleteAt).toEqual(new Date('2026-06-01'));
    });

    it('clearSoftDelete should restore an account', async () => {
      const user = await repository.create(createUserData());
      await repository.markSoftDeleted(user._id.toString(), new Date('2026-06-01'));

      const restored = await repository.clearSoftDelete(user._id.toString());

      expect(restored).not.toBeNull();
      expect(restored!.deletedAt).toBeNull();
      expect(restored!.hardDeleteAt).toBeNull();
    });

    it('findExpiredSoftDeletes should return only past-due, non-legal-hold rows', async () => {
      const now = new Date('2026-05-20T12:00:00Z');
      // Three users: one expired, one not yet expired, one on legal hold.
      const expired = await repository.create(createUserData({ email: 'exp@x' }));
      const upcoming = await repository.create(createUserData({ email: 'up@x' }));
      const held = await repository.create(createUserData({ email: 'held@x' }));
      await repository.markSoftDeleted(expired._id.toString(), new Date('2026-05-19T00:00:00Z'));
      await repository.markSoftDeleted(upcoming._id.toString(), new Date('2026-06-19T00:00:00Z'));
      await repository.markSoftDeleted(held._id.toString(), new Date('2026-05-19T00:00:00Z'));
      await repository.update(held._id.toString(), { legalHold: true });

      const results = await repository.findExpiredSoftDeletes(now);

      const ids = results.map(r => r._id.toString());
      expect(ids).toContain(expired._id.toString());
      expect(ids).not.toContain(upcoming._id.toString());
      expect(ids).not.toContain(held._id.toString());
    });

    it('findExpiredSoftDeletes should project _id only — no passwordHash or other PII fields leaked', async () => {
      const now = new Date('2026-05-20T12:00:00Z');
      const target = await repository.create(createUserData({ email: 'lean@x' }));
      await repository.markSoftDeleted(target._id.toString(), new Date('2026-05-19T00:00:00Z'));

      const results = await repository.findExpiredSoftDeletes(now);

      expect(results).toHaveLength(1);
      const row = results[0] as Record<string, unknown>;
      expect(row._id).toBeDefined();
      // Sensitive fields must not be returned — protects against accidental
      // logging leaks downstream.
      expect(row.passwordHash).toBeUndefined();
      expect(row.preferences).toBeUndefined();
      expect(row.usage).toBeUndefined();
      expect(row.paddleCustomerId).toBeUndefined();
    });

    it('hardDelete should remove the user document', async () => {
      const user = await repository.create(createUserData());

      const removed = await repository.hardDelete(user._id.toString());

      expect(removed).toBe(true);
      expect(await repository.findById(user._id.toString())).toBeNull();
    });

    it('hardDelete should return false when user does not exist', async () => {
      const removed = await repository.hardDelete(new ObjectId().toString());
      expect(removed).toBe(false);
    });
  });

  describe('isEvalUser', () => {
    it('should return true when the user document has isEvalUser: true', async () => {
      const user = await repository.create(createUserData());
      await db.collection('users').updateOne({ _id: user._id }, { $set: { isEvalUser: true } });

      expect(await repository.isEvalUser(user._id.toString())).toBe(true);
    });

    it('should return false when the flag is absent', async () => {
      const user = await repository.create(createUserData());

      expect(await repository.isEvalUser(user._id.toString())).toBe(false);
    });

    it('should return false when the flag is a truthy non-boolean', async () => {
      const user = await repository.create(createUserData());
      await db.collection('users').updateOne({ _id: user._id }, { $set: { isEvalUser: 'true' } });

      expect(await repository.isEvalUser(user._id.toString())).toBe(false);
    });

    it('should return false when the user does not exist', async () => {
      expect(await repository.isEvalUser(new ObjectId().toString())).toBe(false);
    });

    it('should not set the flag when a user is created', async () => {
      const user = await repository.create(createUserData());

      expect(user.isEvalUser).toBeUndefined();
    });
  });
});
