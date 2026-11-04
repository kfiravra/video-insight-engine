import { describe, it, expect, beforeAll, afterAll, beforeEach, vi } from 'vitest';
import { ObjectId } from 'mongodb';
import type { FastifyBaseLogger, FastifyInstance } from 'fastify';
import type { UsageSummary } from '@vie/types';
import { buildTestApp, createMockContainer, type MockContainer } from '../test/helpers.js';
import { getTestDb, mockLogger } from '../test/setup.js';
import { createBillingIndexes } from '../plugins/mongodb.js';
import { BillingRepository } from '../repositories/billing.repository.js';
import { UserRepository } from '../repositories/user.repository.js';
import { BillingService } from '../services/billing/billing.service.js';
import { DAY_MS, insertUser, makeClock } from '../services/billing/__tests__/billing-fixtures.js';
import type { TestClock } from '../services/billing/__tests__/billing-fixtures.js';

/** Keys that would carry a dollar amount; the payload may match none of them. */
const USD_LIKE_KEY = /usd|cost|price|limit$/i;
/** A string value that reads as money. */
const MONEY_VALUE = /^\s*[$€£]\s*\d|\d\s*(usd|dollars?)\b/i;

function collectDollarLeaks(node: unknown, path = '$'): string[] {
  if (Array.isArray(node)) {
    return node.flatMap((item, index) => collectDollarLeaks(item, `${path}[${index}]`));
  }
  if (node !== null && typeof node === 'object') {
    return Object.entries(node).flatMap(([key, value]) => [
      ...(USD_LIKE_KEY.test(key) ? [`${path}.${key}`] : []),
      ...collectDollarLeaks(value, `${path}.${key}`),
    ]);
  }
  return typeof node === 'string' && MONEY_VALUE.test(node) ? [path] : [];
}

/** Paths of numbers with a fraction: tokens and counts are whole, so a decimal is a money-shaped leak. */
function collectFractions(node: unknown, path = '$'): string[] {
  if (Array.isArray(node)) {
    return node.flatMap((item, index) => collectFractions(item, `${path}[${index}]`));
  }
  if (node !== null && typeof node === 'object') {
    return Object.entries(node).flatMap(([key, value]) => collectFractions(value, `${path}.${key}`));
  }
  return typeof node === 'number' && !Number.isInteger(node) ? [path] : [];
}

describe('GET /api/users/me/usage', () => {
  let app: FastifyInstance;
  let mockContainer: MockContainer;
  let clock: TestClock;
  let service: BillingService;
  let userId: string;

  const ctx = () => ({ actorId: new ObjectId().toHexString(), reason: 'usage test', auditId: new ObjectId().toHexString() });

  // The route destructures its services when it registers, so the real
  // BillingService (and the clock the tests move) must exist before the app.
  beforeAll(async () => {
    await createBillingIndexes(getTestDb());
    clock = makeClock('2026-11-01T10:00:00Z');
    service = new BillingService(
      new BillingRepository(getTestDb()),
      new UserRepository(getTestDb()),
      mockLogger as unknown as FastifyBaseLogger,
      clock.now,
    );
    mockContainer = createMockContainer();
    Object.assign(mockContainer, { billingService: service });
    app = await buildTestApp(mockContainer);
    await app.ready();
  });

  afterAll(async () => {
    await app.close();
  });

  beforeEach(async () => {
    vi.clearAllMocks();
    clock.set('2026-11-01T10:00:00Z');
    userId = await insertUser(getTestDb());
  });

  async function getUsage(query = ''): Promise<{ statusCode: number; body: UsageSummary }> {
    const token = app.jwt.sign({ userId, email: 'usage@example.com', type: 'access' });
    const response = await app.inject({
      method: 'GET',
      url: `/api/users/me/usage${query}`,
      headers: { authorization: `Bearer ${token}` },
    });
    return { statusCode: response.statusCode, body: response.json() };
  }

  async function holdMinutes(requestId: string, minutes: number): Promise<void> {
    await service.hold(requestId, await service.quote(userId, 'dQw4w9WgXcQ', minutes * 60));
  }

  describe('shapes', () => {
    it('should show a brand-new user with no account row the signup grant and nothing else', async () => {
      const { statusCode, body } = await getUsage();

      expect(statusCode).toBe(200);
      expect(body).toEqual({
        plan: { id: 'free', periodEnd: null },
        balance: 1000,
        planTokens: null,
        credits: { available: 1000, lots: [{ tokens: 1000, expiresAt: null }] },
        debt: 0,
        holds: [],
        maxVideoMinutes: 180,
        blocked: false,
        signupGrantTokens: 1000,
      });
    });

    it('should list the credit lots of a free account with their expiry', async () => {
      await service.grantLot(userId, { tokens: 3000, expiresAt: new Date(clock.now().getTime() + 30 * DAY_MS) }, ctx());
      await service.grantLot(userId, { tokens: 500, expiresAt: null }, ctx());

      const { body } = await getUsage();

      expect([body.balance, body.credits.available]).toEqual([4500, 4500]);
      expect(body.credits.lots).toEqual(
        expect.arrayContaining([
          { tokens: 3000, expiresAt: '2026-12-01T10:00:00.000Z' },
          { tokens: 500, expiresAt: null },
          { tokens: 1000, expiresAt: null },
        ]),
      );
      expect(body.planTokens).toBeNull();
    });

    it('should show a Pro account mid-cycle with the tokens its runs hold', async () => {
      await service.setPlan(userId, { planId: 'pro' }, ctx());
      clock.advance(10 * DAY_MS);
      await holdMinutes('run-pro-1', 90);

      const { body } = await getUsage();

      expect(body.plan.id).toBe('pro');
      expect(body.plan.periodEnd).not.toBeNull();
      // A 90-minute estimate (2,280) comes out of the cycle's 20,000 first.
      expect(body.planTokens).toMatchObject({ granted: 20_000, used: 2280, remaining: 17_720 });
      expect(body.planTokens?.cycleEnd).not.toBeNull();
      expect(body.balance).toBe(18_720);
    });

    it('should report an open hold with its video and estimate', async () => {
      await holdMinutes('run-hold-1', 12);

      const { body } = await getUsage();

      expect([body.holds, body.balance]).toEqual([[{ youtubeId: 'dQw4w9WgXcQ', tokens: 876 }], 124]);
    });

    it('should show a run that cost more than the account had as debt and a negative balance', async () => {
      await holdMinutes('run-debt-1', 10);
      await service.settleActual('run-debt-1', 0.5);

      const { body } = await getUsage();

      expect(body).toMatchObject({ balance: -500, debt: 500, holds: [], credits: { available: 0 } });
    });

    it('should flag a blocked account', async () => {
      await service.setCaps(userId, { blocked: true }, ctx());

      const { body } = await getUsage();

      expect(body.blocked).toBe(true);
    });

    it('should show an admin cap on the video length', async () => {
      await service.setCaps(userId, { maxVideoMinutes: 20 }, ctx());

      const { body } = await getUsage();

      expect(body.maxVideoMinutes).toBe(20);
    });
  });

  describe('no dollars', () => {
    async function everyShape(): Promise<UsageSummary[]> {
      const shapes: UsageSummary[] = [(await getUsage()).body];

      await service.grantLot(userId, { tokens: 600, expiresAt: new Date(clock.now().getTime() + DAY_MS) }, ctx());
      shapes.push((await getUsage()).body);

      await service.setPlan(userId, { planId: 'pro' }, ctx());
      await holdMinutes('run-shapes-1', 20);
      shapes.push((await getUsage()).body);

      // Settled at a cost no one priced in: a balance in debt.
      await service.settleActual('run-shapes-1', 10.1234567);
      shapes.push((await getUsage()).body);

      await service.setCaps(userId, { blocked: true }, ctx());
      shapes.push((await getUsage()).body);
      return shapes;
    }

    it('should carry no usd, cost, price or limit key anywhere in any shape', async () => {
      const leaks = (await everyShape()).flatMap((shape) => collectDollarLeaks(shape));

      expect(leaks).toEqual([]);
    });

    it('should hold only whole numbers, ids, flags and ISO dates', async () => {
      const shapes = await everyShape();
      const fractions = shapes.flatMap((shape) => collectFractions(shape));

      expect(fractions).toEqual([]);
      expect(shapes[3].debt).toBeGreaterThan(0);
    });

    it('should catch a dollar key when one is added (the walker is not vacuous)', () => {
      const leaks = collectDollarLeaks({ plan: { id: 'free' }, today: { rawUsd: 0.4 }, capUsd: 2, dailyLimit: 3, note: '$2.00' });

      expect(leaks).toEqual(['$.today.rawUsd', '$.capUsd', '$.dailyLimit', '$.note']);
    });
  });

  describe('removed dollar view (P3.2)', () => {
    it('should answer tokens, never dollars, to an old client still asking for ?view=usd', async () => {
      const { statusCode, body } = await getUsage('?view=usd');

      expect(statusCode).toBe(200);
      expect([collectDollarLeaks(body), body.plan.id]).toEqual([[], 'free']);
    });
  });

  it('should require a signed-in user', async () => {
    const response = await app.inject({ method: 'GET', url: '/api/users/me/usage' });

    expect(response.statusCode).toBe(401);
  });
});
