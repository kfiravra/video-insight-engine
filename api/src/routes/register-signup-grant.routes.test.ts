/**
 * The one-time signup grant at registration, over the real AuthService,
 * UserRepository and BillingService on the test replica set: a new user's
 * usage tile shows the 1,000 tokens at once, the grant is written once
 * (`grant:signup:<userId>`) however often the account is read, and a failed
 * billing write never fails the registration — the first billing read opens
 * the same account with its grant.
 */
import { describe, it, expect, beforeAll, afterAll, beforeEach, vi } from 'vitest';
import type { FastifyBaseLogger, FastifyInstance } from 'fastify';
import { buildTestApp, createMockContainer, type MockContainer } from '../test/helpers.js';
import { getTestDb, mockLogger } from '../test/setup.js';
import { createBillingIndexes } from '../plugins/mongodb.js';
import { BillingRepository } from '../repositories/billing.repository.js';
import { UserRepository } from '../repositories/user.repository.js';
import { AuthService } from '../services/auth.service.js';
import { BillingService } from '../services/billing/billing.service.js';

describe('the signup grant at registration', () => {
  let app: FastifyInstance;
  let billing: BillingService;
  let ipSuffix = 0;

  const register = (email: string) => app.inject({
    method: 'POST',
    url: '/api/auth/register',
    // A fresh IP per call: the register route allows 5 per hour per IP.
    remoteAddress: `198.51.100.${++ipSuffix}`,
    headers: { 'content-type': 'application/json' },
    payload: { email, password: 'SecurePass123', name: 'New User' },
  });
  const usage = (accessToken: string) =>
    app.inject({ method: 'GET', url: '/api/users/me/usage', headers: { authorization: `Bearer ${accessToken}` } });
  const signupGrants = (userId: string) =>
    getTestDb().collection('ledgerEntries').countDocuments({ idempotencyKey: `grant:signup:${userId}` });

  beforeAll(async () => {
    await createBillingIndexes(getTestDb());
    await getTestDb().collection('users').createIndex({ email: 1 }, { unique: true });
    const logger = mockLogger as unknown as FastifyBaseLogger;
    const userRepository = new UserRepository(getTestDb());
    billing = new BillingService(new BillingRepository(getTestDb()), userRepository, logger);
    app = await buildTestApp({
      ...createMockContainer(),
      userRepository,
      authService: new AuthService(userRepository, logger),
      billingService: billing,
    } as unknown as Partial<MockContainer>);
    await app.ready();
  });

  afterAll(async () => {
    await app.close();
  });

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('should show the new user 1,000 tokens on the usage tile right after registering', async () => {
    const registered = await register('fresh@example.com');
    const { accessToken, user } = registered.json();

    const tile = await usage(accessToken);

    expect(registered.statusCode).toBe(201);
    expect(tile.json()).toMatchObject({ balance: 1000, signupGrantTokens: 1000, credits: { lots: [{ tokens: 1000, expiresAt: null }] } });
    expect(await signupGrants(user.id)).toBe(1);
  });

  it('should write the grant once however often the account is read afterwards', async () => {
    const { accessToken, user } = (await register('twice@example.com')).json();

    await usage(accessToken);
    await usage(accessToken);
    await billing.ensureAccount(user.id);

    expect(await signupGrants(user.id)).toBe(1);
  });

  it('should register the user when the billing write fails, and grant on the first billing read', async () => {
    vi.spyOn(billing, 'ensureAccount').mockRejectedValueOnce(new Error('mongo: write conflict'));

    const registered = await register('late@example.com');
    const { accessToken, user } = registered.json();

    expect([registered.statusCode, await signupGrants(user.id)]).toEqual([201, 0]);
    expect((await usage(accessToken)).json()).toMatchObject({ balance: 1000, signupGrantTokens: 1000 });
    expect(await signupGrants(user.id)).toBe(1);
  });
});
