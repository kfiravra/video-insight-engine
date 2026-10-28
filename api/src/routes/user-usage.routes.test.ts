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
import { DAY_MS, HOUR_MS, insertUser, makeClock } from '../services/billing/__tests__/billing-fixtures.js';
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

/** Paths of numbers with a fraction: minutes and counts are whole, so a decimal is a money-shaped leak. */
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
    it('should show a brand-new user with no account row a ready free video and nothing else', async () => {
      const { statusCode, body } = await getUsage();

      expect(statusCode).toBe(200);
      expect(body).toEqual({
        plan: { id: 'free', periodEnd: null },
        free: { available: true, nextFreeAt: null, maxVideoMinutes: 30, perWindow: 1 },
        planMinutes: { granted: 0, used: 0, remaining: 0, cycleEnd: null },
        credits: { available: 0, lots: [] },
        holds: [],
        maxVideoMinutes: 30,
        blocked: false,
      });
    });

    it('should list the credit lots of a free account and lift its video length to 180', async () => {
      await service.grantLot(userId, { minutes: 120, expiresAt: new Date(clock.now().getTime() + 30 * DAY_MS) }, ctx());
      await service.grantLot(userId, { minutes: 30, expiresAt: null }, ctx());

      const { body } = await getUsage();

      expect(body.credits.available).toBe(150);
      expect(body.credits.lots).toEqual(
        expect.arrayContaining([
          { minutes: 120, expiresAt: '2026-12-01T10:00:00.000Z' },
          { minutes: 30, expiresAt: null },
        ]),
      );
      expect(body.free.available).toBe(true);
      expect(body.maxVideoMinutes).toBe(180);
    });

    it('should show a Pro account mid-cycle with the minutes it used', async () => {
      await service.setPlan(userId, { planId: 'pro' }, ctx());
      clock.advance(10 * DAY_MS);
      await holdMinutes('run-pro-1', 90);

      const { body } = await getUsage();

      expect(body.plan.id).toBe('pro');
      expect(body.plan.periodEnd).not.toBeNull();
      expect(body.planMinutes).toMatchObject({ granted: 600, used: 90, remaining: 510 });
      expect(body.planMinutes.cycleEnd).not.toBeNull();
      expect(body.maxVideoMinutes).toBe(180);
    });

    it('should report an open hold with its video and minutes', async () => {
      await service.setPlan(userId, { planId: 'pro' }, ctx());
      await holdMinutes('run-hold-1', 12);

      const { body } = await getUsage();

      expect(body.holds).toEqual([{ youtubeId: 'dQw4w9WgXcQ', minutes: 12 }]);
    });

    it('should show zero free videos per window when an admin turned the free video off (W3)', async () => {
      await service.setCaps(userId, { freeVideosPerDay: 0 }, ctx());

      const { body } = await getUsage();

      expect(body.free).toEqual({ available: false, nextFreeAt: null, maxVideoMinutes: 30, perWindow: 0 });
    });

    it('should flag a blocked account', async () => {
      await service.setCaps(userId, { blocked: true }, ctx());

      const { body } = await getUsage();

      expect(body.blocked).toBe(true);
    });

    it('should show when the next free video opens once the free slot is used', async () => {
      await holdMinutes('run-free-1', 10);

      const { body } = await getUsage();

      expect(body.free.available).toBe(false);
      expect(body.free.nextFreeAt).toBe(new Date(clock.now().getTime() + 24 * HOUR_MS).toISOString());
    });
  });

  describe('no dollars', () => {
    async function everyShape(): Promise<UsageSummary[]> {
      const shapes: UsageSummary[] = [(await getUsage()).body];

      await service.grantLot(userId, { minutes: 60, expiresAt: new Date(clock.now().getTime() + DAY_MS) }, ctx());
      shapes.push((await getUsage()).body);

      await service.setPlan(userId, { planId: 'pro' }, ctx());
      await holdMinutes('run-shapes-1', 20);
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
      const fractions = (await everyShape()).flatMap((shape) => collectFractions(shape));

      expect(fractions).toEqual([]);
    });

    it('should catch a dollar key when one is added (the walker is not vacuous)', () => {
      const leaks = collectDollarLeaks({ plan: { id: 'free' }, today: { rawUsd: 0.4 }, capUsd: 2, dailyLimit: 3, note: '$2.00' });

      expect(leaks).toEqual(['$.today.rawUsd', '$.capUsd', '$.dailyLimit', '$.note']);
    });
  });

  describe('removed dollar view (P3.2)', () => {
    it('should answer minutes, never dollars, to an old client still asking for ?view=usd', async () => {
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
