import { describe, it, expect, beforeAll, beforeEach } from 'vitest';
import { ObjectId } from 'mongodb';
import type { FastifyBaseLogger } from 'fastify';
import { getTestDb, mockLogger } from '../../../test/setup.js';
import { createBillingIndexes } from '../../../plugins/mongodb.js';
import { BillingRepository } from '../../../repositories/billing.repository.js';
import { UserRepository } from '../../../repositories/user.repository.js';
import { NotFoundError, ValidationError } from '../../../utils/errors.js';
import { BillingService } from '../billing.service.js';
import { adminCtx, DAY_MS, insertUser, makeClock } from './billing-fixtures.js';
import type { AdminMutationContext } from '../billing.service.js';
import type { TestClock } from './billing-fixtures.js';

describe('BillingService admin API (Contract 1)', () => {
  let clock: TestClock;
  let service: BillingService;
  let userId: string;
  const adminId = new ObjectId().toHexString();

  const ctx = (overrides: Partial<AdminMutationContext> = {}): AdminMutationContext =>
    adminCtx({ actorId: adminId, reason: 'support ticket 42', ...overrides });
  const ghost = () => new ObjectId().toHexString();
  const entriesFor = (auditId: string) => getTestDb().collection('ledgerEntries').find({ 'source.ref': auditId }).toArray();
  const expectConsistent = async () => expect((await service.verifyAccount(userId)).drift).toEqual([]);

  beforeAll(async () => {
    await createBillingIndexes(getTestDb());
  });

  beforeEach(async () => {
    clock = makeClock('2026-11-01T10:00:00Z');
    const db = getTestDb();
    service = new BillingService(new BillingRepository(db), new UserRepository(db), mockLogger as unknown as FastifyBaseLogger, clock.now);
    userId = await insertUser(db);
  });

  describe('getAccountDetail', () => {
    it('should return the account in tokens, its lots, holds, ledger and recurring grants', async () => {
      await service.grantLot(userId, { tokens: 1500 }, ctx());

      const detail = await service.getAccountDetail(userId);

      expect(detail.account).toMatchObject({
        credits: { available: 2500 }, debt: 0, balance: 2500, signupGrantAt: '2026-11-01T10:00:00.000Z', openHolds: 0,
      });
      expect(detail.lots.map((l) => [l.tokensGranted, l.expired])).toEqual([[1500, false], [1000, false]]);
      expect(detail.ledger.map((e) => [e.type, e.unit, e.amount])).toEqual([['grant', 'tokens', 1500], ['grant', 'tokens', 1000]]);
      expect(detail).toMatchObject({ holds: [], recurringGrants: [] });
    });

    it('should page the ledger with ledgerLimit and ledgerBefore', async () => {
      await service.ensureAccount(userId);
      for (let i = 0; i < 3; i++) {
        clock.advance(1000);
        await service.grantLot(userId, { tokens: 10 + i }, ctx());
      }

      const first = await service.getAccountDetail(userId, { ledgerLimit: 2 });
      const next = await service.getAccountDetail(userId, { ledgerLimit: 2, ledgerBefore: new Date(first.ledger[1].createdAt) });

      expect(first.ledger.map((e) => e.amount)).toEqual([12, 11]);
      expect(next.ledger.map((e) => e.amount)).toEqual([10, 1000]);
    });

    it('should show a minute entry from before the token migration in minutes', async () => {
      await service.ensureAccount(userId);
      const account = await getTestDb().collection('billingAccounts').findOne({ ownerId: new ObjectId(userId) });
      await getTestDb().collection('ledgerEntries').insertOne({
        accountId: account?._id, userId: new ObjectId(userId), type: 'grant', bucket: 'credit', minutes: 150,
        source: { kind: 'admin', ref: new ObjectId().toHexString() }, idempotencyKey: 'admin:legacy', createdAt: new Date('2026-10-30T00:00:00Z'),
      });

      const { ledger, account: snapshot } = await service.getAccountDetail(userId);

      expect(ledger.map((e) => [e.unit, e.amount])).toEqual([['tokens', 1000], ['minutes', 150]]);
      expect(snapshot.balance).toBe(1000);
    });

    it('should throw NotFoundError for an unknown user', async () => {
      await expect(service.getAccountDetail(ghost())).rejects.toBeInstanceOf(NotFoundError);
    });

    it('should reject an out-of-range ledgerLimit', async () => {
      await expect(service.getAccountDetail(userId, { ledgerLimit: 0 })).rejects.toBeInstanceOf(ValidationError);
    });
  });

  describe('setPlan', () => {
    it('should start a manual 12-month Pro plan with 20,000 tokens for the current cycle', async () => {
      const { before, after } = await service.setPlan(userId, { planId: 'pro' }, ctx());

      expect(before).toMatchObject({ plan: { id: 'free' }, balance: 1000 });
      expect(after.plan).toMatchObject({ id: 'pro', source: 'manual', periodEnd: '2027-11-01T10:00:00.000Z' });
      expect(after.planTokens).toMatchObject({ granted: 20_000, used: 0, remaining: 20_000, cycleEnd: '2026-12-01T10:00:00.000Z' });
      expect(after.balance).toBe(21_000);
    });

    it('should honour a given periodEnd', async () => {
      const periodEnd = new Date('2027-02-01T00:00:00Z');

      const { after } = await service.setPlan(userId, { planId: 'pro', periodEnd }, ctx());

      expect(after.plan.periodEnd).toBe(periodEnd.toISOString());
    });

    it('should stop plan tokens when set back to free', async () => {
      await service.setPlan(userId, { planId: 'pro' }, ctx());

      const { after } = await service.setPlan(userId, { planId: 'free' }, ctx());

      expect([after.plan.id, after.planTokens.remaining, after.balance]).toEqual(['free', 0, 1000]);
      await expectConsistent();
    });

    it('should stamp actor, reason and audit id on its token entries', async () => {
      const audit = ctx();

      await service.setPlan(userId, { planId: 'pro' }, audit);

      const [entry] = await entriesFor(audit.auditId);
      expect(entry).toMatchObject({
        idempotencyKey: `admin:${audit.auditId}:grant`, unit: 'tokens', tokens: 20_000, actorId: adminId, reason: 'support ticket 42',
      });
      expect(entry.auditId.toHexString()).toBe(audit.auditId);
    });

    it('should grant once when retried with the same auditId', async () => {
      const audit = ctx();
      await service.setPlan(userId, { planId: 'pro' }, audit);

      const { before, after } = await service.setPlan(userId, { planId: 'pro' }, audit);

      expect(after).toEqual(before);
      expect(await entriesFor(audit.auditId)).toHaveLength(1);
    });

    it('should reject a periodEnd in the past', async () => {
      await expect(service.setPlan(userId, { planId: 'pro', periodEnd: new Date('2026-01-01') }, ctx())).rejects.toBeInstanceOf(ValidationError);
    });

    it('should reject an unknown plan id', async () => {
      const input = JSON.parse('{"planId":"team"}');

      await expect(service.setPlan(userId, input, ctx())).rejects.toBeInstanceOf(ValidationError);
    });
  });

  describe('grantLot', () => {
    it('should create a never-expiring lot by default and return its id', async () => {
      const { lotId, after } = await service.grantLot(userId, { tokens: 300 }, ctx());

      const detail = await service.getAccountDetail(userId);
      expect(detail.lots).toContainEqual(expect.objectContaining({ lotId, tokensGranted: 300, tokensRemaining: 300, expiresAt: null }));
      expect(after.credits.available).toBe(1300);
    });

    it('should keep an optional expiry', async () => {
      const expiresAt = new Date(clock.now().getTime() + 30 * DAY_MS);

      const { lotId } = await service.grantLot(userId, { tokens: 300, expiresAt }, ctx());

      const lot = (await service.getAccountDetail(userId)).lots.find((l) => l.lotId === lotId);
      expect(lot?.expiresAt).toBe(expiresAt.toISOString());
    });

    it('should grant once and return the same lot when retried with the same auditId', async () => {
      const audit = ctx();
      const first = await service.grantLot(userId, { tokens: 300 }, audit);

      const second = await service.grantLot(userId, { tokens: 300 }, audit);

      expect(second.lotId).toBe(first.lotId);
      expect(second.after.credits.available).toBe(1300);
    });

    it.each([0, -5, 1.5, 1_000_001])('should reject %s tokens', async (tokens) => {
      await expect(service.grantLot(userId, { tokens }, ctx())).rejects.toBeInstanceOf(ValidationError);
    });

    it.each([1, 1_000_000])('should accept %i tokens', async (tokens) => {
      const { after } = await service.grantLot(userId, { tokens }, ctx());

      expect(after.credits.available).toBe(1000 + tokens);
    });

    it('should reject an expiry in the past', async () => {
      await expect(service.grantLot(userId, { tokens: 10, expiresAt: new Date('2026-01-01') }, ctx())).rejects.toBeInstanceOf(ValidationError);
    });

    it('should reject the minutes field of the old API', async () => {
      const input = JSON.parse('{"minutes":300}');

      await expect(service.grantLot(userId, input, ctx())).rejects.toBeInstanceOf(ValidationError);
    });
  });

  describe('setCaps', () => {
    it('should set caps and clear a field with null', async () => {
      await service.setCaps(userId, { blocked: true, maxVideoMinutes: 60 }, ctx());

      const { after } = await service.setCaps(userId, { blocked: null }, ctx());

      expect(after.caps).toEqual({ maxVideoMinutes: 60 });
    });

    it('should make a retried call a no-op, even after a newer change (I1)', async () => {
      const block = ctx();
      await service.setCaps(userId, { blocked: true }, block);
      await service.setCaps(userId, { blocked: null }, ctx());

      const { before, after } = await service.setCaps(userId, { blocked: true }, block);

      expect(after).toEqual(before);
      expect(after.caps.blocked).toBeUndefined();
    });

    it.each([[{ maxVideoMinutes: 1 }], [{ maxVideoMinutes: 180 }], [{ maxConcurrent: 1 }]])('should accept %j', async (caps) => {
      const { after } = await service.setCaps(userId, caps, ctx());

      expect(after.caps).toEqual(caps);
    });

    it.each([
      ['{"maxVideoMinutes":181}'],
      ['{"maxVideoMinutes":0}'],
      ['{"freeVideosPerDay":1}'],
      ['{"maxConcurrent":0}'],
      ['{}'],
    ])('should reject %s', async (json) => {
      await expect(service.setCaps(userId, JSON.parse(json), ctx())).rejects.toBeInstanceOf(ValidationError);
    });
  });

  describe('adjust', () => {
    it('should take negative credit adjustments from lots in FIFO order', async () => {
      const soon = await service.grantLot(userId, { tokens: 200, expiresAt: new Date(clock.now().getTime() + DAY_MS) }, ctx());
      await service.grantLot(userId, { tokens: 500 }, ctx());

      const { after } = await service.adjust(userId, { bucket: 'credit', tokens: -300 }, ctx());

      expect(after.credits.available).toBe(1400);
      const lots = (await service.getAccountDetail(userId)).lots;
      expect(lots.find((l) => l.lotId === soon.lotId)?.tokensRemaining).toBe(0);
      await expectConsistent();
    });

    it('should add positive credit as a new lot', async () => {
      const { after } = await service.adjust(userId, { bucket: 'credit', tokens: 25 }, ctx());

      expect(after.credits.available).toBe(1025);
      expect((await service.getAccountDetail(userId)).lots).toHaveLength(2);
      await expectConsistent();
    });

    it('should adjust the current Pro cycle both ways', async () => {
      await service.setPlan(userId, { planId: 'pro' }, ctx());

      const up = await service.adjust(userId, { bucket: 'plan', tokens: 500 }, ctx());
      const down = await service.adjust(userId, { bucket: 'plan', tokens: -20_500 }, ctx());

      expect([up.after.planTokens.remaining, down.after.planTokens.remaining]).toEqual([20_500, 0]);
      await expectConsistent();
    });

    it('should apply once when retried with the same auditId', async () => {
      const audit = ctx();
      await service.adjust(userId, { bucket: 'credit', tokens: 25 }, audit);

      const { after } = await service.adjust(userId, { bucket: 'credit', tokens: 25 }, audit);

      expect(after.credits.available).toBe(1025);
    });

    it('should refuse to adjust the plan bucket of a free account', async () => {
      await expect(service.adjust(userId, { bucket: 'plan', tokens: 10 }, ctx())).rejects.toBeInstanceOf(ValidationError);
    });

    it('should refuse an adjustment that would go below zero (Q17)', async () => {
      await service.setPlan(userId, { planId: 'pro' }, ctx());

      await expect(service.adjust(userId, { bucket: 'credit', tokens: -1001 }, ctx())).rejects.toBeInstanceOf(ValidationError);
      await expect(service.adjust(userId, { bucket: 'plan', tokens: -20_001 }, ctx())).rejects.toBeInstanceOf(ValidationError);
    });

    it.each([0, 1.5, 1_000_001, -1_000_001])('should reject %s tokens', async (tokens) => {
      await expect(service.adjust(userId, { bucket: 'credit', tokens }, ctx())).rejects.toBeInstanceOf(ValidationError);
    });
  });

  describe('snapshots', () => {
    it('should show debt and the negative balance before and after', async () => {
      await service.hold('req-1', await service.quote(userId, 'dQw4w9WgXcQ', 600));
      await service.settleActual('req-1', 0.6);

      const { before, after } = await service.grantLot(userId, { tokens: 300 }, ctx());

      expect([before.debt, before.balance, after.debt, after.balance]).toEqual([800, -800, 500, -500]);
    });
  });

  describe('recurring grants', () => {
    it('should create a monthly grant in tokens that is due now and list it', async () => {
      const { grantId } = await service.createRecurringGrant(userId, { tokens: 10_000, every: 'month', active: true }, ctx());

      const [grant] = (await service.getAccountDetail(userId)).recurringGrants;
      expect(grant).toMatchObject({ grantId, tokens: 10_000, every: 'month', active: true, nextRunAt: '2026-11-01T10:00:00.000Z' });
    });

    it('should create one grant when retried with the same auditId', async () => {
      const audit = ctx();
      const first = await service.createRecurringGrant(userId, { tokens: 300, every: 'month', active: true }, audit);

      const second = await service.createRecurringGrant(userId, { tokens: 300, every: 'month', active: true }, audit);

      expect(second.grantId).toBe(first.grantId);
      expect((await service.getAccountDetail(userId)).recurringGrants).toHaveLength(1);
    });

    it.each([
      ['a period other than month', '{"tokens":300,"every":"week","active":true}'],
      ['0 tokens', '{"tokens":0,"every":"month","active":true}'],
      ['more than 1,000,000 tokens', '{"tokens":1000001,"every":"month","active":true}'],
      ['the minutes field of the old API', '{"minutes":300,"every":"month","active":true}'],
    ])('should reject %s', async (_label, json) => {
      await expect(service.createRecurringGrant(userId, JSON.parse(json), ctx())).rejects.toBeInstanceOf(ValidationError);
    });

    it('should delete a grant so it no longer lists', async () => {
      const { grantId } = await service.createRecurringGrant(userId, { tokens: 300, every: 'month', active: true }, ctx());

      await service.deleteRecurringGrant(userId, grantId, ctx());

      expect((await service.getAccountDetail(userId)).recurringGrants).toEqual([]);
    });

    it('should throw NotFoundError for an unknown grant and ValidationError for a malformed id', async () => {
      await expect(service.deleteRecurringGrant(userId, ghost(), ctx())).rejects.toBeInstanceOf(NotFoundError);
      await expect(service.deleteRecurringGrant(userId, 'nope', ctx())).rejects.toBeInstanceOf(ValidationError);
    });
  });

  describe('every mutation', () => {
    const mutations: Array<[string, (uid: string, c: AdminMutationContext) => Promise<unknown>]> = [
      ['setPlan', (uid, c) => service.setPlan(uid, { planId: 'pro' }, c)],
      ['grantLot', (uid, c) => service.grantLot(uid, { tokens: 10 }, c)],
      ['setCaps', (uid, c) => service.setCaps(uid, { blocked: true }, c)],
      ['adjust', (uid, c) => service.adjust(uid, { bucket: 'credit', tokens: 5 }, c)],
      ['createRecurringGrant', (uid, c) => service.createRecurringGrant(uid, { tokens: 10, every: 'month', active: true }, c)],
      ['deleteRecurringGrant', (uid, c) => service.deleteRecurringGrant(uid, ghost(), c)],
    ];

    it.each(mutations)('%s should throw NotFoundError for an unknown user', async (_name, run) => {
      await expect(run(ghost(), ctx())).rejects.toBeInstanceOf(NotFoundError);
    });

    it.each(mutations)('%s should require a reason', async (_name, run) => {
      await expect(run(userId, ctx({ reason: '  ' }))).rejects.toBeInstanceOf(ValidationError);
    });

    it.each(mutations)('%s should require a hex auditId', async (_name, run) => {
      await expect(run(userId, ctx({ auditId: 'audit-1' }))).rejects.toBeInstanceOf(ValidationError);
    });
  });
});
