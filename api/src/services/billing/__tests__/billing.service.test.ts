import { describe, it, expect, beforeAll, beforeEach, vi } from 'vitest';
import fc from 'fast-check';
import { ObjectId } from 'mongodb';
import type { FastifyBaseLogger } from 'fastify';
import { getTestDb, mockLogger } from '../../../test/setup.js';
import { createBillingIndexes } from '../../../plugins/mongodb.js';
import { BillingRepository } from '../../../repositories/billing.repository.js';
import { UserRepository } from '../../../repositories/user.repository.js';
import { BillingService } from '../billing.service.js';
import {
  AccountBlockedError,
  HoldFinalizedError,
  InsufficientMinutesError,
  VideoTooLongForPlanError,
} from '../billing-errors.js';
import { DAY_MS, HOUR_MS, insertUser, makeClock } from './billing-fixtures.js';
import type { TestClock } from './billing-fixtures.js';

describe('BillingService (runs, real Mongo replica set)', () => {
  let clock: TestClock;
  let repo: BillingRepository;
  let service: BillingService;
  let userId: string;

  const ctx = () => ({ actorId: new ObjectId().toHexString(), reason: 'test grant', auditId: new ObjectId().toHexString() });
  const grant = (minutes: number, expiresAt?: Date | null) => service.grantLot(userId, { minutes, expiresAt }, ctx());
  const holdVideo = async (requestId: string, minutes: number) =>
    service.hold(requestId, await service.quote(userId, 'dQw4w9WgXcQ', minutes * 60));
  const ledgerCount = () => getTestDb().collection('ledgerEntries').countDocuments();
  const expectConsistent = async (uid = userId) => expect((await service.verifyAccount(uid)).drift).toEqual([]);
  const lotRemaining = async (lotId: string) =>
    (await getTestDb().collection('creditLots').findOne({ _id: new ObjectId(lotId) }))?.minutesRemaining;

  beforeAll(async () => {
    await createBillingIndexes(getTestDb());
  });

  beforeEach(async () => {
    vi.clearAllMocks();
    clock = makeClock('2026-11-01T10:00:00Z');
    repo = new BillingRepository(getTestDb());
    service = new BillingService(repo, new UserRepository(getTestDb()), mockLogger as unknown as FastifyBaseLogger, clock.now);
    userId = await insertUser(getTestDb());
  });

  describe('quote', () => {
    it('should admit a short video on the free bucket for a new account', async () => {
      const quote = await service.quote(userId, 'dQw4w9WgXcQ', 600);

      expect(quote.minutes).toBe(10);
      expect(quote.decision).toEqual({ ok: true, split: { free: 10, plan: 0, credit: 0, lots: [] } });
    });

    it('should write nothing', async () => {
      await service.quote(userId, 'dQw4w9WgXcQ', 600);

      expect(await ledgerCount()).toBe(0);
      expect((await service.getAccountDetail(userId)).account.version).toBe(0);
    });
  });

  describe('hold', () => {
    it('should use the free video and report when the next one opens', async () => {
      await holdVideo('req-1', 10);

      const summary = await service.summary(userId);
      expect(summary.free).toEqual({ available: false, nextFreeAt: '2026-11-02T10:00:00.000Z', maxVideoMinutes: 30 });
      expect(summary.holds).toEqual([{ youtubeId: 'dQw4w9WgXcQ', minutes: 10 }]);
      await expectConsistent();
    });

    it('should return the open hold for a repeated requestId and write nothing new', async () => {
      const first = await holdVideo('req-1', 10);
      const entries = await ledgerCount();

      const second = await holdVideo('req-1', 10);

      expect(second).toEqual(first);
      expect(await ledgerCount()).toBe(entries);
    });

    it('should take credit minutes from the lot and record the split per lot', async () => {
      const { lotId } = await grant(100);
      await holdVideo('req-free', 5);

      const hold = await holdVideo('req-paid', 40);

      expect(hold.split).toEqual({ free: 0, plan: 0, credit: 40, lots: [{ lotId, minutes: 40 }] });
      expect(await lotRemaining(lotId)).toBe(60);
      await expectConsistent();
    });

    it('should refuse with needed/available when the account cannot pay, writing nothing', async () => {
      await grant(20);
      const entries = await ledgerCount();

      const error = await holdVideo('req-1', 45).catch((err: unknown) => err);

      expect(error).toBeInstanceOf(InsufficientMinutesError);
      expect(error).toMatchObject({ needed: 45, available: 20, status: 402 });
      expect(await ledgerCount()).toBe(entries);
    });

    it('should refuse a video longer than 180 minutes', async () => {
      await grant(1000);

      await expect(holdVideo('req-1', 181)).rejects.toBeInstanceOf(VideoTooLongForPlanError);
    });

    it('should refuse a blocked account', async () => {
      await service.setCaps(userId, { blocked: true }, ctx());

      await expect(holdVideo('req-1', 10)).rejects.toBeInstanceOf(AccountBlockedError);
    });

    it('should refuse a new hold for a run that was already released', async () => {
      await holdVideo('req-1', 10);
      await service.release('req-1', 'failed');

      await expect(holdVideo('req-1', 10)).rejects.toBeInstanceOf(HoldFinalizedError);
    });
  });

  describe('settle', () => {
    it('should post the hold as debits carrying the run cost and keep the minutes spent', async () => {
      const { lotId } = await grant(100);
      await holdVideo('req-free', 5);
      await holdVideo('req-1', 40);

      const outcome = await service.settle('req-1', { costUsd: 0.25 });

      expect(outcome).toEqual({ requestId: 'req-1', status: 'debited', minutes: 40 });
      const debits = await getTestDb().collection('ledgerEntries').find({ type: 'debit', 'source.ref': 'req-1' }).toArray();
      expect(debits.map((d) => [d.minutes, d.costUsd])).toEqual([[-40, 0.25]]);
      expect(await lotRemaining(lotId)).toBe(60);
      expect((await service.summary(userId)).holds).toEqual([{ youtubeId: 'dQw4w9WgXcQ', minutes: 5 }]);
      await expectConsistent();
    });

    it('should debit once when the run completes twice (translation\'s second completion)', async () => {
      await holdVideo('req-1', 10);
      await service.settle('req-1');
      const entries = await ledgerCount();

      const second = await service.settle('req-1');

      expect(second.status).toBe('already_debited');
      expect(await ledgerCount()).toBe(entries);
    });

    it('should log and write nothing when there is no hold', async () => {
      const outcome = await service.settle('req-unknown');

      expect(outcome.status).toBe('no_hold');
      expect(await ledgerCount()).toBe(0);
      expect(mockLogger.warn).toHaveBeenCalledWith(
        expect.objectContaining({ requestId: 'req-unknown', status: 'no_hold' }),
        expect.any(String),
      );
    });

    it('should report a run that completed after its hold was released', async () => {
      await holdVideo('req-1', 10);
      await service.release('req-1', 'failed');

      expect((await service.settle('req-1')).status).toBe('already_released');
    });
  });

  describe('release', () => {
    it('should give the free video back', async () => {
      await holdVideo('req-1', 10);

      await service.release('req-1', 'cached');

      expect((await service.summary(userId)).free.available).toBe(true);
      await expectConsistent();
    });

    it('should return credit minutes to the lot they came from, even a lot the hold emptied', async () => {
      const { lotId } = await grant(40);
      await holdVideo('req-free', 5);
      await holdVideo('req-1', 40);
      expect(await lotRemaining(lotId)).toBe(0);

      const outcome = await service.release('req-1', 'failed', { costUsd: 0.05 });

      expect(outcome.status).toBe('released');
      expect(await lotRemaining(lotId)).toBe(40);
      await expectConsistent();
    });

    it('should be a no-op after the debit', async () => {
      await holdVideo('req-1', 10);
      await service.settle('req-1');
      const entries = await ledgerCount();

      const outcome = await service.release('req-1', 'failed');

      expect(outcome.status).toBe('already_debited');
      expect(await ledgerCount()).toBe(entries);
    });

    it('should let minutes returned to an expired lot expire instead of refilling it', async () => {
      const { lotId } = await grant(50, new Date(clock.now().getTime() + 2 * HOUR_MS));
      await holdVideo('req-free', 5);
      await holdVideo('req-1', 40);
      clock.advance(3 * HOUR_MS);

      await service.release('req-1', 'failed');

      expect(await lotRemaining(lotId)).toBe(10);
      expect((await service.summary(userId)).credits.available).toBe(0);
      await expectConsistent();
    });
  });

  describe('plan cycle', () => {
    it('should reset Pro minutes each month with no rollover and expire minutes released into an ended cycle', async () => {
      await service.setPlan(userId, { planId: 'pro' }, ctx());
      await holdVideo('req-free', 5);
      await holdVideo('req-1', 100);
      expect((await service.summary(userId)).planMinutes.remaining).toBe(500);

      clock.advance(31 * DAY_MS);
      const rolled = await service.rollPeriod(userId);
      expect(rolled.planMinutes).toMatchObject({ granted: 600, used: 0, remaining: 600 });

      await service.release('req-1', 'failed');
      expect((await service.summary(userId)).planMinutes.remaining).toBe(600);
      await expectConsistent();
    });
  });

  describe('concurrency (P1.9)', () => {
    it('should give 8 parallel holds with minutes for 7 exactly one INSUFFICIENT_MINUTES and keep Σ ledger = projection', async () => {
      const first = await grant(100);
      const second = await grant(180);
      await holdVideo('req-free', 5);
      const quote = await service.quote(userId, 'dQw4w9WgXcQ', 40 * 60);

      const results = await Promise.allSettled(
        Array.from({ length: 8 }, (_, n) => service.hold(`req-parallel-${n}`, quote)),
      );

      const refused = results.flatMap((r) => (r.status === 'rejected' ? [r.reason] : []));
      expect(refused).toHaveLength(1);
      expect(refused[0]).toBeInstanceOf(InsufficientMinutesError);
      expect(refused[0]).toMatchObject({ code: 'INSUFFICIENT_MINUTES', needed: 40, available: 0 });
      expect([await lotRemaining(first.lotId), await lotRemaining(second.lotId)]).toEqual([0, 0]);
      expect((await service.summary(userId)).holds).toHaveLength(8);
      await expectConsistent();
    });

    it('should hold the same line across processes (one service each): transaction + version, no in-process queue', async () => {
      await grant(280);
      await holdVideo('req-free', 5);
      const quote = await service.quote(userId, 'dQw4w9WgXcQ', 40 * 60);
      const processes = Array.from({ length: 8 }, () =>
        new BillingService(repo, new UserRepository(getTestDb()), mockLogger as unknown as FastifyBaseLogger, clock.now));

      const results = await Promise.allSettled(processes.map((p, n) => p.hold(`req-process-${n}`, quote)));

      const refused = results.flatMap((r) => (r.status === 'rejected' ? [r.reason] : []));
      expect(refused).toHaveLength(1);
      expect(refused[0]).toBeInstanceOf(InsufficientMinutesError);
      await expectConsistent();
    });
  });

  describe('projections stay equal to Σ ledger', () => {
    type Op =
      | { kind: 'hold'; minutes: number }
      | { kind: 'settle' | 'release'; pick: number }
      | { kind: 'grant'; minutes: number; expiresInHours: number | null }
      | { kind: 'wait'; hours: number }
      | { kind: 'plan'; planId: 'free' | 'pro' };

    const opArb: fc.Arbitrary<Op> = fc.oneof(
      fc.record({ kind: fc.constant('hold' as const), minutes: fc.integer({ min: 1, max: 90 }) }),
      fc.record({ kind: fc.constantFrom('settle' as const, 'release' as const), pick: fc.nat() }),
      fc.record({
        kind: fc.constant('grant' as const),
        minutes: fc.integer({ min: 1, max: 120 }),
        expiresInHours: fc.option(fc.integer({ min: 1, max: 30 }), { nil: null }),
      }),
      fc.record({ kind: fc.constant('wait' as const), hours: fc.oneof(fc.integer({ min: 1, max: 30 }), fc.integer({ min: 24 * 25, max: 24 * 40 })) }),
      fc.record({ kind: fc.constant('plan' as const), planId: fc.constantFrom('free' as const, 'pro' as const) }),
    );

    it('should keep every bucket, lot and hold consistent through random hold/settle/release/grant/plan/expiry sequences', async () => {
      await fc.assert(
        fc.asyncProperty(fc.array(opArb, { minLength: 1, maxLength: 10 }), async (ops) => {
          userId = await insertUser(getTestDb());
          const open: string[] = [];
          for (const [n, op] of ops.entries()) {
            if (op.kind === 'hold') {
              const held = await holdVideo(`${userId}-${n}`, op.minutes).then(() => true, () => false);
              if (held) open.push(`${userId}-${n}`);
            } else if (op.kind === 'grant') {
              const expiresAt = op.expiresInHours ? new Date(clock.now().getTime() + op.expiresInHours * HOUR_MS) : null;
              await grant(op.minutes, expiresAt);
            } else if (op.kind === 'wait') {
              clock.advance(op.hours * HOUR_MS);
            } else if (op.kind === 'plan') {
              await service.setPlan(userId, { planId: op.planId }, ctx());
            } else if (open.length > 0) {
              const [requestId] = open.splice(op.pick % open.length, 1);
              if (op.kind === 'settle') await service.settle(requestId);
              else await service.release(requestId, 'failed');
            }
            await expectConsistent();
          }
        }),
        { numRuns: 25 },
      );
    });
  });
});
