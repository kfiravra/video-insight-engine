import { describe, it, expect } from 'vitest';
import bcrypt from 'bcrypt';
import { getTestDb } from '../../api/src/test/setup.js';
import { AdminResetRequiredError, upsertUser } from '../create-user.js';

const PASSWORD = 'Sup3rSecret';

async function findUser(email: string) {
  return getTestDb().collection('users').findOne({ email });
}

async function seedAdmin(email: string): Promise<void> {
  const now = new Date();
  await getTestDb().collection('users').insertOne({
    email,
    passwordHash: await bcrypt.hash('OldAdminPass1', 4),
    name: 'Root',
    role: 'admin',
    createdAt: now,
    updatedAt: now,
  });
}

describe('create-user', () => {
  describe('upsertUser', () => {
    it('should create a user without a role when the email is new', async () => {
      const outcome = await upsertUser(getTestDb(), { email: 'new@example.com', password: PASSWORD });

      const user = await findUser('new@example.com');
      expect(outcome).toBe('created');
      expect(user).not.toBeNull();
      expect(user).not.toHaveProperty('role');
    });

    it('should write the document shape UserRepository.create writes', async () => {
      await upsertUser(getTestDb(), { email: 'shape@example.com', password: PASSWORD, name: 'Shape' });

      const user = await findUser('shape@example.com');
      expect(user).toMatchObject({
        email: 'shape@example.com',
        name: 'Shape',
        preferences: { defaultSummarizedFolder: null, theme: 'system' },
        usage: { videosThisMonth: 0 },
      });
      expect(user?.createdAt).toBeInstanceOf(Date);
      expect(user?.updatedAt).toBeInstanceOf(Date);
    });

    it('should store a bcrypt hash that verifies the password', async () => {
      await upsertUser(getTestDb(), { email: 'hash@example.com', password: PASSWORD });

      const user = await findUser('hash@example.com');
      expect(user?.passwordHash).not.toBe(PASSWORD);
      expect(await bcrypt.compare(PASSWORD, user?.passwordHash)).toBe(true);
    });

    it('should trim and lowercase the email', async () => {
      await upsertUser(getTestDb(), { email: '  Mixed.Case@Example.COM ', password: PASSWORD });

      expect(await findUser('mixed.case@example.com')).not.toBeNull();
    });

    it('should treat a differently-cased email as the same user', async () => {
      await upsertUser(getTestDb(), { email: 'dup@example.com', password: PASSWORD });

      const outcome = await upsertUser(getTestDb(), { email: ' DUP@example.com', password: 'Other1234x' });

      expect(outcome).toBe('unchanged');
      expect(await getTestDb().collection('users').countDocuments({ email: 'dup@example.com' })).toBe(1);
    });

    it('should leave an existing user untouched when re-run without resetPassword', async () => {
      await upsertUser(getTestDb(), { email: 'again@example.com', password: PASSWORD });
      const before = await findUser('again@example.com');

      const outcome = await upsertUser(getTestDb(), { email: 'again@example.com', password: 'Different123' });

      expect(outcome).toBe('unchanged');
      expect(await findUser('again@example.com')).toEqual(before);
    });

    it('should change only passwordHash and updatedAt when resetPassword is set', async () => {
      await upsertUser(getTestDb(), { email: 'reset@example.com', password: PASSWORD, name: 'Reset' });
      const before = await findUser('reset@example.com');

      const outcome = await upsertUser(getTestDb(), {
        email: 'reset@example.com',
        password: 'Different123',
        name: 'Ignored',
        resetPassword: true,
      });

      const after = await findUser('reset@example.com');
      expect(outcome).toBe('password-reset');
      expect(await bcrypt.compare('Different123', after?.passwordHash)).toBe(true);
      expect({ ...after, passwordHash: undefined, updatedAt: undefined }).toEqual({
        ...before,
        passwordHash: undefined,
        updatedAt: undefined,
      });
    });

    it('should refuse to touch an existing admin when resetPassword is not set', async () => {
      await seedAdmin('root@example.com');
      const before = await findUser('root@example.com');

      await expect(
        upsertUser(getTestDb(), { email: 'root@example.com', password: PASSWORD }),
      ).rejects.toThrow(AdminResetRequiredError);

      expect(await findUser('root@example.com')).toEqual(before);
    });

    it('should keep role admin untouched when resetting an admin password explicitly', async () => {
      await seedAdmin('root@example.com');

      const outcome = await upsertUser(getTestDb(), {
        email: 'root@example.com',
        password: PASSWORD,
        resetPassword: true,
      });

      const after = await findUser('root@example.com');
      expect(outcome).toBe('password-reset');
      expect(after?.role).toBe('admin');
      expect(await bcrypt.compare(PASSWORD, after?.passwordHash)).toBe(true);
    });

    it('should never give a non-admin a role when resetting their password', async () => {
      await upsertUser(getTestDb(), { email: 'plain@example.com', password: PASSWORD });

      await upsertUser(getTestDb(), { email: 'plain@example.com', password: 'Different123', resetPassword: true });

      expect(await findUser('plain@example.com')).not.toHaveProperty('role');
    });

    it('should reject a blank email or password', async () => {
      await expect(upsertUser(getTestDb(), { email: '  ', password: PASSWORD })).rejects.toThrow('required');
      await expect(upsertUser(getTestDb(), { email: 'x@example.com', password: '' })).rejects.toThrow('required');
    });
  });
});
