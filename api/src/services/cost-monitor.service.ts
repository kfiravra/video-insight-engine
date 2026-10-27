import { Db, Collection } from 'mongodb';
import { FastifyBaseLogger } from 'fastify';
import { DailyLimitReachedError } from '../utils/errors.js';
import { config } from '../config.js';
import type { UserTier } from '@vie/types';
import {
  UserCostRepository,
  getUtcDateKey,
  getNextUtcMidnightIso,
} from '../repositories/user-cost.repository.js';

/**
 * `null` for `remainingUsd` indicates an unlimited tier. We avoid `Infinity`
 * because JSON serialization turns it into `null` anyway — surfacing `null`
 * at the type level keeps client code honest.
 */
export interface UserCostCheckResult {
  allowed: boolean;
  usedUsd: number;
  limitUsd: number;
  remainingUsd: number | null;
  resetAt: string;
  tier: UserTier;
}

export interface UserUsageSummary {
  tier: UserTier;
  today: {
    date: string;
    rawUsd: number;
    creditAdjustmentUsd: number;
    effectiveUsd: number;
    videoCount: number;
  };
  limitUsd: number;
  remainingUsd: number | null;
  resetAt: string;
}

/** Opaque token returned by `reserveUserCost` so the caller can refund on failure. */
export interface CostReservation {
  userId: string;
  dateKey: string;
  amountUsd: number;
}

/** Per-tier daily USD ceiling. `-1` (or `0`) = unlimited. */
function isUnlimited(limit: number): boolean {
  return limit < 0 || limit === 0;
}

/**
 * Optimistic per-video reservation. We intentionally over-estimate so that
 * concurrent starts can't blow past the cap while reconciliation lags behind.
 * `reconcileUserDay` rewrites `totalCostUsd` to the real value after
 * completion, so the over-estimate is transient.
 */
const VIDEO_RESERVATION_ESTIMATE_USD = 0.15;

export class CostMonitorService {
  // `llm_usage` holds the Python summarizer's schema (`user_id`, `cost_usd`,
  // `timestamp`). Node only reads it, to rebuild the per-user day aggregates;
  // there is no global spend aggregate here (the vie-admin evaluator owns the
  // `daily_global_cost` operator alarm).
  private readonly usageCollection: Collection;

  constructor(
    db: Db,
    private readonly logger: FastifyBaseLogger,
    private readonly userCostRepository: UserCostRepository = new UserCostRepository(db),
  ) {
    this.usageCollection = db.collection('llm_usage');
  }

  // ─── Per-user (new) ───

  getTierLimit(tier: UserTier): number {
    const limit = config.COST_LIMITS_PER_TIER[tier];
    return typeof limit === 'number' ? limit : config.COST_LIMITS_PER_TIER.free;
  }

  /** Effective USD spent today (after credit adjustments). */
  async getUserDailyCost(userId: string): Promise<number> {
    return this.userCostRepository.getEffectiveCost(userId, getUtcDateKey());
  }

  /** Read-only gate decision — does NOT reserve. Useful for UI display. */
  async checkUserCanProcess(userId: string, tier: UserTier): Promise<UserCostCheckResult> {
    const limitUsd = this.getTierLimit(tier);
    const usedUsd = await this.getUserDailyCost(userId);
    const resetAt = getNextUtcMidnightIso();

    if (isUnlimited(limitUsd)) {
      return { allowed: true, usedUsd, limitUsd, remainingUsd: null, resetAt, tier };
    }

    const remainingUsd = Math.max(limitUsd - usedUsd, 0);
    return {
      allowed: usedUsd < limitUsd,
      usedUsd,
      limitUsd,
      remainingUsd,
      resetAt,
      tier,
    };
  }

  /**
   * Atomic reservation. Increments `totalCostUsd` and `videoCount` first, then
   * verifies the post-increment effective spend is still within the cap. If
   * the increment pushed the user past the cap, the reservation is refunded
   * and `DailyLimitReachedError` is thrown. This pattern serializes concurrent
   * starts on the per-user/day document — two parallel requests cannot both
   * see the same "used = 0" snapshot and both succeed.
   *
   * `reconcileUserDay` later overwrites `totalCostUsd` with the real
   * `llm_usage` total, so the estimate is transient.
   */
  async reserveUserCost(userId: string, tier: UserTier): Promise<CostReservation | null> {
    const limitUsd = this.getTierLimit(tier);
    const dateKey = getUtcDateKey();
    const resetAt = getNextUtcMidnightIso();

    if (isUnlimited(limitUsd)) {
      // No reservation needed — still bump videoCount for accurate display.
      await this.userCostRepository.incrementDailyCost(userId, dateKey, 0, true);
      return null;
    }

    const estimate = VIDEO_RESERVATION_ESTIMATE_USD;
    // `incrementAndReturnEffective` returns THIS operation's post-state, not
    // the eventually-concurrent state — that's what makes the check below race-free.
    const after = await this.userCostRepository.incrementAndReturnEffective(
      userId,
      dateKey,
      estimate,
      true,
    );

    // Pre-reservation effective spend = post-reservation minus our estimate.
    // Block only if the user was already at/past the cap *before* this request —
    // a request that lands exactly on the cap is allowed.
    if (after.effectiveUsd - estimate >= limitUsd) {
      await this.userCostRepository.refundReservation(userId, dateKey, estimate);
      throw new DailyLimitReachedError(limitUsd, resetAt);
    }

    return { userId, dateKey, amountUsd: estimate };
  }

  /** Release a reservation when the downstream pipeline never starts (e.g. validation error). */
  async refundReservation(reservation: CostReservation | null): Promise<void> {
    if (!reservation) return;
    await this.userCostRepository.refundReservation(
      reservation.userId,
      reservation.dateKey,
      reservation.amountUsd,
    );
  }

  /** Low-level primitive: atomic add of incremental cost. Prefer `reserveUserCost` for gating. */
  async incrementUserCost(
    userId: string,
    costUsd: number,
    options: { countVideo?: boolean } = {},
  ): Promise<void> {
    await this.userCostRepository.incrementDailyCost(
      userId,
      getUtcDateKey(),
      costUsd,
      options.countVideo ?? false,
    );
  }

  /**
   * Rebuild today's `totalCostUsd` from `llm_usage` (the truth source) for one
   * user. `videoCount` is preserved — it's owned by the reservation/refund
   * code path (reserve = +1, refund = -1), so reconciliation should not touch
   * it. Eventual consistency note: rows that land in `llm_usage` between the
   * aggregate and the write are picked up by the next reconcile call.
   */
  async reconcileUserDay(userId: string, dateKey: string = getUtcDateKey()): Promise<number> {
    const dayStart = new Date(`${dateKey}T00:00:00.000Z`);
    const dayEnd = new Date(dayStart);
    dayEnd.setUTCDate(dayEnd.getUTCDate() + 1);

    const result = await this.usageCollection
      .aggregate<{ total: number }>([
        {
          $match: {
            user_id: userId,
            timestamp: { $gte: dayStart, $lt: dayEnd },
          },
        },
        { $group: { _id: null, total: { $sum: '$cost_usd' } } },
      ])
      .toArray();

    const totalCostUsd = result[0]?.total ?? 0;
    const existing = await this.userCostRepository.findByUserAndDate(userId, dateKey);
    const videoCount = existing?.videoCount ?? 0;
    await this.userCostRepository.setDailyCost(userId, dateKey, totalCostUsd, videoCount);
    return totalCostUsd;
  }

  /** Reconcile ALL users for a given UTC day from `llm_usage`. Served by `POST /internal/reconcile-costs`; nothing schedules it. */
  async reconcileAllUsersForDay(dateKey: string = getUtcDateKey()): Promise<{ usersReconciled: number; totalUsd: number }> {
    const dayStart = new Date(`${dateKey}T00:00:00.000Z`);
    const dayEnd = new Date(dayStart);
    dayEnd.setUTCDate(dayEnd.getUTCDate() + 1);

    const rows = await this.usageCollection
      .aggregate<{ _id: string; total: number; calls: number }>([
        {
          $match: {
            user_id: { $ne: null },
            timestamp: { $gte: dayStart, $lt: dayEnd },
          },
        },
        {
          $group: {
            _id: '$user_id',
            total: { $sum: '$cost_usd' },
            calls: { $sum: 1 },
          },
        },
      ])
      .toArray();

    let totalUsd = 0;
    for (const row of rows) {
      if (!row._id) continue;
      const existing = await this.userCostRepository.findByUserAndDate(row._id, dateKey);
      await this.userCostRepository.setDailyCost(
        row._id,
        dateKey,
        row.total,
        existing?.videoCount ?? 0,
      );
      totalUsd += row.total;
    }

    this.logger.info(
      { usersReconciled: rows.length, totalUsd, dateKey },
      'Reconciled user cost aggregates from llm_usage',
    );

    return { usersReconciled: rows.length, totalUsd };
  }

  async getUserUsageSummary(userId: string, tier: UserTier): Promise<UserUsageSummary> {
    const dateKey = getUtcDateKey();
    const doc = await this.userCostRepository.findByUserAndDate(userId, dateKey);
    const rawUsd = doc?.totalCostUsd ?? 0;
    const creditAdjustmentUsd = doc?.creditAdjustmentUsd ?? 0;
    const effectiveUsd = rawUsd + creditAdjustmentUsd;
    const limitUsd = this.getTierLimit(tier);
    const remainingUsd = isUnlimited(limitUsd)
      ? null
      : Math.max(limitUsd - effectiveUsd, 0);

    return {
      tier,
      today: {
        date: dateKey,
        rawUsd,
        creditAdjustmentUsd,
        effectiveUsd,
        videoCount: doc?.videoCount ?? 0,
      },
      limitUsd,
      remainingUsd,
      resetAt: getNextUtcMidnightIso(),
    };
  }
}
