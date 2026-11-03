import { describe, it, expect, beforeAll, beforeEach, vi } from 'vitest';
import { ObjectId } from 'mongodb';
import type { FastifyBaseLogger } from 'fastify';
import { getTestDb, mockLogger } from '../../../test/setup.js';
import { createBillingIndexes } from '../../../plugins/mongodb.js';
import { BillingRepository } from '../../../repositories/billing.repository.js';
import { UserRepository } from '../../../repositories/user.repository.js';
import { BillingService } from '../billing.service.js';
import {
  AccountBlockedError,
  HoldConflictError,
  HoldFinalizedError,
  InsufficientTokensError,
  TooManyRunsError,
  ValidationError,
  VideoTooLongError,
} from '../../../utils/errors.js';
import { adminCtx, HOUR_MS, insertUser, makeClock } from './billing-fixtures.js';
import type { ReleaseReason } from '../billing.service.js';
import type { TestClock } from './billing-fixtures.js';

const YOUTUBE_ID = 'dQw4w9WgXcQ';
const logger = mockLogger as unknown as FastifyBaseLogger;

describe('BillingService (runs, real Mongo replica set)', () => {
  let clock: TestClock;
  let repo: BillingRepository;
  let service: BillingService;
  let userId: string;

  const db = () => getTestDb();
  const newService = () => new BillingService(repo, new UserRepository(db()), logger, clock.now);
  const grant = (tokens: number, expiresAt?: Date | null) => service.grantLot(userId, { tokens, expiresAt }, adminCtx());
  const holdVideo = async (requestId: string, minutes: number, uid = userId, youtubeId = YOUTUBE_ID) =>
    service.hold(requestId, await service.quote(uid, youtubeId, minutes * 60));
  const ledger = (filter: Record<string, unknown> = {}) => db().collection('ledgerEntries').find(filter).toArray();
  const ledgerCount = () => db().collection('ledgerEntries').countDocuments();
  const expectConsistent = async (uid = userId) => expect((await service.verifyAccount(uid)).drift).toEqual([]);
  const lotRemaining = async (lotId: string) =>
    (await db().collection('creditLots').findOne({ _id: new ObjectId(lotId) }))?.tokensRemaining;
  const signupLotId = async (uid = userId) =>
    (await db().collection('ledgerEntries').findOne({ idempotencyKey: `grant:signup:${uid}` }))?.lotId?.toHexString() as string;

  beforeAll(async () => {
    await createBillingIndexes(db());
  });

  beforeEach(async () => {
    vi.clearAllMocks();
    clock = makeClock('2026-11-01T10:00:00Z');
    repo = new BillingRepository(db());
    service = newService();
    userId = await insertUser(db());
  });

  describe('signup grant', () => {
    it('should open a new account on its first read with one never-expiring 1,000-token lot', async () => {
      const summary = await service.summary(userId);

      expect([summary.balance, summary.signupGrantTokens, summary.credits.lots]).toEqual([1000, 1000, [{ tokens: 1000, expiresAt: null }]]);
      const entries = await ledger();
      expect(entries).toEqual([expect.objectContaining({
        type: 'grant', bucket: 'credit', unit: 'tokens', tokens: 1000,
        idempotencyKey: `grant:signup:${userId}`, source: { kind: 'system', ref: 'signup' },
      })]);
      const lots = await db().collection('creditLots').find({}).toArray();
      expect(lots).toEqual([expect.objectContaining({ _id: entries[0].lotId, tokensGranted: 1000, tokensRemaining: 1000, expiresAt: null })]);
      const account = await db().collection('billingAccounts').findOne({ ownerId: new ObjectId(userId) });
      expect(account?.signupGrantAt).toEqual(clock.now());
      await expectConsistent();
    });

    it('should open it on a first write too, before the write applies', async () => {
      await service.debitExecution({ userId, executionId: 'ex-1', kind: 'chat', requestId: 'req-1', costUsd: 0.01 });

      expect((await service.summary(userId)).balance).toBe(970);
      expect(await ledger({ type: 'grant' })).toHaveLength(1);
    });

    it('should be idempotent through ensureAccount', async () => {
      await service.ensureAccount(userId);
      await service.ensureAccount(userId);
      await service.summary(userId);

      expect(await db().collection('billingAccounts').countDocuments()).toBe(1);
      expect([await ledgerCount(), await db().collection('creditLots').countDocuments()]).toEqual([1, 1]);
    });

    it('should open one account with one grant when ten first requests race (in-process and across processes)', async () => {
      const others = Array.from({ length: 5 }, newService);

      const summaries = await Promise.all([
        ...Array.from({ length: 5 }, () => service.summary(userId)),
        ...others.map((other) => other.summary(userId)),
      ]);

      expect(summaries.map((s) => s.balance)).toEqual(Array(10).fill(1000));
      expect(await db().collection('billingAccounts').countDocuments({ ownerId: new ObjectId(userId) })).toBe(1);
      expect(await ledger({ idempotencyKey: `grant:signup:${userId}` })).toHaveLength(1);
      expect(await db().collection('creditLots').countDocuments()).toBe(1);
      await expectConsistent();
    });

    it('should reject an owner id that is not an ObjectId', async () => {
      await expect(service.summary('not-an-id')).rejects.toBeInstanceOf(ValidationError);
    });
  });

  describe('quote', () => {
    it('should estimate a 10-minute video at 840 tokens held from the signup lot', async () => {
      const quote = await service.quote(userId, YOUTUBE_ID, 600);

      expect(quote.tokens).toBe(840);
      expect(quote.decision).toEqual({ ok: true, split: { plan: 0, credit: 840, lots: [{ lotId: await signupLotId(), tokens: 840 }] } });
    });

    it('should write nothing beyond the account it opens', async () => {
      await service.quote(userId, YOUTUBE_ID, 600);
      const entries = await ledgerCount();

      await service.quote(userId, YOUTUBE_ID, 600);

      expect(await ledgerCount()).toBe(entries);
      expect((await service.summary(userId)).holds).toEqual([]);
    });

    it('should refuse a 20-minute video on the signup grant alone (1,020 > 1,000)', async () => {
      const quote = await service.quote(userId, YOUTUBE_ID, 20 * 60);

      expect(quote.decision).toEqual({ ok: false, reason: 'insufficient', needed: 1020, available: 1000 });
    });
  });

  describe('quoteMany', () => {
    it('should decide items in order on the account as the earlier ones leave it, holding nothing', async () => {
      await grant(1000);
      const entries = await ledgerCount();

      const quote = await service.quoteMany(userId, [600, 600, 300, 0]);

      expect(quote.neededTokens).toBe(840 + 840 + 750 + 660);
      expect(quote.availableTokens).toBe(2000);
      expect(quote.decisions.map((d) => (d.ok ? 'ok' : d))).toEqual([
        'ok',
        'ok',
        { ok: false, reason: 'insufficient', needed: 750, available: 320 },
        { ok: false, reason: 'insufficient', needed: 660, available: 320 },
      ]);
      expect([await ledgerCount(), (await service.summary(userId)).holds]).toEqual([entries, []]);
    });

    it('should count earlier items against maxConcurrent', async () => {
      await service.setCaps(userId, { maxConcurrent: 1 }, adminCtx());

      const quote = await service.quoteMany(userId, [60, 60]);

      expect(quote.decisions.map((d) => (d.ok ? 'ok' : d.reason))).toEqual(['ok', 'too_many_runs']);
    });
  });

  describe('hold', () => {
    it('should take the estimate from the signup lot and show it as in flight', async () => {
      const hold = await holdVideo('req-1', 10);

      expect(hold).toMatchObject({ requestId: 'req-1', userId, youtubeId: YOUTUBE_ID, durationSec: 600, tokens: 840 });
      const summary = await service.summary(userId);
      expect([summary.balance, summary.holds]).toEqual([160, [{ youtubeId: YOUTUBE_ID, tokens: 840 }]]);
      expect(await lotRemaining(await signupLotId())).toBe(160);
      await expectConsistent();
    });

    it('should hold from the Pro cycle before any lot', async () => {
      await service.setPlan(userId, { planId: 'pro' }, adminCtx());

      const hold = await holdVideo('req-1', 100);

      expect(hold.split).toEqual({ plan: 2460, credit: 0, lots: [] });
      expect((await service.summary(userId)).planTokens).toMatchObject({ used: 2460, remaining: 17_540 });
      await expectConsistent();
    });

    it('should return the open hold for a repeated requestId and write nothing new', async () => {
      const first = await holdVideo('req-1', 10);
      const entries = await ledgerCount();

      const second = await holdVideo('req-1', 10);

      expect(second).toEqual(first);
      expect(await ledgerCount()).toBe(entries);
    });

    it('should refuse with needed/available when the balance does not cover the estimate, writing nothing', async () => {
      await service.ensureAccount(userId);
      const entries = await ledgerCount();

      const error = await holdVideo('req-1', 20).catch((err: unknown) => err);

      expect(error).toBeInstanceOf(InsufficientTokensError);
      expect(error).toMatchObject({ code: 'INSUFFICIENT_TOKENS', status: 402, needed: 1020, available: 1000 });
      expect(await ledgerCount()).toBe(entries);
    });

    it('should refuse while the account owes tokens, with the negative balance as available', async () => {
      await holdVideo('req-1', 10);
      await service.settleActual('req-1', 0.6);

      await expect(holdVideo('req-2', 1)).rejects.toMatchObject({ code: 'INSUFFICIENT_TOKENS', needed: 678, available: -800 });
    });

    it('should refuse a video longer than 180 minutes, or than the account cap', async () => {
      await grant(10_000);
      await service.setPlan(userId, { planId: 'pro' }, adminCtx());

      await expect(holdVideo('req-1', 181)).rejects.toMatchObject({ code: 'VIDEO_TOO_LONG', status: 422, maxMinutes: 180 });
      await service.setCaps(userId, { maxVideoMinutes: 30 }, adminCtx());
      await expect(holdVideo('req-2', 31)).rejects.toBeInstanceOf(VideoTooLongError);
      await expect(holdVideo('req-3', 31)).rejects.toMatchObject({ maxMinutes: 30 });
    });

    it('should refuse a blocked account', async () => {
      await service.setCaps(userId, { blocked: true }, adminCtx());

      await expect(holdVideo('req-1', 1)).rejects.toBeInstanceOf(AccountBlockedError);
    });

    it('should refuse a run beyond maxConcurrent', async () => {
      await service.setCaps(userId, { maxConcurrent: 1 }, adminCtx());
      await holdVideo('req-1', 1);

      await expect(holdVideo('req-2', 1)).rejects.toBeInstanceOf(TooManyRunsError);
    });

    it('should refuse a run id already holding for another video (W3)', async () => {
      await holdVideo('req-1', 1);
      const other = await service.quote(userId, 'aaaaaaaaaaa', 60);

      await expect(service.hold('req-1', other)).rejects.toBeInstanceOf(HoldConflictError);
    });

    it('should refuse a run id already holding for another user (W3)', async () => {
      await holdVideo('req-1', 1);
      const otherUser = await insertUser(db());
      const quote = await service.quote(otherUser, YOUTUBE_ID, 60);

      await expect(service.hold('req-1', quote)).rejects.toBeInstanceOf(HoldConflictError);
      expect((await service.summary(otherUser)).holds).toEqual([]);
    });

    it.each([
      ['released', (s: BillingService) => s.release('req-1', 'failed')],
      ['settled', (s: BillingService) => s.settleActual('req-1', 0.1)],
    ])('should refuse a new hold for a run that was already %s', async (_label, finish) => {
      await holdVideo('req-1', 1);
      await finish(service);

      await expect(holdVideo('req-1', 1)).rejects.toBeInstanceOf(HoldFinalizedError);
    });
  });

  describe('settleActual', () => {
    it('should debit the real cost out of the hold and give the unused estimate back', async () => {
      await service.ensureAccount(userId);
      const lot = await signupLotId();
      await holdVideo('req-1', 10);

      const outcome = await service.settleActual('req-1', 0.2);

      expect(outcome).toEqual({ requestId: 'req-1', status: 'debited', tokens: 600, released: 240, debt: 0 });
      const debits = await ledger({ type: 'debit', 'source.ref': 'req-1' });
      expect(debits.map((d) => [d.tokens, d.costUsd, d.mirrorsHold, d.idempotencyKey])).toEqual([[-600, 0.2, true, `debit:req-1:lot:${lot}`]]);
      expect(await lotRemaining(lot)).toBe(400);
      expect((await service.summary(userId)).holds).toEqual([]);
      await expectConsistent();
    });

    it('should take a cost beyond the hold from what is left, then run into debt', async () => {
      await holdVideo('req-1', 10);

      const outcome = await service.settleActual('req-1', 0.6);

      expect(outcome).toEqual({ requestId: 'req-1', status: 'debited', tokens: 1800, released: 0, debt: 800 });
      expect((await ledger({ type: 'debit' })).map((d) => d.idempotencyKey)).toEqual([
        `debit:req-1:lot:${await signupLotId()}`,
        `debit:req-1:extra:lot:${await signupLotId()}`,
        'debit:req-1:extra:debt',
      ]);
      expect(await service.summary(userId)).toMatchObject({ balance: -800, debt: 800 });
      await expectConsistent();
    });

    it('should debit once when the run is settled twice, writing nothing the second time', async () => {
      await holdVideo('req-1', 10);
      await service.settleActual('req-1', 0.2);
      const entries = await ledgerCount();

      const second = await service.settleActual('req-1', 0.2);

      expect(second).toEqual({ requestId: 'req-1', status: 'already_debited' });
      expect(await ledgerCount()).toBe(entries);
    });

    it('should let a lot that expired during the run pay its part and expire the rest', async () => {
      const { lotId } = await grant(1000, new Date(clock.now().getTime() + 2 * HOUR_MS));
      await holdVideo('req-1', 10);
      clock.advance(3 * HOUR_MS);

      await service.settleActual('req-1', 0.1);

      const rows = await ledger({ 'source.ref': 'req-1', type: { $ne: 'hold' } });
      expect(rows.map((e) => [e.type, e.tokens, e.idempotencyKey])).toEqual([
        ['debit', -300, `debit:req-1:lot:${lotId}`],
        ['release', 540, `release:req-1:lot:${lotId}`],
        ['expire', -540, `release-expire:req-1:lot:${lotId}`],
      ]);
      expect(await lotRemaining(lotId)).toBe(160);
      expect((await service.summary(userId)).balance).toBe(1000);
      await expectConsistent();
    });

    it('should log and write nothing when there is no hold', async () => {
      const outcome = await service.settleActual('req-unknown', 0.1);

      expect(outcome).toEqual({ requestId: 'req-unknown', status: 'no_hold' });
      expect(await ledgerCount()).toBe(0);
      expect(mockLogger.warn).toHaveBeenCalledWith(expect.objectContaining({ requestId: 'req-unknown', status: 'no_hold' }), expect.any(String));
    });

    it('should report a run that completed after its hold was released', async () => {
      await holdVideo('req-1', 1);
      await service.release('req-1', 'failed');

      expect((await service.settleActual('req-1', 0.1)).status).toBe('already_released');
    });

    it.each([
      ['an empty run id', '', 0.1],
      ['a negative cost', 'req-1', -0.1],
      ['a NaN cost', 'req-1', Number.NaN],
    ])('should reject %s', async (_label, requestId, costUsd) => {
      await expect(service.settleActual(requestId, costUsd)).rejects.toBeInstanceOf(ValidationError);
    });
  });

  describe('release', () => {
    it.each(['failed', 'cached', 'attached', 'submit_error', 'stale'] as ReleaseReason[])(
      'should give everything back on %s, once',
      async (reason) => {
        await holdVideo('req-1', 10);

        const first = await service.release('req-1', reason);
        const entries = await ledgerCount();
        const second = await service.release('req-1', reason);

        expect([first, second]).toEqual([
          { requestId: 'req-1', status: 'released', tokens: 840 },
          { requestId: 'req-1', status: 'already_released' },
        ]);
        expect((await ledger({ type: 'release' })).map((e) => e.reason)).toEqual([reason]);
        expect([await ledgerCount(), (await service.summary(userId)).balance]).toEqual([entries, 1000]);
        await expectConsistent();
      },
    );

    it('should return tokens to the lot they came from, even a lot the hold emptied', async () => {
      await grant(1000);
      await holdVideo('req-1', 10);
      await holdVideo('req-2', 10);
      const signup = await signupLotId();
      expect(await lotRemaining(signup)).toBe(0);

      await service.release('req-2', 'failed', { costUsd: 0.05 });

      expect(await lotRemaining(signup)).toBe(160);
      expect((await service.summary(userId)).balance).toBe(1160);
      await expectConsistent();
    });

    it('should be a no-op after the debit', async () => {
      await holdVideo('req-1', 1);
      await service.settleActual('req-1', 0.1);
      const entries = await ledgerCount();

      const outcome = await service.release('req-1', 'failed');

      expect(outcome.status).toBe('already_debited');
      expect(await ledgerCount()).toBe(entries);
    });

    it('should let tokens returned to an expired lot expire instead of refilling it', async () => {
      const { lotId } = await grant(1000, new Date(clock.now().getTime() + 2 * HOUR_MS));
      await holdVideo('req-1', 10);
      clock.advance(3 * HOUR_MS);

      await service.release('req-1', 'failed');

      expect(await lotRemaining(lotId)).toBe(160);
      expect((await service.summary(userId)).credits.available).toBe(1000);
      await expectConsistent();
    });
  });
});
