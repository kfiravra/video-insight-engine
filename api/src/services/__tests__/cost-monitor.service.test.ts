import { describe, it, expect, vi, beforeAll, beforeEach } from 'vitest';
import { ObjectId } from 'mongodb';
import type { FastifyBaseLogger } from 'fastify';
import { getTestDb, mockLogger } from '../../test/setup.js';
import { CostMonitorService } from '../cost-monitor.service.js';
import { UserCostRepository, getUtcDateKey } from '../../repositories/user-cost.repository.js';

// Mock config with per-tier USD limits
vi.mock('../../config.js', () => ({
  config: {
    COST_LIMITS_PER_TIER: { free: 2, pro: 20, team: -1 },
  },
}));

describe('CostMonitorService', () => {
  let costMonitorService: CostMonitorService;

  // Mirror the prod index (plugins/mongodb.ts). Without it, concurrent upserts
  // on the same (userId, date) race into two documents and findOne sees one.
  beforeAll(async () => {
    await getTestDb()
      .collection('userCosts')
      .createIndex({ userId: 1, date: 1 }, { unique: true });
  });

  beforeEach(() => {
    vi.clearAllMocks();

    const db = getTestDb();
    const userCostRepo = new UserCostRepository(db);
    costMonitorService = new CostMonitorService(
      db,
      mockLogger as unknown as FastifyBaseLogger,
      userCostRepo,
    );
  });

  describe('getTierLimit', () => {
    it('should return the configured limit for the free tier', () => {
      expect(costMonitorService.getTierLimit('free')).toBe(2);
    });

    it('should return the configured limit for the pro tier', () => {
      expect(costMonitorService.getTierLimit('pro')).toBe(20);
    });

    it('should return -1 for the team tier (unlimited)', () => {
      expect(costMonitorService.getTierLimit('team')).toBe(-1);
    });
  });

  describe('checkUserCanProcess', () => {
    const userId = new ObjectId().toString();

    it('should allow a free user with no usage today', async () => {
      const result = await costMonitorService.checkUserCanProcess(userId, 'free');

      expect(result.allowed).toBe(true);
      expect(result.usedUsd).toBe(0);
      expect(result.limitUsd).toBe(2);
      expect(result.remainingUsd).toBe(2);
      expect(result.tier).toBe('free');
      expect(() => new Date(result.resetAt).toISOString()).not.toThrow();
    });

    it('should block a free user once usage reaches the limit', async () => {
      await costMonitorService.incrementUserCost(userId, 2.0, { countVideo: true });

      const result = await costMonitorService.checkUserCanProcess(userId, 'free');

      expect(result.allowed).toBe(false);
      expect(result.usedUsd).toBeCloseTo(2.0);
      expect(result.remainingUsd).toBe(0);
    });

    it('should treat the team tier as unlimited', async () => {
      await costMonitorService.incrementUserCost(userId, 999, { countVideo: true });

      const result = await costMonitorService.checkUserCanProcess(userId, 'team');

      expect(result.allowed).toBe(true);
      // null (not Infinity) so JSON serialization is honest.
      expect(result.remainingUsd).toBeNull();
    });

    it('should expose resetAt at the next UTC midnight', async () => {
      const result = await costMonitorService.checkUserCanProcess(userId, 'pro');
      expect(result.resetAt.endsWith('T00:00:00.000Z')).toBe(true);
    });
  });

  describe('incrementUserCost', () => {
    it('should accumulate per-user cost atomically across concurrent writes', async () => {
      const userId = new ObjectId().toString();

      await Promise.all([
        costMonitorService.incrementUserCost(userId, 0.5, { countVideo: true }),
        costMonitorService.incrementUserCost(userId, 0.25, { countVideo: false }),
        costMonitorService.incrementUserCost(userId, 0.25, { countVideo: true }),
      ]);

      const used = await costMonitorService.getUserDailyCost(userId);
      expect(used).toBeCloseTo(1.0);
    });
  });

  describe('reconcileUserDay', () => {
    it('should rebuild totals from llm_usage rows (Python schema)', async () => {
      const db = getTestDb();
      const userId = new ObjectId().toString();
      const today = getUtcDateKey();
      const now = new Date(`${today}T12:00:00Z`);

      await db.collection('llm_usage').insertMany([
        { user_id: userId, cost_usd: 0.4, timestamp: now },
        { user_id: userId, cost_usd: 0.6, timestamp: now },
        { user_id: 'other-user', cost_usd: 99, timestamp: now },
      ]);

      const total = await costMonitorService.reconcileUserDay(userId, today);

      expect(total).toBeCloseTo(1.0);
      expect(await costMonitorService.getUserDailyCost(userId)).toBeCloseTo(1.0);
    });

    it('should ignore usage outside the target UTC day', async () => {
      const db = getTestDb();
      const userId = new ObjectId().toString();
      const today = getUtcDateKey();
      const yesterday = new Date(`${today}T00:00:00Z`);
      yesterday.setUTCDate(yesterday.getUTCDate() - 1);

      await db.collection('llm_usage').insertOne({
        user_id: userId,
        cost_usd: 5,
        timestamp: yesterday,
      });

      const total = await costMonitorService.reconcileUserDay(userId, today);

      expect(total).toBe(0);
    });
  });

  describe('reconcileAllUsersForDay', () => {
    it('should reconcile every user with usage on the day', async () => {
      const db = getTestDb();
      const userA = new ObjectId().toString();
      const userB = new ObjectId().toString();
      const today = getUtcDateKey();
      const now = new Date(`${today}T08:00:00Z`);

      await db.collection('llm_usage').insertMany([
        { user_id: userA, cost_usd: 1, timestamp: now },
        { user_id: userB, cost_usd: 3, timestamp: now },
      ]);

      const result = await costMonitorService.reconcileAllUsersForDay(today);

      expect(result.usersReconciled).toBe(2);
      expect(result.totalUsd).toBeCloseTo(4);
    });
  });

  describe('getUserUsageSummary', () => {
    it('should expose remaining cost for the user tier', async () => {
      const userId = new ObjectId().toString();
      await costMonitorService.incrementUserCost(userId, 0.5, { countVideo: true });

      const summary = await costMonitorService.getUserUsageSummary(userId, 'free');

      expect(summary.tier).toBe('free');
      expect(summary.today.effectiveUsd).toBeCloseTo(0.5);
      expect(summary.today.videoCount).toBe(1);
      expect(summary.limitUsd).toBe(2);
      expect(summary.remainingUsd).toBeCloseTo(1.5);
    });

    it('should return remainingUsd=null for unlimited tiers (no Infinity over the wire)', async () => {
      const userId = new ObjectId().toString();
      const summary = await costMonitorService.getUserUsageSummary(userId, 'team');
      expect(summary.remainingUsd).toBeNull();
    });
  });

  describe('reserveUserCost', () => {
    it('should reserve estimated cost and bump videoCount for a free user under the cap', async () => {
      const userId = new ObjectId().toString();
      const reservation = await costMonitorService.reserveUserCost(userId, 'free');

      expect(reservation).not.toBeNull();
      expect(reservation?.userId).toBe(userId);
      expect(reservation?.amountUsd).toBeGreaterThan(0);

      const summary = await costMonitorService.getUserUsageSummary(userId, 'free');
      expect(summary.today.effectiveUsd).toBeGreaterThan(0);
      expect(summary.today.videoCount).toBe(1);
    });

    it('should throw DailyLimitReachedError when the user is already past the cap', async () => {
      const userId = new ObjectId().toString();
      await costMonitorService.incrementUserCost(userId, 2.0, { countVideo: true });

      const { DailyLimitReachedError } = await import('../../utils/errors.js');
      await expect(costMonitorService.reserveUserCost(userId, 'free')).rejects.toBeInstanceOf(
        DailyLimitReachedError,
      );

      // Reservation was rolled back — totals stay at the pre-reservation value
      const summary = await costMonitorService.getUserUsageSummary(userId, 'free');
      expect(summary.today.effectiveUsd).toBeCloseTo(2.0);
      expect(summary.today.videoCount).toBe(1);
    });

    it('should serialize concurrent reservations on the same user/day (only one can overshoot)', async () => {
      // Regression: an earlier two-step (increment then read) implementation
      // had all 30 concurrent reads observe the post-all-increments state, so
      // every reservation got refunded. The fix uses findOneAndUpdate to get
      // each operation's own ordered post-state.
      const userId = new ObjectId().toString();
      // Free cap is $2; estimate per reservation is $0.15. Run 30 in parallel.
      const reservations = await Promise.allSettled(
        Array.from({ length: 30 }, () => costMonitorService.reserveUserCost(userId, 'free')),
      );

      const allowed = reservations.filter((r) => r.status === 'fulfilled').length;
      const blocked = reservations.filter((r) => r.status === 'rejected').length;

      expect(allowed + blocked).toBe(30);
      expect(allowed).toBeGreaterThan(0);
      expect(blocked).toBeGreaterThan(0);

      const summary = await costMonitorService.getUserUsageSummary(userId, 'free');
      // Cap + at most one full estimate above (the request that lands on the cap is allowed)
      expect(summary.today.effectiveUsd).toBeLessThanOrEqual(2 + 0.15 + 0.0001);
    });

    it('should return null and only bump videoCount for unlimited tiers', async () => {
      const userId = new ObjectId().toString();
      const reservation = await costMonitorService.reserveUserCost(userId, 'team');

      expect(reservation).toBeNull();
      const summary = await costMonitorService.getUserUsageSummary(userId, 'team');
      expect(summary.today.videoCount).toBe(1);
      expect(summary.today.effectiveUsd).toBe(0);
    });
  });

  describe('refundReservation', () => {
    it('should roll back both cost and videoCount', async () => {
      const userId = new ObjectId().toString();
      const reservation = await costMonitorService.reserveUserCost(userId, 'free');
      expect(reservation).not.toBeNull();

      await costMonitorService.refundReservation(reservation);

      const summary = await costMonitorService.getUserUsageSummary(userId, 'free');
      expect(summary.today.effectiveUsd).toBeCloseTo(0);
      expect(summary.today.videoCount).toBe(0);
    });

    it('should be a no-op for null reservations (unlimited tier path)', async () => {
      await expect(costMonitorService.refundReservation(null)).resolves.toBeUndefined();
    });
  });
});
