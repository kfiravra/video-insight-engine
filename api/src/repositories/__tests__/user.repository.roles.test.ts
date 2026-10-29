import { readFileSync } from 'node:fs';
import { describe, it, expect, beforeAll, afterAll, beforeEach } from 'vitest';
import { MongoMemoryServer } from 'mongodb-memory-server';
import { MongoClient, Db, ObjectId } from 'mongodb';
import { PERMISSIONS, READ_PERMISSIONS } from '@vie/types';
import { UserRepository, CreateUserData, effectiveRoles } from '../user.repository.js';

interface RoleRuleCase {
  name: string;
  user: Record<string, unknown>;
  effectiveRoles: string[];
  activeAdmin: boolean;
}

// Shared with services/admin/tests/test_roles.py: vie-admin's login and session
// middleware apply the same rule in Python, and both are held to these cases.
const { roleRules } = JSON.parse(
  readFileSync(new URL('../../../../packages/types/test-vectors/admin-auth.json', import.meta.url), 'utf8'),
) as { roleRules: RoleRuleCase[] };

describe('UserRepository roles', () => {
  let mongod: MongoMemoryServer;
  let client: MongoClient;
  let db: Db;
  let repository: UserRepository;

  beforeAll(async () => {
    mongod = await MongoMemoryServer.create();
    client = new MongoClient(mongod.getUri());
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

  function createUserData(overrides: Partial<CreateUserData> = {}): CreateUserData {
    const uniqueId = new ObjectId().toString().slice(0, 8);
    return {
      email: `test-${uniqueId}@example.com`,
      passwordHash: '$2b$10$hashedpassword',
      name: 'Test User',
      ...overrides,
    };
  }

  async function createWithFields(email: string, fields: Record<string, unknown>) {
    const user = await repository.create(createUserData({ email }));
    await db.collection('users').updateOne({ _id: user._id }, { $set: fields });
    return user._id.toString();
  }

  describe('effectiveRoles', () => {
    it('should read a legacy role:admin with no roles array as admin', () => {
      expect(effectiveRoles({ role: 'admin' })).toEqual(['admin']);
    });

    it('should let an empty roles array override a leftover legacy role', () => {
      expect(effectiveRoles({ roles: [], role: 'admin' })).toEqual([]);
    });

    it('should drop role values this build does not know', () => {
      expect(effectiveRoles({ roles: ['admin', 'owner' as 'admin'] })).toEqual(['admin']);
    });

    it('should return no roles for a user with neither field', () => {
      expect(effectiveRoles({})).toEqual([]);
    });

    it('should return no roles for a missing user', () => {
      expect(effectiveRoles(null)).toEqual([]);
    });
  });

  describe('hasPermission', () => {
    it.each(PERMISSIONS.map((permission) => [permission]))(
      'should grant %s to an admin',
      async (permission) => {
        const id = await createWithFields('admin@example.com', { roles: ['admin'], roleSource: 'env' });

        expect(await repository.hasPermission(id, permission)).toBe(true);
      },
    );

    it('should deny every permission to a user without roles', async () => {
      const user = await repository.create(createUserData());

      expect(await repository.hasPermission(user._id.toString(), 'billing:read')).toBe(false);
    });

    it('should grant a legacy role:admin user until the migration runs', async () => {
      const id = await createWithFields('legacy@example.com', { role: 'admin' });

      expect(await repository.hasPermission(id, 'users:delete')).toBe(true);
    });

    it('should deny a demoted user whose legacy role is still stored', async () => {
      const id = await createWithFields('demoted@example.com', { role: 'admin', roles: [] });

      expect(await repository.hasPermission(id, 'users:delete')).toBe(false);
    });

    it('should deny a soft-deleted admin', async () => {
      const id = await createWithFields('gone@example.com', { roles: ['admin'], deletedAt: new Date() });

      expect(await repository.hasPermission(id, 'billing:read')).toBe(false);
    });

    it('should deny an unknown user', async () => {
      expect(await repository.hasPermission(new ObjectId().toString(), 'billing:read')).toBe(false);
    });

    it('should deny a malformed id without querying', async () => {
      expect(await repository.hasPermission('not-an-object-id', 'billing:read')).toBe(false);
    });
  });

  describe('machine-key read set', () => {
    it('should contain only permissions an admin also holds', () => {
      expect([...READ_PERMISSIONS].every((permission) => PERMISSIONS.includes(permission))).toBe(true);
    });

    it('should exclude every mutating permission', () => {
      expect(READ_PERMISSIONS.has('users:delete')).toBe(false);
    });
  });

  describe('mayRunCold', () => {
    it('should allow an admin by roles', async () => {
      const id = await createWithFields('admin@example.com', { roles: ['admin'] });

      expect(await repository.mayRunCold(id)).toBe(true);
    });

    it('should allow a legacy role:admin user', async () => {
      const id = await createWithFields('legacy@example.com', { role: 'admin' });

      expect(await repository.mayRunCold(id)).toBe(true);
    });

    it('should allow the eval account', async () => {
      const id = await createWithFields('eval@example.com', { isEvalUser: true });

      expect(await repository.mayRunCold(id)).toBe(true);
    });

    it('should refuse an ordinary user', async () => {
      const user = await repository.create(createUserData());

      expect(await repository.mayRunCold(user._id.toString())).toBe(false);
    });

    it('should refuse a demoted user whose legacy role is still stored', async () => {
      const id = await createWithFields('demoted@example.com', { role: 'admin', roles: [] });

      expect(await repository.mayRunCold(id)).toBe(false);
    });
  });

  describe('listAdmins', () => {
    it('should list roles admins and legacy admins but not ordinary or demoted users', async () => {
      await createWithFields('env@example.com', { roles: ['admin'], roleSource: 'env' });
      await createWithFields('legacy@example.com', { role: 'admin' });
      await createWithFields('demoted@example.com', { role: 'admin', roles: [] });
      await repository.create(createUserData({ email: 'plain@example.com' }));

      const admins = await repository.listAdmins();

      expect(admins.map((admin) => admin.email).sort()).toEqual(['env@example.com', 'legacy@example.com']);
    });

    it('should report env and granted sources, reading a legacy admin as granted', async () => {
      await createWithFields('env@example.com', { roles: ['admin'], roleSource: 'env' });
      await createWithFields('granted@example.com', { roles: ['admin'], roleSource: 'granted' });
      await createWithFields('legacy@example.com', { role: 'admin' });

      const admins = await repository.listAdmins();

      const sources = Object.fromEntries(admins.map((admin) => [admin.email, admin.roleSource]));
      expect(sources).toEqual({
        'env@example.com': 'env',
        'granted@example.com': 'granted',
        'legacy@example.com': 'granted',
      });
    });

    it('should flag a soft-deleted admin through deletedAt', async () => {
      const deletedAt = new Date('2026-05-01T00:00:00Z');
      await createWithFields('gone@example.com', { roles: ['admin'], deletedAt });

      const [admin] = await repository.listAdmins();

      expect(admin.deletedAt).toEqual(deletedAt);
    });
  });

  describe('findRoleSummariesByEmails', () => {
    it('should match emails ignoring case', async () => {
      await repository.create(createUserData({ email: 'Mixed.Case@Example.com' }));

      const found = await repository.findRoleSummariesByEmails(['mixed.case@example.com']);

      expect(found.map((user) => user.email)).toEqual(['Mixed.Case@Example.com']);
    });

    it('should report no source for a user without roles', async () => {
      await repository.create(createUserData({ email: 'plain@example.com' }));

      const [user] = await repository.findRoleSummariesByEmails(['plain@example.com']);

      expect(user.roleSource).toBeNull();
    });

    it('should return nothing for an empty list', async () => {
      expect(await repository.findRoleSummariesByEmails([])).toEqual([]);
    });

    it('should skip emails that have no account', async () => {
      expect(await repository.findRoleSummariesByEmails(['nobody@example.com'])).toEqual([]);
    });
  });

  describe('setRoles', () => {
    it('should store roles with their source', async () => {
      const user = await repository.create(createUserData());

      await repository.setRoles(user._id.toString(), ['admin'], 'env');

      const stored = await repository.findById(user._id.toString());
      expect([stored?.roles, stored?.roleSource]).toEqual([['admin'], 'env']);
    });

    it('should store a repeated role once', async () => {
      const user = await repository.create(createUserData());

      await repository.setRoles(user._id.toString(), ['admin', 'admin'], 'granted');

      expect((await repository.findById(user._id.toString()))?.roles).toEqual(['admin']);
    });

    it('should demote with an empty list, unsetting the source and the legacy role', async () => {
      const id = await createWithFields('legacy@example.com', { role: 'admin', roles: ['admin'], roleSource: 'env' });

      await repository.setRoles(id, [], 'env');

      const raw = await db.collection('users').findOne({ _id: new ObjectId(id) });
      expect([raw?.roles, raw?.roleSource, raw?.role]).toEqual([[], undefined, undefined]);
    });

    it('should leave a demoted user without any permission', async () => {
      const id = await createWithFields('legacy@example.com', { role: 'admin' });

      await repository.setRoles(id, [], 'granted');

      expect(await repository.hasPermission(id, 'billing:read')).toBe(false);
    });

    it('should report false for an unknown user', async () => {
      expect(await repository.setRoles(new ObjectId().toString(), ['admin'], 'env')).toBe(false);
    });
  });

  describe('shared role rules (also enforced by vie-admin)', () => {
    it.each(roleRules.map((rule) => [rule.name, rule] as const))(
      'should resolve effective roles: %s',
      (_name, rule) => {
        expect(effectiveRoles(rule.user as Parameters<typeof effectiveRoles>[0])).toEqual(rule.effectiveRoles);
      },
    );

    it.each(roleRules.map((rule) => [rule.name, rule] as const))(
      'should decide admin access from the stored document: %s',
      async (_name, rule) => {
        const user = await repository.create(createUserData());
        await db.collection('users').updateOne({ _id: user._id }, { $set: rule.user });

        expect(await repository.hasPermission(user._id.toString(), 'billing:read')).toBe(rule.activeAdmin);
      },
    );
  });
});
