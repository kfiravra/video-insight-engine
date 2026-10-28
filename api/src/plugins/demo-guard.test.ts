import { describe, it, expect, beforeAll, afterAll, beforeEach, afterEach, vi } from 'vitest';
import type { FastifyInstance, FastifyRequest } from 'fastify';
import { config } from '../config.js';
import { DemoRestrictedError, UnauthorizedError } from '../utils/errors.js';
import { buildTestApp, createMockContainer, type MockContainer } from '../test/helpers.js';
import { DemoUserResolver } from './demo-guard.js';

const DEMO_EMAIL = 'demo@example.com';
const DEMO_ID = 'dddddddddddddddddddddddd';
const OTHER_ID = 'aaaaaaaaaaaaaaaaaaaaaaaa';

function userDoc(id: string): { _id: { toString: () => string } } {
  return { _id: { toString: () => id } };
}

describe('DemoUserResolver', () => {
  let findByEmail: ReturnType<typeof vi.fn>;
  let email: string;
  let nowMs: number;
  let resolver: DemoUserResolver;

  beforeEach(() => {
    findByEmail = vi.fn().mockResolvedValue(userDoc(DEMO_ID));
    email = DEMO_EMAIL;
    nowMs = 1_000_000;
    resolver = new DemoUserResolver({ findByEmail }, () => email, () => nowMs);
  });

  describe('isDemoUser', () => {
    it('should return false without a database call when DEMO_USER_EMAIL is empty', async () => {
      email = '';

      const result = await resolver.isDemoUser(DEMO_ID);

      expect(result).toBe(false);
      expect(findByEmail).not.toHaveBeenCalled();
    });

    it('should return true for the id of the account carrying the demo email', async () => {
      const result = await resolver.isDemoUser(DEMO_ID);

      expect(result).toBe(true);
      expect(findByEmail).toHaveBeenCalledWith(DEMO_EMAIL);
    });

    it('should return false for any other user id', async () => {
      const result = await resolver.isDemoUser(OTHER_ID);

      expect(result).toBe(false);
    });

    it('should match the demo id regardless of hex casing', async () => {
      const result = await resolver.isDemoUser(DEMO_ID.toUpperCase());

      expect(result).toBe(true);
    });

    it('should not query again within the cache window once the demo user is resolved', async () => {
      await resolver.isDemoUser(DEMO_ID);
      nowMs += 9 * 60_000;
      await resolver.isDemoUser(OTHER_ID);

      expect(findByEmail).toHaveBeenCalledTimes(1);
    });

    it('should re-resolve the demo user after the found-entry window so a recreated account is guarded', async () => {
      await resolver.isDemoUser(DEMO_ID);
      findByEmail.mockResolvedValue(userDoc(OTHER_ID));
      nowMs += 11 * 60_000;

      const result = await resolver.isDemoUser(OTHER_ID);

      expect(result).toBe(true);
      expect(findByEmail).toHaveBeenCalledTimes(2);
    });

    it('should return false when no account carries the demo email', async () => {
      findByEmail.mockResolvedValue(null);

      const result = await resolver.isDemoUser(DEMO_ID);

      expect(result).toBe(false);
    });

    it('should cache a missing account only briefly', async () => {
      findByEmail.mockResolvedValue(null);
      await resolver.isDemoUser(DEMO_ID);
      nowMs += 30_000;
      await resolver.isDemoUser(DEMO_ID);
      expect(findByEmail).toHaveBeenCalledTimes(1);

      findByEmail.mockResolvedValue(userDoc(DEMO_ID));
      nowMs += 31_000;
      const result = await resolver.isDemoUser(DEMO_ID);

      expect(findByEmail).toHaveBeenCalledTimes(2);
      expect(result).toBe(true);
    });

    it('should drop the cached entry when the configured demo email changes', async () => {
      await resolver.isDemoUser(DEMO_ID);
      email = 'other-demo@example.com';
      findByEmail.mockResolvedValue(userDoc(OTHER_ID));

      const result = await resolver.isDemoUser(OTHER_ID);

      expect(findByEmail).toHaveBeenLastCalledWith('other-demo@example.com');
      expect(result).toBe(true);
    });

    it('should propagate a lookup failure and not cache it', async () => {
      findByEmail.mockRejectedValueOnce(new Error('mongo down'));

      await expect(resolver.isDemoUser(DEMO_ID)).rejects.toThrow('mongo down');
      const result = await resolver.isDemoUser(DEMO_ID);

      expect(result).toBe(true);
    });
  });
});

describe('demo guard plugin', () => {
  let app: FastifyInstance;
  let mockContainer: MockContainer;
  const originalEmail = config.DEMO_USER_EMAIL;

  beforeAll(async () => {
    mockContainer = createMockContainer();
    app = await buildTestApp(mockContainer);
    await app.ready();
  });

  afterAll(async () => {
    await app.close();
  });

  beforeEach(() => {
    vi.clearAllMocks();
    config.DEMO_USER_EMAIL = DEMO_EMAIL;
    mockContainer.userRepository.findByEmail.mockResolvedValue(userDoc(DEMO_ID));
  });

  afterEach(() => {
    config.DEMO_USER_EMAIL = originalEmail;
  });

  describe('requireNotDemo', () => {
    it('should throw DemoRestrictedError for the account the container user repository resolves as demo', async () => {
      const req = { user: { userId: DEMO_ID } } as unknown as FastifyRequest;

      await expect(app.requireNotDemo(req)).rejects.toBeInstanceOf(DemoRestrictedError);
    });

    it('should pass for any other user', async () => {
      const req = { user: { userId: OTHER_ID } } as unknown as FastifyRequest;

      await expect(app.requireNotDemo(req)).resolves.toBeUndefined();
    });

    it('should throw UnauthorizedError when used without an authenticated user', async () => {
      const req = {} as unknown as FastifyRequest;

      await expect(app.requireNotDemo(req)).rejects.toBeInstanceOf(UnauthorizedError);
    });
  });

  describe('isDemoUser', () => {
    it.each([
      ['the demo account', { user: { userId: DEMO_ID } }, true],
      ['any other user', { user: { userId: OTHER_ID } }, false],
      ['a request without a user', {}, false],
    ])('should answer for %s', async (_label, request, expected) => {
      await expect(app.isDemoUser(request as unknown as FastifyRequest)).resolves.toBe(expected);
    });
  });
});
