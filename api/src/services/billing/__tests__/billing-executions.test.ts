import { describe, it, expect, beforeAll, beforeEach, vi } from 'vitest';
import { ObjectId } from 'mongodb';
import type { FastifyBaseLogger } from 'fastify';
import { getTestDb, mockLogger } from '../../../test/setup.js';
import { createBillingIndexes } from '../../../plugins/mongodb.js';
import { BillingRepository } from '../../../repositories/billing.repository.js';
import { UserRepository } from '../../../repositories/user.repository.js';
import { AccountBlockedError, InsufficientTokensError, ValidationError } from '../../../utils/errors.js';
import { BillingService } from '../billing.service.js';
import { adminCtx, DAY_MS, insertUser, makeClock } from './billing-fixtures.js';
import type { ExecutionCost } from '../billing.service.js';
import type { TestClock } from './billing-fixtures.js';

const YOUTUBE_ID = 'dQw4w9WgXcQ';

describe('BillingService executions, debt and plan cycles (real Mongo replica set)', () => {
  let clock: TestClock;
  let service: BillingService;
  let userId: string;

  const db = () => getTestDb();
  const holdVideo = async (requestId: string, minutes: number) =>
    service.hold(requestId, await service.quote(userId, YOUTUBE_ID, minutes * 60));
  const chat = (overrides: Partial<ExecutionCost> = {}): ExecutionCost => ({
    userId, executionId: 'ex-1', kind: 'chat', requestId: 'req-chat-1', costUsd: 0.01, ...overrides,
  });
  const ledger = (filter: Record<string, unknown> = {}) => db().collection('ledgerEntries').find(filter).toArray();
  const ledgerCount = () => db().collection('ledgerEntries').countDocuments();
  const expectConsistent = async () => expect((await service.verifyAccount(userId)).drift).toEqual([]);
  /** 1,000 signup tokens, a 10-minute run settled at $0.60: 800 owed. */
  const runIntoDebt = async () => {
    await holdVideo('req-debt', 10);
    await service.settleActual('req-debt', 0.6);
    expect((await service.summary(userId)).debt).toBe(800);
  };

  beforeAll(async () => {
    await createBillingIndexes(db());
  });

  beforeEach(async () => {
    vi.clearAllMocks();
    clock = makeClock('2026-11-01T10:00:00Z');
    const repo = new BillingRepository(db());
    service = new BillingService(repo, new UserRepository(db()), mockLogger as unknown as FastifyBaseLogger, clock.now);
    userId = await insertUser(db());
  });

  describe('admitExecution', () => {
    it('should admit a new user on the signup grant and return the balance', async () => {
      expect(await service.admitExecution(userId)).toBe(1000);
    });

    it('should count what runs in flight hold', async () => {
      await holdVideo('req-1', 10);

      expect(await service.admitExecution(userId)).toBe(160);
    });

    it('should refuse a blocked account', async () => {
      await service.setCaps(userId, { blocked: true }, adminCtx());

      await expect(service.admitExecution(userId)).rejects.toBeInstanceOf(AccountBlockedError);
    });

    it('should refuse an empty balance with needed 1', async () => {
      await service.adjust(userId, { bucket: 'credit', tokens: -1000 }, adminCtx());

      const error = await service.admitExecution(userId).catch((err: unknown) => err);

      expect(error).toBeInstanceOf(InsufficientTokensError);
      expect(error).toMatchObject({ code: 'INSUFFICIENT_TOKENS', status: 402, needed: 1, available: 0 });
    });

    it('should refuse an account in debt with its negative balance', async () => {
      await runIntoDebt();

      await expect(service.admitExecution(userId)).rejects.toMatchObject({ needed: 1, available: -800 });
    });
  });

  describe('debitExecution', () => {
    it('should debit the real cost and mark the entries as the execution\'s', async () => {
      const outcome = await service.debitExecution(chat({ costUsd: 0.04 }));

      expect(outcome).toEqual({ executionId: 'ex-1', status: 'debited', tokens: 120 });
      const [entry] = await ledger({ type: 'debit' });
      expect(entry).toMatchObject({
        bucket: 'credit', unit: 'tokens', tokens: -120, costUsd: 0.04, executionKind: 'chat', executionId: 'ex-1',
        source: { kind: 'run', ref: 'req-chat-1' }, idempotencyKey: `debit:exec:ex-1:lot:${entry.lotId.toHexString()}`,
      });
      expect(entry).not.toHaveProperty('mirrorsHold');
      expect((await service.summary(userId)).balance).toBe(880);
      await expectConsistent();
    });

    it('should not charge a late callback for a turn the repair job already backfilled', async () => {
      expect(await service.backfillExecution({ userId, requestId: 'req-chat-1', kind: 'chat', costUsd: 0.04 })).toBe(true);

      const late = await service.debitExecution(chat({ costUsd: 0.04 }));

      expect(late).toEqual({ executionId: 'ex-1', status: 'already_debited', tokens: 120 });
      expect(await ledger({ type: 'debit' })).toHaveLength(1);
      expect((await service.summary(userId)).balance).toBe(880);
    });

    it.each(['library_chat', 'action'] as const)('should record a %s execution', async (kind) => {
      await service.debitExecution(chat({ kind }));

      expect((await ledger({ type: 'debit' })).map((e) => e.executionKind)).toEqual([kind]);
    });

    it('should take the plan first, then lots, then run into debt', async () => {
      await service.setPlan(userId, { planId: 'pro' }, adminCtx());
      await service.adjust(userId, { bucket: 'plan', tokens: -19_900 }, adminCtx());

      await service.debitExecution(chat({ costUsd: 0.5 }));

      const keys = (await ledger({ type: 'debit' })).map((e) => [e.bucket, e.tokens]);
      expect(keys).toEqual([['plan', -100], ['credit', -1000], ['debt', -400]]);
      expect(await service.summary(userId)).toMatchObject({ balance: -400, debt: 400 });
      await expectConsistent();
    });

    it('should debit once per executionId and report the same tokens on a retry', async () => {
      await service.debitExecution(chat({ costUsd: 0.5 }));
      const entries = await ledgerCount();

      const retry = await service.debitExecution(chat({ costUsd: 0.5 }));

      expect(retry).toEqual({ executionId: 'ex-1', status: 'already_debited', tokens: 1500 });
      expect(await ledgerCount()).toBe(entries);
    });

    it('should tell executions apart whose ids share a prefix or carry regex characters', async () => {
      await service.debitExecution(chat({ executionId: 'ex-10' }));

      const statuses: string[] = [];
      for (const executionId of ['ex-1', 'ex.10', 'e.*', 'e.*']) {
        statuses.push((await service.debitExecution(chat({ executionId }))).status);
      }

      expect(statuses).toEqual(['debited', 'debited', 'debited', 'already_debited']);
    });

    it('should write nothing for a free execution', async () => {
      expect(await service.debitExecution(chat({ costUsd: 0 }))).toEqual({ executionId: 'ex-1', status: 'free', tokens: 0 });
      expect(await ledgerCount()).toBe(0);
    });

    it.each([
      ['an empty executionId', { executionId: '' }],
      ['an empty requestId', { requestId: '' }],
      ['a negative cost', { costUsd: -1 }],
    ])('should reject %s', async (_label, overrides) => {
      await expect(service.debitExecution(chat(overrides))).rejects.toBeInstanceOf(ValidationError);
    });
  });

  describe('repaying debt (D7): every inflow pays what is owed first', () => {
    const accountHex = async () => (await db().collection('billingAccounts').findOne({ ownerId: new ObjectId(userId) }))?._id.toHexString();

    it.each<[string, () => Promise<string>, number]>([
      ['an admin lot', async () => {
        const audit = adminCtx();
        await service.grantLot(userId, { tokens: 5000 }, audit);
        return `admin:${audit.auditId}`;
      }, 4200],
      ['a positive credit adjustment', async () => {
        const audit = adminCtx();
        await service.adjust(userId, { bucket: 'credit', tokens: 5000 }, audit);
        return `admin:${audit.auditId}`;
      }, 4200],
      ['setPlan pro', async () => {
        const audit = adminCtx();
        await service.setPlan(userId, { planId: 'pro' }, audit);
        return `admin:${audit.auditId}:grant`;
      }, 19_200],
      ['a recurring grant\'s run', async () => {
        const { grantId } = await service.createRecurringGrant(userId, { tokens: 5000, every: 'month', active: true }, adminCtx());
        await service.applyRecurringGrant(new ObjectId(await accountHex()), new ObjectId(grantId));
        return `recurring:${grantId}:${clock.now().toISOString()}`;
      }, 4200],
    ])('should repay it from %s, keyed repay:<inflow key>', async (_label, inflow, balance) => {
      await runIntoDebt();

      const key = await inflow();

      const repays = await ledger({ type: 'repay' });
      expect(repays.find((e) => e.bucket === 'debt')).toMatchObject({ tokens: 800, idempotencyKey: `repay:${key}` });
      expect(repays.filter((e) => e.bucket !== 'debt').every((e) => e.idempotencyKey.startsWith(`repay:${key}:`))).toBe(true);
      expect(await service.summary(userId)).toMatchObject({ debt: 0, balance });
      await expectConsistent();
    });

    it('should repay it from a released hold', async () => {
      await service.grantLot(userId, { tokens: 840 }, adminCtx());
      await holdVideo('req-open', 10);
      await runIntoDebt();

      await service.release('req-open', 'failed');

      expect((await ledger({ type: 'repay', bucket: 'debt' })).map((e) => [e.idempotencyKey, e.tokens])).toEqual([['repay:release:req-open', 800]]);
      expect(await service.summary(userId)).toMatchObject({ debt: 0, balance: 40 });
      await expectConsistent();
    });

    it('should repay it from a new Pro cycle when the plan rolls', async () => {
      await service.setPlan(userId, { planId: 'pro' }, adminCtx());
      await service.adjust(userId, { bucket: 'plan', tokens: -20_000 }, adminCtx());
      await runIntoDebt();
      clock.advance(31 * DAY_MS);

      const rolled = await service.rollPeriod(userId);

      const id = await accountHex();
      expect((await ledger({ type: 'repay', bucket: 'debt' })).map((e) => e.idempotencyKey)).toEqual([`repay:roll:${id}:grant:2026-12-01T10:00:00.000Z`]);
      expect(rolled).toMatchObject({ debt: 0, balance: 19_200, planTokens: { granted: 20_000, used: 800, remaining: 19_200 } });
      await expectConsistent();
    });

    it('should repay it from a positive plan adjustment', async () => {
      await service.setPlan(userId, { planId: 'pro' }, adminCtx());
      await service.adjust(userId, { bucket: 'plan', tokens: -20_000 }, adminCtx());
      await runIntoDebt();
      const audit = adminCtx();

      await service.adjust(userId, { bucket: 'plan', tokens: 5000 }, audit);

      expect((await ledger({ type: 'repay' })).map((e) => [e.bucket, e.tokens, e.idempotencyKey])).toEqual([
        ['plan', -800, `repay:admin:${audit.auditId}:plan`],
        ['debt', 800, `repay:admin:${audit.auditId}`],
      ]);
      expect(await service.summary(userId)).toMatchObject({ debt: 0, balance: 4200 });
      await expectConsistent();
    });

    it('should leave debt in place when an admin takes tokens away or ends the plan', async () => {
      await runIntoDebt();

      await service.setPlan(userId, { planId: 'free' }, adminCtx());

      expect(await ledger({ type: 'repay' })).toEqual([]);
      expect((await service.summary(userId)).debt).toBe(800);
    });
  });

  describe('Pro plan cycles', () => {
    it('should grant 20,000 tokens per cycle with no rollover, and expire tokens released into an ended cycle', async () => {
      await service.setPlan(userId, { planId: 'pro' }, adminCtx());
      await holdVideo('req-1', 100);
      expect((await service.summary(userId)).planTokens?.remaining).toBe(17_540);

      clock.advance(31 * DAY_MS);
      const rolled = await service.rollPeriod(userId);
      expect(rolled.planTokens).toMatchObject({ granted: 20_000, used: 0, remaining: 20_000 });

      await service.release('req-1', 'failed');
      expect((await service.summary(userId)).planTokens?.remaining).toBe(20_000);
      await expectConsistent();
    });

    it('should grant nothing for idle months and one cycle for the month containing now', async () => {
      await service.setPlan(userId, { planId: 'pro' }, adminCtx());
      clock.advance(100 * DAY_MS);

      await service.rollPeriod(userId);

      const grants = await ledger({ type: 'grant', bucket: 'plan' });
      expect(grants.map((e) => e.tokens)).toEqual([20_000, 20_000]);
      await expectConsistent();
    });

    it('should revert to free when the term ends, keeping the credits', async () => {
      await service.setPlan(userId, { planId: 'pro', periodEnd: new Date('2026-11-20T00:00:00Z') }, adminCtx());
      clock.set('2026-11-21T00:00:00Z');

      const summary = await service.summary(userId);

      expect(summary).toMatchObject({ plan: { id: 'free', periodEnd: null }, planTokens: null, balance: 1000 });
      await service.rollPeriod(userId);
      await expectConsistent();
    });
  });
});
