import { describe, it, expect, beforeEach, afterEach, beforeAll, afterAll, vi } from 'vitest';
import { MongoMemoryServer } from 'mongodb-memory-server';
import { MongoClient, Db, ObjectId } from 'mongodb';
import type { FastifyBaseLogger } from 'fastify';

import { UserRepository, type UserDocument } from '../../repositories/user.repository.js';
import { UserDeletionRepository } from '../../repositories/user-deletion.repository.js';
import {
  UserDeletionService,
  hashEmail,
} from '../user-deletion.service.js';
import { config } from '../../config.js';
import {
  AccountAlreadyDeletedError,
  DemoRestrictedError,
  UserNotFoundError,
} from '../../utils/errors.js';

const silentLogger = {
  info: vi.fn(),
  warn: vi.fn(),
  error: vi.fn(),
  debug: vi.fn(),
  trace: vi.fn(),
  fatal: vi.fn(),
  child: vi.fn(() => silentLogger),
  level: 'silent',
  silent: vi.fn(),
} as unknown as FastifyBaseLogger;

describe('UserDeletionService (integration with in-memory MongoDB)', () => {
  let mongod: MongoMemoryServer;
  let client: MongoClient;
  let db: Db;
  let userRepo: UserRepository;
  let deletionRepo: UserDeletionRepository;
  let service: UserDeletionService;
  let frozenNow: Date;

  // Insert a fully-formed user doc by hand so we can control _id and avoid
  // the AuthService bcrypt path.
  async function insertUser(overrides: Partial<UserDocument> = {}): Promise<UserDocument> {
    const now = new Date('2026-05-20T10:00:00Z');
    const _id = overrides._id ?? new ObjectId();
    const doc: UserDocument = {
      _id,
      email: overrides.email ?? `u${_id.toString()}@example.com`,
      passwordHash: '$2b$10$fake',
      name: overrides.name ?? 'Test User',
      preferences: { defaultSummarizedFolder: null, theme: 'system' },
      usage: { videosThisMonth: 0, videosResetAt: now },
      createdAt: now,
      updatedAt: now,
      ...overrides,
    };
    await db.collection<UserDocument>('users').insertOne(doc);
    return doc;
  }

  beforeAll(async () => {
    mongod = await MongoMemoryServer.create();
    client = new MongoClient(mongod.getUri());
    await client.connect();
    db = client.db('gdpr-test');
  });

  afterAll(async () => {
    await client.close();
    await mongod.stop();
  });

  beforeEach(async () => {
    const collections = await db.listCollections().toArray();
    for (const c of collections) await db.collection(c.name).deleteMany({});

    userRepo = new UserRepository(db);
    deletionRepo = new UserDeletionRepository(db);
    frozenNow = new Date('2026-05-20T12:00:00Z');
    service = new UserDeletionService(db, userRepo, deletionRepo, silentLogger, () => frozenNow);
  });

  // ─── Phase 2: soft delete ───────────────────────────────────────────────

  describe('requestDeletion', () => {
    it('should set deletedAt and schedule hardDeleteAt 30 days out by default', async () => {
      const user = await insertUser();

      const result = await service.requestDeletion(user._id.toString());

      const expectedHardDelete = new Date(frozenNow.getTime() + 30 * 24 * 60 * 60 * 1000);
      expect(result.scheduledHardDeleteAt).toEqual(expectedHardDelete);
      expect(result.graceDays).toBe(30);

      const fresh = await userRepo.findById(user._id.toString());
      expect(fresh?.deletedAt).toEqual(frozenNow);
      expect(fresh?.hardDeleteAt).toEqual(expectedHardDelete);
    });

    it('should honor a custom graceDays value', async () => {
      const user = await insertUser();

      const result = await service.requestDeletion(user._id.toString(), { graceDays: 7 });

      expect(result.graceDays).toBe(7);
      const expected = new Date(frozenNow.getTime() + 7 * 24 * 60 * 60 * 1000);
      expect(result.scheduledHardDeleteAt).toEqual(expected);
    });

    it('should throw UserNotFoundError for missing user', async () => {
      await expect(
        service.requestDeletion(new ObjectId().toString()),
      ).rejects.toBeInstanceOf(UserNotFoundError);
    });

    it('should throw AccountAlreadyDeletedError on second call', async () => {
      const user = await insertUser();
      await service.requestDeletion(user._id.toString());

      await expect(
        service.requestDeletion(user._id.toString()),
      ).rejects.toBeInstanceOf(AccountAlreadyDeletedError);
    });
  });

  describe('requestDeletion for the demo account', () => {
    const DEMO_EMAIL = 'demo@example.com';
    const originalEmail = config.DEMO_USER_EMAIL;

    beforeEach(() => {
      config.DEMO_USER_EMAIL = DEMO_EMAIL;
    });

    afterEach(() => {
      config.DEMO_USER_EMAIL = originalEmail;
    });

    it('should throw DemoRestrictedError when the demo account requests deletion', async () => {
      const user = await insertUser({ email: DEMO_EMAIL });

      await expect(
        service.requestDeletion(user._id.toString()),
      ).rejects.toBeInstanceOf(DemoRestrictedError);
    });

    it('should leave the demo account active when its deletion is refused', async () => {
      const user = await insertUser({ email: DEMO_EMAIL });

      await service.requestDeletion(user._id.toString()).catch(() => undefined);

      const fresh = await userRepo.findById(user._id.toString());
      expect(fresh?.deletedAt ?? null).toBeNull();
    });

    it('should still delete other accounts when demo mode is on', async () => {
      const user = await insertUser({ email: 'someone@example.com' });

      await service.requestDeletion(user._id.toString());

      const fresh = await userRepo.findById(user._id.toString());
      expect(fresh?.deletedAt).toEqual(frozenNow);
    });

    it('should delete an account with the same email when demo mode is off', async () => {
      config.DEMO_USER_EMAIL = '';
      const user = await insertUser({ email: DEMO_EMAIL });

      await service.requestDeletion(user._id.toString());

      const fresh = await userRepo.findById(user._id.toString());
      expect(fresh?.deletedAt).toEqual(frozenNow);
    });
  });

  describe('cancelDeletion', () => {
    it('should clear deletedAt and hardDeleteAt within the grace window', async () => {
      const user = await insertUser();
      await service.requestDeletion(user._id.toString());

      const restored = await service.cancelDeletion(user._id.toString());

      expect(restored.deletedAt).toBeNull();
      expect(restored.hardDeleteAt).toBeNull();
    });

    it('should be a no-op for an already-active user', async () => {
      const user = await insertUser();

      const restored = await service.cancelDeletion(user._id.toString());

      expect(restored._id.toString()).toBe(user._id.toString());
    });

    it('should throw UserNotFoundError for missing user', async () => {
      await expect(
        service.cancelDeletion(new ObjectId().toString()),
      ).rejects.toBeInstanceOf(UserNotFoundError);
    });
  });

  // ─── Phase 3: hard delete saga ──────────────────────────────────────────

  describe('executeHardDelete', () => {
    async function seedUserData(userOid: ObjectId): Promise<void> {
      // userVideos
      await db.collection('userVideos').insertMany([
        { userId: userOid, videoSummaryId: new ObjectId(), youtubeId: 'a' },
        { userId: userOid, videoSummaryId: new ObjectId(), youtubeId: 'b' },
        { userId: userOid, videoSummaryId: new ObjectId(), youtubeId: 'c' },
      ]);
      // folders
      await db.collection('folders').insertMany([
        { userId: userOid, name: 'Work', path: '/Work' },
        { userId: userOid, name: 'Personal', path: '/Personal' },
      ]);
      // userCosts
      await db.collection('userCosts').insertOne({
        userId: userOid,
        date: '2026-05-20',
        rawUsd: 1.5,
      });
      // userCostAdjustments
      await db.collection('userCostAdjustments').insertOne({
        userId: userOid,
        amount: -0.5,
      });
      // idempotencyKeys
      await db.collection('idempotencyKeys').insertOne({
        userId: userOid,
        hash: 'h1',
        status: 'completed',
      });
      // agentNotes (assistant collection — userId stored as string per
      // services/assistant/src/repositories/notes_repository.py:55)
      await db.collection('agentNotes').insertMany([
        { _id: 'n1', userId: userOid.toString(), videoId: 'a', text: 'note1' },
        { _id: 'n2', userId: userOid.toString(), videoId: 'b', text: 'note2' },
      ] as unknown as Document[]);
    }

    it('should delete every user-scoped row and write an audit', async () => {
      const user = await insertUser({ email: 'doomed@example.com' });
      await seedUserData(user._id);
      // Other users' data — must not be touched
      const other = await insertUser({ email: 'other@example.com' });
      await db.collection('userVideos').insertOne({
        userId: other._id,
        videoSummaryId: new ObjectId(),
        youtubeId: 'z',
      });

      const audit = await service.executeHardDelete(user._id.toString(), {
        initiatedBy: 'admin',
        adminId: 'admin-007',
        reason: 'compliance request',
      });

      expect(audit.originalUserId).toBe(user._id.toString());
      expect(audit.emailHash).toBe(hashEmail('doomed@example.com'));
      expect(audit.initiatedBy).toBe('admin');
      expect(audit.adminId).toBe('admin-007');
      expect(audit.reason).toBe('compliance request');
      expect(audit.counts).toMatchObject({
        userVideos: 3,
        folders: 2,
        userCosts: 1,
        userCostAdjustments: 1,
        idempotencyKeys: 1,
        assistantNotes: 2,
        qdrantPoints: 0,
        s3Objects: 0,
      });
      expect(audit.warnings ?? []).toEqual([]);

      // Verify the doomed user's data is gone…
      expect(await userRepo.findById(user._id.toString())).toBeNull();
      expect(await db.collection('userVideos').countDocuments({ userId: user._id })).toBe(0);
      expect(await db.collection('folders').countDocuments({ userId: user._id })).toBe(0);
      expect(await db.collection('userCosts').countDocuments({ userId: user._id })).toBe(0);
      expect(await db.collection('agentNotes').countDocuments({ userId: user._id.toString() })).toBe(0);

      // …but the other user's row is untouched.
      expect(await db.collection('userVideos').countDocuments({ userId: other._id })).toBe(1);
      expect(await userRepo.findById(other._id.toString())).not.toBeNull();
    });

    it('should hash the email deterministically (case-insensitive, trimmed)', async () => {
      const user = await insertUser({ email: '  Mixed.Case@Example.COM  ' });

      const audit = await service.executeHardDelete(user._id.toString(), {
        initiatedBy: 'self',
      });

      expect(audit.emailHash).toBe(hashEmail('mixed.case@example.com'));
      // Hash never contains the plaintext.
      expect(audit.emailHash).not.toContain('Mixed');
      expect(audit.emailHash).not.toContain('@');
    });

    it('should be idempotent on a re-run (delete-many filters match nothing)', async () => {
      const user = await insertUser();
      await db.collection('userVideos').insertOne({
        userId: user._id,
        videoSummaryId: new ObjectId(),
      });

      await service.executeHardDelete(user._id.toString(), { initiatedBy: 'self' });
      // Re-running on the already-deleted user must throw UserNotFoundError
      // (the lookup at the top fails before any destructive operation runs).
      await expect(
        service.executeHardDelete(user._id.toString(), { initiatedBy: 'self' }),
      ).rejects.toBeInstanceOf(UserNotFoundError);
    });

    // Contract: any non-empty `warnings[]` on the audit row means the cascade
    // had a partial failure — the terminal log must hit `error` so on-call
    // alerting catches it instead of being buried in info noise.
    it('should escalate the terminal log to error level when warnings are non-empty', async () => {
      const errorLogger = vi.fn();
      const infoLogger = vi.fn();
      const observingLogger = {
        ...silentLogger,
        info: infoLogger,
        warn: vi.fn(),
        error: errorLogger,
      } as unknown as typeof silentLogger;

      const observingService = new UserDeletionService(
        db,
        userRepo,
        deletionRepo,
        observingLogger,
        () => frozenNow,
      );

      const user = await insertUser();
      vi.spyOn(
        observingService as unknown as { deleteAssistantNotes: (id: string) => Promise<number> },
        'deleteAssistantNotes',
      ).mockRejectedValueOnce(new Error('downstream-blip'));

      await observingService.executeHardDelete(user._id.toString(), { initiatedBy: 'self' });

      // The "completed with warnings" log must land at ERROR so paging/alerting
      // surfaces partial-failure cascades — info would hide them in the noise.
      const errorMessages = errorLogger.mock.calls.map(c => c[c.length - 1]);
      expect(errorMessages).toContain('user_hard_delete_completed_with_warnings');
      // And the clean-completion message must NOT fire when warnings are present.
      const infoMessages = infoLogger.mock.calls.map(c => c[c.length - 1]);
      expect(infoMessages).not.toContain('user_hard_delete_completed');
    });

    it('should keep terminal log at info level when the cascade is clean', async () => {
      const errorLogger = vi.fn();
      const infoLogger = vi.fn();
      const observingLogger = {
        ...silentLogger,
        info: infoLogger,
        warn: vi.fn(),
        error: errorLogger,
      } as unknown as typeof silentLogger;
      const observingService = new UserDeletionService(
        db,
        userRepo,
        deletionRepo,
        observingLogger,
        () => frozenNow,
      );

      const user = await insertUser();
      await observingService.executeHardDelete(user._id.toString(), { initiatedBy: 'self' });

      const infoMessages = infoLogger.mock.calls.map(c => c[c.length - 1]);
      expect(infoMessages).toContain('user_hard_delete_completed');
      const errorMessages = errorLogger.mock.calls.map(c => c[c.length - 1]);
      expect(errorMessages).not.toContain('user_hard_delete_completed_with_warnings');
    });

    it('should record warnings without aborting when one step throws', async () => {
      const user = await insertUser();
      await db.collection('userVideos').insertOne({
        userId: user._id,
        videoSummaryId: new ObjectId(),
      });

      // Stub the assistant-notes step directly. Spying on the collection
      // wrapper does not work because `db.collection(name)` returns a new
      // wrapper per call in the MongoDB driver — the service code and the
      // test see different objects.
      const spy = vi
        .spyOn(service as unknown as { deleteAssistantNotes: (id: string) => Promise<number> }, 'deleteAssistantNotes')
        .mockRejectedValueOnce(new Error('simulated assistant outage'));

      const audit = await service.executeHardDelete(user._id.toString(), {
        initiatedBy: 'self',
      });

      expect(spy).toHaveBeenCalled();
      expect(audit.warnings?.some(w => w.includes('simulated assistant outage'))).toBe(true);
      // Subsequent steps still ran — the user doc and userVideos rows were
      // deleted even though the notes step threw.
      expect(await userRepo.findById(user._id.toString())).toBeNull();
      expect(await db.collection('userVideos').countDocuments({ userId: user._id })).toBe(0);
    });
  });

  // ─── Phase 4: scheduler ─────────────────────────────────────────────────

  describe('runScheduledDeletions', () => {
    it('should run the cascade for every user whose grace window has elapsed', async () => {
      const expired1 = await insertUser({
        email: 'a@x',
        deletedAt: new Date('2026-04-01'),
        hardDeleteAt: new Date('2026-05-01'),
      });
      const expired2 = await insertUser({
        email: 'b@x',
        deletedAt: new Date('2026-04-15'),
        hardDeleteAt: new Date('2026-05-15'),
      });
      // Not yet eligible — grace window still open.
      const stillPending = await insertUser({
        email: 'c@x',
        deletedAt: new Date('2026-05-19'),
        hardDeleteAt: new Date('2026-06-19'),
      });
      // Active user — should be ignored.
      const active = await insertUser({ email: 'd@x' });

      const result = await service.runScheduledDeletions(frozenNow);

      expect(result.processed).toBe(2);
      expect(result.succeeded).toBe(2);
      expect(result.failed).toBe(0);

      expect(await userRepo.findById(expired1._id.toString())).toBeNull();
      expect(await userRepo.findById(expired2._id.toString())).toBeNull();
      expect(await userRepo.findById(stillPending._id.toString())).not.toBeNull();
      expect(await userRepo.findById(active._id.toString())).not.toBeNull();
    });

    it('should skip users with legalHold=true', async () => {
      const held = await insertUser({
        email: 'held@x',
        deletedAt: new Date('2026-04-01'),
        hardDeleteAt: new Date('2026-05-01'),
        legalHold: true,
      });

      const result = await service.runScheduledDeletions(frozenNow);

      expect(result.processed).toBe(0);
      expect(await userRepo.findById(held._id.toString())).not.toBeNull();
    });

    it('should report failures without aborting the batch', async () => {
      const user1 = await insertUser({
        email: 'f1@x',
        deletedAt: new Date('2026-04-01'),
        hardDeleteAt: new Date('2026-05-01'),
      });
      const user2 = await insertUser({
        email: 'f2@x',
        deletedAt: new Date('2026-04-01'),
        hardDeleteAt: new Date('2026-05-01'),
      });

      // Make the audit insert blow up just for user1 to simulate a downstream
      // failure mid-batch.
      const originalInsert = deletionRepo.insert.bind(deletionRepo);
      let calls = 0;
      vi.spyOn(deletionRepo, 'insert').mockImplementation(async (data) => {
        calls++;
        if (calls === 1) throw new Error('audit insert failed for first user');
        return originalInsert(data);
      });

      const result = await service.runScheduledDeletions(frozenNow);

      expect(result.processed).toBe(2);
      expect(result.succeeded).toBe(1);
      expect(result.failed).toBe(1);

      // The second user should still be fully deleted.
      // Find which one succeeded by checking which user doc remains. The
      // saga deletes the user doc BEFORE the audit insert, so both user
      // docs are gone — but only one audit row exists.
      const audits = await db.collection('userDeletions').find({}).toArray();
      expect(audits).toHaveLength(1);
      const succeededId = audits[0].originalUserId;
      expect([user1._id.toString(), user2._id.toString()]).toContain(succeededId);
    });
  });

  // ─── hashEmail helper ───────────────────────────────────────────────────

  describe('hashEmail', () => {
    it('should produce 64-char hex SHA-256', () => {
      expect(hashEmail('a@b.com')).toMatch(/^[0-9a-f]{64}$/);
    });

    it('should be case-insensitive and trim whitespace', () => {
      expect(hashEmail('A@B.com')).toBe(hashEmail('a@b.com'));
      expect(hashEmail('  a@b.com  ')).toBe(hashEmail('a@b.com'));
    });

    it('should be deterministic and unique per email', () => {
      expect(hashEmail('a@b.com')).toBe(hashEmail('a@b.com'));
      expect(hashEmail('a@b.com')).not.toBe(hashEmail('a@c.com'));
    });
  });
});
