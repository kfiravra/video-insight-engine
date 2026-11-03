import { describe, it, expect, beforeAll, beforeEach } from 'vitest';
import fc from 'fast-check';
import type { FastifyBaseLogger } from 'fastify';
import { getTestDb, mockLogger } from '../../../test/setup.js';
import { createBillingIndexes } from '../../../plugins/mongodb.js';
import { BillingRepository } from '../../../repositories/billing.repository.js';
import { UserRepository } from '../../../repositories/user.repository.js';
import { InsufficientTokensError } from '../../../utils/errors.js';
import { BillingService } from '../billing.service.js';
import { estimateTokens } from '../tokens.js';
import { adminCtx, HOUR_MS, insertUser, makeClock } from './billing-fixtures.js';
import type { TestClock } from './billing-fixtures.js';

const YOUTUBE_ID = 'dQw4w9WgXcQ';
const logger = mockLogger as unknown as FastifyBaseLogger;
const costArb = fc.integer({ min: 0, max: 2_000_000 }).map((micros) => micros / 1e6);

describe('BillingService under concurrency and random sequences (real Mongo replica set)', () => {
  let clock: TestClock;
  let repo: BillingRepository;
  let service: BillingService;

  const db = () => getTestDb();
  const newService = () => new BillingService(repo, new UserRepository(db()), logger, clock.now);
  const holdFor = async (svc: BillingService, userId: string, requestId: string, durationSec: number) =>
    svc.hold(requestId, await svc.quote(userId, YOUTUBE_ID, durationSec));
  const refusals = (results: PromiseSettledResult<unknown>[]): unknown[] =>
    results.flatMap((r) => (r.status === 'rejected' ? [r.reason] : []));

  /** Σ ledger = projection, debt ≥ 0, debt > 0 ⇔ balance < 0. */
  const expectInvariants = async (userId: string) => {
    expect((await service.verifyAccount(userId)).drift).toEqual([]);
    const { debt, balance } = await service.summary(userId);
    expect(debt).toBeGreaterThanOrEqual(0);
    expect(debt > 0).toBe(balance < 0);
  };

  beforeAll(async () => {
    await createBillingIndexes(db());
  });

  beforeEach(() => {
    clock = makeClock('2026-11-01T10:00:00Z');
    repo = new BillingRepository(db());
    service = newService();
  });

  describe('parallel holds against one balance', () => {
    it.each([
      ['in one process (queued per account)', () => Array.from({ length: 8 }, () => service)],
      ['across processes (transactions + version only)', () => Array.from({ length: 8 }, newService)],
    ])('should admit exactly the affordable ones %s', async (_label, services) => {
      const userId = await insertUser(db());
      await service.grantLot(userId, { tokens: 4000 }, adminCtx());

      const results = await Promise.allSettled(services().map((svc, n) => holdFor(svc, userId, `req-${n}`, 600)));

      const refused = refusals(results);
      expect(refused).toHaveLength(3);
      for (const err of refused) {
        expect(err).toBeInstanceOf(InsufficientTokensError);
        expect(err).toMatchObject({ needed: 840, available: 800 });
      }
      expect(await service.summary(userId)).toMatchObject({ balance: 800 });
      expect((await service.summary(userId)).holds).toHaveLength(5);
      await expectInvariants(userId);
    });

    it('should never hold more than the balance whatever the mix of lengths', async () => {
      await fc.assert(
        fc.asyncProperty(
          fc.array(fc.integer({ min: 0, max: 60 * 60 }), { minLength: 2, maxLength: 8 }),
          fc.integer({ min: 0, max: 5000 }),
          async (durations, extra) => {
            const userId = await insertUser(db());
            if (extra > 0) await service.grantLot(userId, { tokens: extra }, adminCtx());
            const before = (await service.summary(userId)).balance;

            const results = await Promise.allSettled(durations.map((sec, n) => holdFor(service, userId, `${userId}-${n}`, sec)));

            const after = (await service.summary(userId)).balance;
            const held = durations.filter((_, n) => results[n].status === 'fulfilled').reduce((sum, sec) => sum + estimateTokens(sec), 0);
            expect(held + after).toBe(before);
            durations.forEach((sec, n) => {
              const result = results[n];
              if (result.status === 'fulfilled') return;
              expect(result.reason).toBeInstanceOf(InsufficientTokensError);
              expect(result.reason).toMatchObject({ needed: estimateTokens(sec) });
              expect(estimateTokens(sec)).toBeGreaterThan(after);
            });
            await expectInvariants(userId);
          },
        ),
        { numRuns: 20 },
      );
    });

    it('should settle and release in parallel without drift', async () => {
      const userId = await insertUser(db());
      await service.grantLot(userId, { tokens: 10_000 }, adminCtx());
      for (let n = 0; n < 6; n++) await holdFor(service, userId, `req-${n}`, 600);

      await Promise.all([
        service.settleActual('req-0', 0.1), service.settleActual('req-1', 2), newService().settleActual('req-2', 0.3),
        service.release('req-3', 'failed'), newService().release('req-4', 'cached'), service.settleActual('req-5', 1.5),
        service.settleActual('req-0', 0.1),
      ]);

      expect((await service.summary(userId)).holds).toEqual([]);
      await expectInvariants(userId);
    });
  });

  describe('settleActual on random accounts', () => {
    it('should keep the projection exact after every settle and owe only with nothing spendable', async () => {
      const scenario = fc.record({
        lots: fc.array(
          fc.record({ tokens: fc.integer({ min: 1, max: 3000 }), expiresInHours: fc.option(fc.integer({ min: 1, max: 6 }), { nil: null }) }),
          { maxLength: 3 },
        ),
        pro: fc.boolean(),
        runs: fc.array(fc.record({ minutes: fc.integer({ min: 0, max: 120 }), costUsd: costArb }), { minLength: 1, maxLength: 3 }),
        waitHours: fc.integer({ min: 0, max: 8 }),
      });
      await fc.assert(
        fc.asyncProperty(scenario, async ({ lots, pro, runs, waitHours }) => {
          const userId = await insertUser(db());
          if (pro) await service.setPlan(userId, { planId: 'pro' }, adminCtx());
          for (const lot of lots) {
            const expiresAt = lot.expiresInHours === null ? null : new Date(clock.now().getTime() + lot.expiresInHours * HOUR_MS);
            await service.grantLot(userId, { tokens: lot.tokens, expiresAt }, adminCtx());
          }
          const held: Array<{ requestId: string; costUsd: number }> = [];
          for (const [n, run] of runs.entries()) {
            const ok = await holdFor(service, userId, `${userId}-${n}`, run.minutes * 60).then(() => true, () => false);
            if (ok) held.push({ requestId: `${userId}-${n}`, costUsd: run.costUsd });
          }
          clock.advance(waitHours * HOUR_MS);

          for (const { requestId, costUsd } of held.reverse()) {
            expect((await service.settleActual(requestId, costUsd)).status).toBe('debited');
            await expectInvariants(userId);
            const summary = await service.summary(userId);
            if (summary.debt > 0) expect((summary.planTokens?.remaining ?? 0) + summary.credits.available).toBe(0);
          }
        }),
        { numRuns: 25 },
      );
    });
  });

  describe('random sequences (D7)', () => {
    type Op =
      | { kind: 'hold'; minutes: number }
      | { kind: 'settle'; pick: number; costUsd: number }
      | { kind: 'release'; pick: number }
      | { kind: 'grant'; tokens: number; expiresInHours: number | null }
      | { kind: 'adjust'; tokens: number }
      | { kind: 'chat'; costUsd: number }
      | { kind: 'plan'; planId: 'free' | 'pro' }
      | { kind: 'wait'; hours: number };

    const opArb: fc.Arbitrary<Op> = fc.oneof(
      fc.record({ kind: fc.constant('hold' as const), minutes: fc.integer({ min: 0, max: 120 }) }),
      fc.record({ kind: fc.constant('settle' as const), pick: fc.nat(), costUsd: costArb }),
      fc.record({ kind: fc.constant('release' as const), pick: fc.nat() }),
      fc.record({
        kind: fc.constant('grant' as const),
        tokens: fc.integer({ min: 1, max: 3000 }),
        expiresInHours: fc.option(fc.integer({ min: 1, max: 30 }), { nil: null }),
      }),
      fc.record({ kind: fc.constant('adjust' as const), tokens: fc.integer({ min: 1, max: 2000 }) }),
      fc.record({ kind: fc.constant('chat' as const), costUsd: costArb }),
      fc.record({ kind: fc.constant('plan' as const), planId: fc.constantFrom('free' as const, 'pro' as const) }),
      fc.record({ kind: fc.constant('wait' as const), hours: fc.oneof(fc.integer({ min: 1, max: 30 }), fc.integer({ min: 24 * 25, max: 24 * 40 })) }),
    );

    it('should keep every bucket, lot and hold equal to Σ ledger, and owe only below zero', async () => {
      await fc.assert(
        fc.asyncProperty(fc.array(opArb, { minLength: 1, maxLength: 12 }), async (ops) => {
          const userId = await insertUser(db());
          const open: string[] = [];
          for (const [n, op] of ops.entries()) {
            const requestId = `${userId}-${n}`;
            if (op.kind === 'hold') {
              if (await holdFor(service, userId, requestId, op.minutes * 60).then(() => true, () => false)) open.push(requestId);
            } else if (op.kind === 'settle' || op.kind === 'release') {
              if (open.length === 0) continue;
              const [picked] = open.splice(op.pick % open.length, 1);
              if (op.kind === 'settle') await service.settleActual(picked, op.costUsd);
              else await service.release(picked, 'failed');
            } else if (op.kind === 'grant') {
              const expiresAt = op.expiresInHours === null ? null : new Date(clock.now().getTime() + op.expiresInHours * HOUR_MS);
              await service.grantLot(userId, { tokens: op.tokens, expiresAt }, adminCtx());
            } else if (op.kind === 'adjust') {
              await service.adjust(userId, { bucket: 'credit', tokens: op.tokens }, adminCtx());
            } else if (op.kind === 'chat') {
              await service.debitExecution({ userId, executionId: requestId, kind: 'chat', requestId, costUsd: op.costUsd });
            } else if (op.kind === 'plan') {
              await service.setPlan(userId, { planId: op.planId }, adminCtx());
            } else {
              clock.advance(op.hours * HOUR_MS);
            }
            await expectInvariants(userId);
          }
        }),
        { numRuns: 30 },
      );
    });
  });
});
