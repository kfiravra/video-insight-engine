import { describe, it, expect } from 'vitest';
import { getTestDb } from '../../api/src/test/setup.js';
import { NoAdminsKeptError, demoteAdmins } from '../demote-admins.js';

const KEEPER = 'kfir@example.com';

async function seedUser(email: string, extra: Record<string, unknown> = {}): Promise<void> {
  const now = new Date();
  await getTestDb().collection('users').insertOne({
    email,
    passwordHash: 'hash-never-printed',
    name: email.split('@')[0],
    createdAt: now,
    updatedAt: now,
    ...extra,
  });
}

async function seedAdmin(email: string, extra: Record<string, unknown> = {}): Promise<void> {
  await seedUser(email, { role: 'admin', ...extra });
}

async function allUsers() {
  return getTestDb().collection('users').find({}).sort({ email: 1 }).toArray();
}

async function adminEmails(): Promise<string[]> {
  const admins = await getTestDb().collection('users').find({ role: 'admin' }).sort({ email: 1 }).toArray();
  return admins.map((admin) => admin.email);
}

describe('demote-admins', () => {
  describe('demoteAdmins', () => {
    it('should return the would-demote list and write nothing when dryRun is set', async () => {
      await seedAdmin(KEEPER);
      await seedAdmin('a@example.com');
      await seedAdmin('b@example.com');
      const before = await allUsers();

      const result = await demoteAdmins(getTestDb(), { keep: [KEEPER], dryRun: true });

      expect(result.demoted.sort()).toEqual(['a@example.com', 'b@example.com']);
      expect(result.kept).toEqual([KEEPER]);
      expect(result.modifiedCount).toBe(0);
      expect(await allUsers()).toEqual(before);
    });

    it('should unset role for every admin who is not kept and keep the kept admins', async () => {
      await seedAdmin(KEEPER);
      await seedAdmin('a@example.com');
      await seedAdmin('b@example.com');

      const result = await demoteAdmins(getTestDb(), { keep: [KEEPER], dryRun: false });

      expect(result.modifiedCount).toBe(2);
      expect(await adminEmails()).toEqual([KEEPER]);
    });

    it('should keep every admin on a multi-email keep list', async () => {
      await seedAdmin(KEEPER);
      await seedAdmin('second@example.com');
      await seedAdmin('gone@example.com');

      await demoteAdmins(getTestDb(), { keep: [KEEPER, 'second@example.com'], dryRun: false });

      expect(await adminEmails()).toEqual([KEEPER, 'second@example.com']);
    });

    it('should change nothing but role on a demoted admin', async () => {
      await seedAdmin(KEEPER);
      await seedAdmin('eval@example.com', { isEvalUser: true, tier: 'pro' });
      const expected = { ...(await getTestDb().collection('users').findOne({ email: 'eval@example.com' })) };
      delete expected.role;

      await demoteAdmins(getTestDb(), { keep: [KEEPER], dryRun: false });

      expect(await getTestDb().collection('users').findOne({ email: 'eval@example.com' })).toEqual(expected);
    });

    it('should leave a non-admin user untouched', async () => {
      await seedAdmin(KEEPER);
      await seedAdmin('a@example.com');
      await seedUser('plain@example.com', { isEvalUser: true });
      const before = await getTestDb().collection('users').findOne({ email: 'plain@example.com' });

      await demoteAdmins(getTestDb(), { keep: [KEEPER], dryRun: false });

      expect(await getTestDb().collection('users').findOne({ email: 'plain@example.com' })).toEqual(before);
    });

    it('should refuse and write nothing when the keep email is not an admin', async () => {
      await seedAdmin('a@example.com');
      await seedAdmin('b@example.com');
      await seedUser(KEEPER);
      const before = await allUsers();

      await expect(demoteAdmins(getTestDb(), { keep: [KEEPER], dryRun: false })).rejects.toThrow(NoAdminsKeptError);

      expect(await allUsers()).toEqual(before);
    });

    it('should refuse when the keep email matches no user at all', async () => {
      await seedAdmin('a@example.com');
      const before = await allUsers();

      await expect(demoteAdmins(getTestDb(), { keep: ['typo@example.com'], dryRun: false })).rejects.toThrow(
        NoAdminsKeptError,
      );

      expect(await allUsers()).toEqual(before);
    });

    it('should refuse on a dry run too, so the preview matches the real run', async () => {
      await seedAdmin('a@example.com');

      await expect(demoteAdmins(getTestDb(), { keep: ['typo@example.com'], dryRun: true })).rejects.toThrow(
        NoAdminsKeptError,
      );
    });

    it('should trim and lowercase keep emails and drop empty entries', async () => {
      await seedAdmin(KEEPER);
      await seedAdmin('a@example.com');

      await demoteAdmins(getTestDb(), { keep: ['  KFIR@Example.COM ', '', '   '], dryRun: false });

      expect(await adminEmails()).toEqual([KEEPER]);
    });

    it('should match a mixed-case stored admin email against the keep list', async () => {
      await seedAdmin('Kfir@Example.com');
      await seedAdmin('Other@Example.com');

      await demoteAdmins(getTestDb(), { keep: [KEEPER], dryRun: false });

      expect(await adminEmails()).toEqual(['Kfir@Example.com']);
    });

    it('should report keep emails that are not admins as unmatched', async () => {
      await seedAdmin(KEEPER);
      await seedUser('plain@example.com');

      const result = await demoteAdmins(getTestDb(), { keep: [KEEPER, 'plain@example.com', 'ghost@example.com'], dryRun: true });

      expect(result.unmatchedKeep).toEqual(['plain@example.com', 'ghost@example.com']);
    });

    it('should do nothing when every admin is on the keep list', async () => {
      await seedAdmin(KEEPER);
      const before = await allUsers();

      const result = await demoteAdmins(getTestDb(), { keep: [KEEPER], dryRun: false });

      expect(result).toMatchObject({ demoted: [], modifiedCount: 0 });
      expect(await allUsers()).toEqual(before);
    });

    it('should reject a keep list that is empty after normalising', async () => {
      await seedAdmin('a@example.com');
      const before = await allUsers();

      await expect(demoteAdmins(getTestDb(), { keep: [' ', ''], dryRun: false })).rejects.toThrow('--keep');

      expect(await allUsers()).toEqual(before);
    });
  });
});
