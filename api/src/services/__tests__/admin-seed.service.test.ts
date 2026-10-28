import { describe, it, expect, beforeAll, afterAll, beforeEach, afterEach, vi } from 'vitest';
import { MongoMemoryServer } from 'mongodb-memory-server';
import { MongoClient, Db, ObjectId } from 'mongodb';
import type { FastifyBaseLogger } from 'fastify';

import { UserRepository } from '../../repositories/user.repository.js';
import { AdminAuditRepository } from '../../repositories/admin-audit.repository.js';
import { AuditService } from '../audit.service.js';
import { AdminSeedService, parseAdminEmails } from '../admin-seed.service.js';

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

describe('parseAdminEmails', () => {
  it('should trim, lowercase, split on commas and drop blanks', () => {
    expect(parseAdminEmails(' A@x.com, ,b@X.com ')).toEqual(['a@x.com', 'b@x.com']);
  });

  it('should drop duplicates that differ only by case', () => {
    expect(parseAdminEmails('a@x.com,A@X.COM')).toEqual(['a@x.com']);
  });

  it('should accept semicolons and whitespace as separators', () => {
    expect(parseAdminEmails('a@x.com; b@x.com c@x.com')).toEqual(['a@x.com', 'b@x.com', 'c@x.com']);
  });

  it('should return nothing for an empty value', () => {
    expect(parseAdminEmails('')).toEqual([]);
  });
});

describe('AdminSeedService (integration with in-memory MongoDB)', () => {
  let mongod: MongoMemoryServer;
  let client: MongoClient;
  let db: Db;
  let userRepository: UserRepository;
  let logger: ReturnType<typeof createLogger>;

  function createService(adminEmails: string, demoEmail = ''): AdminSeedService {
    const audit = new AuditService(new AdminAuditRepository(db), logger as unknown as FastifyBaseLogger);
    return new AdminSeedService(adminEmails, demoEmail, userRepository, audit, logger as unknown as FastifyBaseLogger);
  }

  async function insertUser(email: string, fields: Record<string, unknown> = {}): Promise<string> {
    const user = await userRepository.create({ email, passwordHash: '$2b$10$fake', name: 'Test' });
    if (Object.keys(fields).length > 0) {
      await db.collection('users').updateOne({ _id: user._id }, { $set: fields });
    }
    return user._id.toString();
  }

  async function rawUser(id: string) {
    return db.collection('users').findOne({ _id: new ObjectId(id) });
  }

  async function auditRows() {
    return db.collection('adminAudit').find({}).sort({ _id: 1 }).toArray();
  }

  beforeAll(async () => {
    mongod = await MongoMemoryServer.create();
    client = new MongoClient(mongod.getUri());
    await client.connect();
    db = client.db('admin-seed-test');
  });

  afterAll(async () => {
    await client.close();
    await mongod.stop();
  });

  beforeEach(async () => {
    for (const collection of await db.collections()) await collection.deleteMany({});
    userRepository = new UserRepository(db);
    logger = createLogger();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe('promotion', () => {
    it('should promote a listed existing user to an env admin', async () => {
      const id = await insertUser('kfir@example.com');

      const result = await createService('kfir@example.com').run();

      const user = await rawUser(id);
      expect([result.status, result.promoted, user?.roles, user?.roleSource]).toEqual(['reconciled', 1, ['admin'], 'env']);
    });

    it('should write one audit row for the promotion, actor system:boot', async () => {
      const id = await insertUser('kfir@example.com');

      await createService('kfir@example.com').run();

      const rows = await auditRows();
      expect(rows).toHaveLength(1);
      expect(rows[0]).toMatchObject({
        actorId: 'system:boot',
        action: 'roles.seed.promote',
        targetType: 'role',
        targetId: id,
        before: { roles: [], roleSource: null },
        after: { roles: ['admin'], roleSource: 'env' },
      });
    });

    it('should match the listed email ignoring case', async () => {
      const id = await insertUser('Kfir@Example.com');

      await createService('  KFIR@example.COM ').run();

      expect((await rawUser(id))?.roles).toEqual(['admin']);
    });

    it('should not create a user for an email with no account', async () => {
      await insertUser('kfir@example.com');

      await createService('kfir@example.com, ghost@example.com').run();

      expect(await db.collection('users').countDocuments()).toBe(1);
    });

    it('should log an unknown email masked, never in full', async () => {
      await insertUser('kfir@example.com');

      await createService('kfir@example.com, ghost@example.com').run();

      const logged = JSON.stringify(logger.warn.mock.calls);
      expect(logged).toContain('admin_seed_unknown_email');
      expect(logged).not.toContain('ghost@example.com');
    });

    it('should not promote a soft-deleted account', async () => {
      const active = await insertUser('kfir@example.com');
      const deleted = await insertUser('gone@example.com', { deletedAt: new Date() });

      await createService('kfir@example.com, gone@example.com').run();

      expect((await rawUser(deleted))?.roles).toBeUndefined();
      expect((await rawUser(active))?.roles).toEqual(['admin']);
    });

    it('should never promote the shared demo account', async () => {
      await insertUser('kfir@example.com');
      const demo = await insertUser('demo@example.com');

      await createService('kfir@example.com, demo@example.com', 'demo@example.com').run();

      expect((await rawUser(demo))?.roles).toBeUndefined();
    });

    it('should leave an existing granted admin granted when the email is also listed', async () => {
      const id = await insertUser('kfir@example.com', { roles: ['admin'], roleSource: 'granted' });

      await createService('kfir@example.com').run();

      expect((await rawUser(id))?.roleSource).toBe('granted');
    });
  });

  describe('demotion', () => {
    it('should demote an env admin whose email left the list, clearing roles and source', async () => {
      const staying = await insertUser('kfir@example.com');
      const leaving = await insertUser('old@example.com', { roles: ['admin'], roleSource: 'env' });

      const result = await createService('kfir@example.com').run();

      const user = await rawUser(leaving);
      expect([result.demoted, user?.roles, user?.roleSource]).toEqual([1, [], undefined]);
      expect((await rawUser(staying))?.roles).toEqual(['admin']);
    });

    it('should write a demote audit row naming system:boot', async () => {
      await insertUser('kfir@example.com');
      const leaving = await insertUser('old@example.com', { roles: ['admin'], roleSource: 'env' });

      await createService('kfir@example.com').run();

      const demote = (await auditRows()).find((row) => row.action === 'roles.seed.demote');
      expect(demote).toMatchObject({
        actorId: 'system:boot',
        targetId: leaving,
        before: { roles: ['admin'], roleSource: 'env' },
        after: { roles: [], roleSource: null },
      });
    });

    it('should never touch a granted admin that is not listed', async () => {
      await insertUser('kfir@example.com');
      const granted = await insertUser('helper@example.com', { roles: ['admin'], roleSource: 'granted' });

      await createService('kfir@example.com').run();

      expect((await rawUser(granted))?.roles).toEqual(['admin']);
    });

    it('should never touch a legacy role:admin user without a source', async () => {
      await insertUser('kfir@example.com');
      const legacy = await insertUser('legacy@example.com', { role: 'admin' });

      await createService('kfir@example.com').run();

      expect((await rawUser(legacy))?.role).toBe('admin');
    });

    it('should keep every admin when the demotion and a failed promotion would leave none', async () => {
      const envAdmin = await insertUser('old@example.com', { roles: ['admin'], roleSource: 'env' });
      await insertUser('new@example.com');
      vi.spyOn(userRepository, 'setRoles').mockRejectedValueOnce(new Error('write failed'));

      await createService('new@example.com').run();

      expect((await rawUser(envAdmin))?.roles).toEqual(['admin']);
    });
  });

  describe('zero-admin refusal', () => {
    it('should change nothing when the list names only unknown emails and an env admin exists', async () => {
      const envAdmin = await insertUser('kfir@example.com', { roles: ['admin'], roleSource: 'env' });

      const result = await createService('typo@example.com').run();

      expect([result.status, (await rawUser(envAdmin))?.roles]).toEqual(['refused_zero_admins', ['admin']]);
    });

    it('should log the refusal at error level and write no audit row', async () => {
      await insertUser('kfir@example.com', { roles: ['admin'], roleSource: 'env' });

      await createService('typo@example.com').run();

      expect(logger.error).toHaveBeenCalledWith(expect.anything(), 'admin_seed_refused_zero_admins');
      expect(await auditRows()).toEqual([]);
    });

    it('should not count a soft-deleted admin as a remaining admin', async () => {
      const envAdmin = await insertUser('kfir@example.com', { roles: ['admin'], roleSource: 'env' });
      await insertUser('gone@example.com', { roles: ['admin'], roleSource: 'granted', deletedAt: new Date() });

      const result = await createService('typo@example.com').run();

      expect([result.status, (await rawUser(envAdmin))?.roles]).toEqual(['refused_zero_admins', ['admin']]);
    });
  });

  describe('empty list', () => {
    it('should skip everything and warn when ADMIN_EMAILS is empty', async () => {
      const envAdmin = await insertUser('kfir@example.com', { roles: ['admin'], roleSource: 'env' });

      const result = await createService('').run();

      expect(result.status).toBe('skipped_empty');
      expect((await rawUser(envAdmin))?.roles).toEqual(['admin']);
      expect(logger.warn).toHaveBeenCalledWith(expect.anything(), 'admin_seed_skipped_empty');
    });

    it('should not touch the database for an empty list', async () => {
      const findSpy = vi.spyOn(userRepository, 'listAdmins');

      await createService('  , ').run();

      expect(findSpy).not.toHaveBeenCalled();
    });
  });

  describe('idempotency', () => {
    it('should write nothing and add no audit rows on a second run', async () => {
      await insertUser('kfir@example.com');
      await insertUser('old@example.com', { roles: ['admin'], roleSource: 'env' });
      const service = createService('kfir@example.com');
      await service.run();
      const rowsAfterFirst = await auditRows();

      const second = await service.run();

      expect([second.promoted, second.demoted, await auditRows()]).toEqual([0, 0, rowsAfterFirst]);
    });

    it('should not bump updatedAt on a second run', async () => {
      const id = await insertUser('kfir@example.com');
      const service = createService('kfir@example.com');
      await service.run();
      const updatedAt = (await rawUser(id))?.updatedAt;

      await service.run();

      expect((await rawUser(id))?.updatedAt).toEqual(updatedAt);
    });
  });

  describe('audit failure', () => {
    it('should keep the role change when the audit insert fails', async () => {
      const id = await insertUser('kfir@example.com');
      vi.spyOn(AdminAuditRepository.prototype, 'insert').mockRejectedValueOnce(new Error('audit down'));

      await createService('kfir@example.com').run();

      expect((await rawUser(id))?.roles).toEqual(['admin']);
      expect(logger.error).toHaveBeenCalledWith(expect.anything(), 'admin_audit_write_failed');
    });
  });
});
