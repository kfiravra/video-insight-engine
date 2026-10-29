import { describe, it, expect, beforeAll, afterAll, beforeEach, afterEach, vi } from 'vitest';
import { MongoMemoryServer } from 'mongodb-memory-server';
import { MongoClient, Db, ObjectId } from 'mongodb';
import type { FastifyBaseLogger } from 'fastify';

import { AdminAuditRepository } from '../../repositories/admin-audit.repository.js';
import { AuditService, type RecordAuditInput } from '../audit.service.js';
import { ValidationError } from '../../utils/errors.js';

function createLogger() {
  return {
    info: vi.fn(),
    warn: vi.fn(),
    error: vi.fn(),
    debug: vi.fn(),
    trace: vi.fn(),
    fatal: vi.fn(),
  };
}

describe('AuditService (integration with in-memory MongoDB)', () => {
  let mongod: MongoMemoryServer;
  let client: MongoClient;
  let db: Db;
  let repository: AdminAuditRepository;
  let logger: ReturnType<typeof createLogger>;
  let now: Date;
  let service: AuditService;

  const baseInput: RecordAuditInput = {
    actorId: 'op-1',
    action: 'videos.purge',
    targetType: 'video',
    targetId: 'dQw4w9WgXcQ',
  };

  beforeAll(async () => {
    mongod = await MongoMemoryServer.create();
    client = new MongoClient(mongod.getUri());
    await client.connect();
    db = client.db('audit-test');
  });

  afterAll(async () => {
    await client.close();
    await mongod.stop();
  });

  beforeEach(async () => {
    await db.collection('adminAudit').deleteMany({});
    repository = new AdminAuditRepository(db);
    logger = createLogger();
    now = new Date('2026-10-28T10:00:00Z');
    service = new AuditService(repository, logger as unknown as FastifyBaseLogger, () => now);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe('record', () => {
    it('should store one row with the actor, action, target and clock time', async () => {
      const id = await service.record({ ...baseInput, requestId: 'req-12345678', ip: '10.0.0.1', reason: 'duplicate upload' });

      const row = await db.collection('adminAudit').findOne({ _id: new ObjectId(id) });
      expect(row).toMatchObject({
        actorId: 'op-1',
        action: 'videos.purge',
        targetType: 'video',
        targetId: 'dQw4w9WgXcQ',
        requestId: 'req-12345678',
        ip: '10.0.0.1',
        reason: 'duplicate upload',
        createdAt: now,
      });
    });

    it('should leave out fields the caller did not supply instead of storing null', async () => {
      const id = await service.record(baseInput);

      const row = await db.collection('adminAudit').findOne({ _id: new ObjectId(id) });
      expect(Object.keys(row ?? {}).sort()).toEqual(['_id', 'action', 'actorId', 'createdAt', 'targetId', 'targetType']);
    });

    it('should use a pre-generated audit id so a ledger row can reference it', async () => {
      const auditId = new ObjectId().toString();

      const returned = await service.record({ ...baseInput, auditId });

      expect(returned).toBe(auditId);
    });

    it('should write nothing and return the id when the audit id already has a row (a retry)', async () => {
      const auditId = new ObjectId().toString();
      await service.record({ ...baseInput, auditId });

      const again = await service.record({ ...baseInput, auditId });

      expect(again).toBe(auditId);
      expect(await db.collection('adminAudit').countDocuments()).toBe(1);
    });

    it('should return the lowercase id for an uppercase audit id that already has a row', async () => {
      const auditId = new ObjectId().toString();
      await service.record({ ...baseInput, auditId });

      expect(await service.record({ ...baseInput, auditId: auditId.toUpperCase() })).toBe(auditId);
    });

    it('should still raise an insert error other than a duplicate id', async () => {
      vi.spyOn(repository, 'insert').mockRejectedValue(Object.assign(new Error('boom'), { code: 91 }));

      await expect(service.record({ ...baseInput, auditId: new ObjectId().toString() })).rejects.toThrow('boom');
    });

    it('should reject an audit id that is not an ObjectId', async () => {
      await expect(service.record({ ...baseInput, auditId: 'not-hex' })).rejects.toBeInstanceOf(ValidationError);
    });

    it('should store the request hash a caller supplies, to tell a retry from a reused key', async () => {
      const requestHash = 'f'.repeat(64);

      const id = await service.record({ ...baseInput, requestHash });

      const row = await db.collection('adminAudit').findOne({ _id: new ObjectId(id) });
      expect(row?.requestHash).toBe(requestHash);
    });

    it('should store before and after as plain JSON, ObjectIds and Dates as strings', async () => {
      const userId = new ObjectId();
      const id = await service.record({
        ...baseInput,
        before: { owner: userId, at: new Date('2026-01-01T00:00:00Z') },
        after: { roles: ['admin'] },
      });

      const row = await db.collection('adminAudit').findOne({ _id: new ObjectId(id) });
      expect([row?.before, row?.after]).toEqual([
        { owner: userId.toString(), at: '2026-01-01T00:00:00.000Z' },
        { roles: ['admin'] },
      ]);
    });

    it('should store the actor email in the row but never log it', async () => {
      const id = await service.record({ ...baseInput, actorEmail: 'kfir@example.com' });

      const row = await db.collection('adminAudit').findOne({ _id: new ObjectId(id) });
      expect(row?.actorEmail).toBe('kfir@example.com');
      expect(JSON.stringify(logger.info.mock.calls)).not.toContain('kfir@example.com');
    });

    it('should reject when the insert fails', async () => {
      vi.spyOn(repository, 'insert').mockRejectedValue(new Error('mongo down'));

      await expect(service.record(baseInput)).rejects.toThrow('mongo down');
    });
  });

  describe('recordBestEffort', () => {
    it('should return the audit id when the insert succeeds', async () => {
      const id = await service.recordBestEffort(baseInput);

      expect(id).toMatch(/^[a-f0-9]{24}$/);
    });

    it('should return null and log at error level when the insert fails', async () => {
      vi.spyOn(repository, 'insert').mockRejectedValue(new Error('mongo down'));

      const id = await service.recordBestEffort({ ...baseInput, auditId: new ObjectId().toString() });

      expect(id).toBeNull();
      expect(logger.error).toHaveBeenCalledWith(
        expect.objectContaining({ action: 'videos.purge', targetId: 'dQw4w9WgXcQ', auditId: expect.any(String) }),
        'admin_audit_write_failed',
      );
    });
  });

  describe('findRequestHash', () => {
    it('should return the hash stored with the row for an audit id', async () => {
      const auditId = new ObjectId().toString();
      await service.record({ ...baseInput, auditId, requestHash: 'a'.repeat(64) });

      expect(await service.findRequestHash(auditId)).toBe('a'.repeat(64));
    });

    it('should return null for a row recorded without a hash', async () => {
      const auditId = new ObjectId().toString();
      await service.record({ ...baseInput, auditId });

      expect(await service.findRequestHash(auditId)).toBeNull();
    });

    it('should return null when no row has the audit id', async () => {
      expect(await service.findRequestHash(new ObjectId().toString())).toBeNull();
    });

    it('should find the row by an uppercase spelling of its id', async () => {
      const auditId = new ObjectId().toString();
      await service.record({ ...baseInput, auditId, requestHash: 'b'.repeat(64) });

      expect(await service.findRequestHash(auditId.toUpperCase())).toBe('b'.repeat(64));
    });
  });

  describe('list', () => {
    async function seed(targetId: string, actorId: string, offsetMinutes: number): Promise<void> {
      now = new Date(Date.UTC(2026, 9, 28, 10, offsetMinutes));
      await service.record({ ...baseInput, targetId, actorId });
    }

    it('should return rows newest first as views with id and ISO createdAt', async () => {
      await seed('a', 'op-1', 0);
      await seed('b', 'op-1', 5);

      const rows = await service.list();

      expect(rows.map((row) => row.targetId)).toEqual(['b', 'a']);
      expect(rows[0]).toMatchObject({ id: expect.stringMatching(/^[a-f0-9]{24}$/), createdAt: '2026-10-28T10:05:00.000Z' });
      expect(rows[0]).not.toHaveProperty('_id');
    });

    it('should filter by target', async () => {
      await seed('a', 'op-1', 0);
      await seed('b', 'op-2', 1);

      const rows = await service.list({ targetId: 'a' });

      expect(rows.map((row) => row.targetId)).toEqual(['a']);
    });

    it('should filter by actor', async () => {
      await seed('a', 'op-1', 0);
      await seed('b', 'op-2', 1);

      const rows = await service.list({ actorId: 'op-2' });

      expect(rows.map((row) => row.actorId)).toEqual(['op-2']);
    });

    it('should page with the before cursor', async () => {
      await seed('a', 'op-1', 0);
      await seed('b', 'op-1', 5);
      await seed('c', 'op-1', 10);

      const rows = await service.list({ before: new Date('2026-10-28T10:10:00Z') });

      expect(rows.map((row) => row.targetId)).toEqual(['b', 'a']);
    });

    it('should not expose the request hash on a listed view', async () => {
      await service.record({ ...baseInput, requestHash: 'c'.repeat(64) });

      const [row] = await service.list();

      expect(row).not.toHaveProperty('requestHash');
    });

    it('should page through rows that share a millisecond without skipping or repeating any', async () => {
      now = new Date('2026-10-28T10:00:00Z');
      for (const targetId of ['a', 'b', 'c', 'd', 'e']) await service.record({ ...baseInput, targetId });

      const seen: string[] = [];
      let before: Date | { createdAt: Date; id: string } | undefined;
      for (let page = 0; page < 5; page++) {
        const rows = await service.list({ limit: 2, before });
        if (rows.length === 0) break;
        seen.push(...rows.map((row) => row.targetId));
        const last = rows[rows.length - 1];
        before = { createdAt: new Date(last.createdAt), id: last.id };
      }

      expect([...seen].sort()).toEqual(['a', 'b', 'c', 'd', 'e']);
    });

    it('should honor the limit and cap it at 200', async () => {
      await seed('a', 'op-1', 0);
      await seed('b', 'op-1', 1);
      const findSpy = vi.spyOn(repository, 'find');

      const limited = await service.list({ limit: 1 });
      await service.list({ limit: 5000 });

      expect(limited).toHaveLength(1);
      expect(findSpy).toHaveBeenLastCalledWith(expect.objectContaining({ limit: 200 }));
    });

    it('should fall back to the default limit for a non-numeric one', async () => {
      const findSpy = vi.spyOn(repository, 'find');

      await service.list({ limit: Number.NaN });

      expect(findSpy).toHaveBeenCalledWith(expect.objectContaining({ limit: 50 }));
    });
  });
});
