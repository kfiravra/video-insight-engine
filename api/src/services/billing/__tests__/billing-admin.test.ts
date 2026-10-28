import { describe, it, expect, beforeAll, beforeEach } from 'vitest';
import { ObjectId } from 'mongodb';
import type { FastifyBaseLogger } from 'fastify';
import { getTestDb, mockLogger } from '../../../test/setup.js';
import { createBillingIndexes } from '../../../plugins/mongodb.js';
import { BillingRepository } from '../../../repositories/billing.repository.js';
import { UserRepository } from '../../../repositories/user.repository.js';
import { NotFoundError, ValidationError } from '../../../utils/errors.js';
import { BillingService } from '../billing.service.js';
import { DAY_MS, insertUser, makeClock } from './billing-fixtures.js';
import type { AdminMutationContext } from '../billing.service.js';
import type { TestClock } from './billing-fixtures.js';

describe('BillingService admin API (Contract 1)', () => {
  let clock: TestClock;
  let service: BillingService;
  let userId: string;
  const adminId = new ObjectId().toHexString();

  const ctx = (overrides: Partial<AdminMutationContext> = {}): AdminMutationContext => ({
    actorId: adminId,
    reason: 'support ticket 42',
    auditId: new ObjectId().toHexString(),
    ...overrides,
  });
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
    it('should return account, lots, holds, ledger and recurring grants', async () => {
      await service.grantLot(userId, { minutes: 150 }, ctx());

      const detail = await service.getAccountDetail(userId);

      expect(detail.account.credits.available).toBe(150);
      expect(detail.lots).toHaveLength(1);
      expect(detail.ledger.map((e) => e.type)).toEqual(['grant']);
      expect(detail).toMatchObject({ holds: [], recurringGrants: [] });
    });

    it('should page the ledger with ledgerLimit and ledgerBefore', async () => {
      for (let i = 0; i < 3; i++) {
        await service.grantLot(userId, { minutes: 10 + i }, ctx());
        clock.advance(1000);
      }

      const first = await service.getAccountDetail(userId, { ledgerLimit: 2 });
      const next = await service.getAccountDetail(userId, { ledgerLimit: 2, ledgerBefore: new Date(first.ledger[1].createdAt) });

      expect(first.ledger.map((e) => e.minutes)).toEqual([12, 11]);
      expect(next.ledger.map((e) => e.minutes)).toEqual([10]);
    });

    it('should throw NotFoundError for an unknown user', async () => {
      await expect(service.getAccountDetail(ghost())).rejects.toBeInstanceOf(NotFoundError);
    });

    it('should reject an out-of-range ledgerLimit', async () => {
      await expect(service.getAccountDetail(userId, { ledgerLimit: 0 })).rejects.toBeInstanceOf(ValidationError);
    });
  });

  describe('setPlan', () => {
    it('should start a manual 12-month Pro plan with 600 minutes for the current period', async () => {
      const { before, after } = await service.setPlan(userId, { planId: 'pro' }, ctx());

      expect(before.plan.id).toBe('free');
      expect(after.plan).toMatchObject({ id: 'pro', source: 'manual', periodEnd: '2027-11-01T10:00:00.000Z' });
      expect(after.planMinutes).toMatchObject({ granted: 600, remaining: 600, cycleEnd: '2026-12-01T10:00:00.000Z' });
    });

    it('should honour a given periodEnd', async () => {
      const periodEnd = new Date('2027-02-01T00:00:00Z');

      const { after } = await service.setPlan(userId, { planId: 'pro', periodEnd }, ctx());

      expect(after.plan.periodEnd).toBe(periodEnd.toISOString());
    });

    it('should stop plan minutes when set back to free', async () => {
      await service.setPlan(userId, { planId: 'pro' }, ctx());

      const { after } = await service.setPlan(userId, { planId: 'free' }, ctx());

      expect(after.plan.id).toBe('free');
      expect(after.planMinutes.remaining).toBe(0);
      await expectConsistent();
    });

    it('should stamp actor, reason and audit id on its ledger entries', async () => {
      const audit = ctx();

      await service.setPlan(userId, { planId: 'pro' }, audit);

      const [entry] = await entriesFor(audit.auditId);
      expect(entry).toMatchObject({ idempotencyKey: `admin:${audit.auditId}:grant`, actorId: adminId, reason: 'support ticket 42' });
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
      const { lotId, after } = await service.grantLot(userId, { minutes: 300 }, ctx());

      const detail = await service.getAccountDetail(userId);
      expect(detail.lots).toEqual([expect.objectContaining({ lotId, minutesRemaining: 300, expiresAt: null })]);
      expect(after.credits.available).toBe(300);
    });

    it('should keep an optional expiry', async () => {
      const expiresAt = new Date(clock.now().getTime() + 30 * DAY_MS);

      await service.grantLot(userId, { minutes: 300, expiresAt }, ctx());

      expect((await service.getAccountDetail(userId)).lots[0].expiresAt).toBe(expiresAt.toISOString());
    });

    it('should grant once and return the same lot when retried with the same auditId', async () => {
      const audit = ctx();
      const first = await service.grantLot(userId, { minutes: 300 }, audit);

      const second = await service.grantLot(userId, { minutes: 300 }, audit);

      expect(second.lotId).toBe(first.lotId);
      expect(second.after.credits.available).toBe(300);
    });

    it.each([0, -5, 1.5, 100_001])('should reject %s minutes', async (minutes) => {
      await expect(service.grantLot(userId, { minutes }, ctx())).rejects.toBeInstanceOf(ValidationError);
    });

    it('should reject an expiry in the past', async () => {
      await expect(service.grantLot(userId, { minutes: 10, expiresAt: new Date('2026-01-01') }, ctx())).rejects.toBeInstanceOf(ValidationError);
    });
  });

  describe('setCaps', () => {
    it('should set caps and clear a field with null', async () => {
      await service.setCaps(userId, { blocked: true, maxVideoMinutes: 60 }, ctx());

      const { after } = await service.setCaps(userId, { blocked: null }, ctx());

      expect(after.caps).toEqual({ maxVideoMinutes: 60 });
    });

    it('should reflect freeVideosPerDay in the free window', async () => {
      const { after } = await service.setCaps(userId, { freeVideosPerDay: 2 }, ctx());

      expect(after.free.perWindow).toBe(2);
    });

    it.each([
      [{ maxVideoMinutes: 500 }],
      [{ freeVideosPerDay: -1 }],
      [{ maxConcurrent: 0 }],
      [{}],
    ])('should reject %j', async (caps) => {
      await expect(service.setCaps(userId, caps, ctx())).rejects.toBeInstanceOf(ValidationError);
    });
  });

  describe('adjust', () => {
    it('should take negative credit adjustments from lots in FIFO order', async () => {
      const soon = await service.grantLot(userId, { minutes: 20, expiresAt: new Date(clock.now().getTime() + DAY_MS) }, ctx());
      await service.grantLot(userId, { minutes: 50 }, ctx());

      const { after } = await service.adjust(userId, { bucket: 'credit', minutes: -30 }, ctx());

      expect(after.credits.available).toBe(40);
      const lots = (await service.getAccountDetail(userId)).lots;
      expect(lots.find((l) => l.lotId === soon.lotId)?.minutesRemaining).toBe(0);
      await expectConsistent();
    });

    it('should add positive credit as a new lot', async () => {
      const { after } = await service.adjust(userId, { bucket: 'credit', minutes: 25 }, ctx());

      expect(after.credits.available).toBe(25);
      await expectConsistent();
    });

    it('should adjust the current Pro cycle', async () => {
      await service.setPlan(userId, { planId: 'pro' }, ctx());

      const { after } = await service.adjust(userId, { bucket: 'plan', minutes: 50 }, ctx());

      expect(after.planMinutes.remaining).toBe(650);
      await expectConsistent();
    });

    it('should refuse to adjust the plan bucket of a free account', async () => {
      await expect(service.adjust(userId, { bucket: 'plan', minutes: 10 }, ctx())).rejects.toBeInstanceOf(ValidationError);
    });

    it('should refuse an adjustment that would go below zero (Q17)', async () => {
      await service.grantLot(userId, { minutes: 10 }, ctx());

      await expect(service.adjust(userId, { bucket: 'credit', minutes: -11 }, ctx())).rejects.toBeInstanceOf(ValidationError);
    });

    it('should reject 0 minutes', async () => {
      await expect(service.adjust(userId, { bucket: 'credit', minutes: 0 }, ctx())).rejects.toBeInstanceOf(ValidationError);
    });
  });

  describe('recurring grants', () => {
    it('should create a monthly grant that is due now and list it', async () => {
      const { grantId } = await service.createRecurringGrant(userId, { minutes: 300, every: 'month', active: true }, ctx());

      const [grant] = (await service.getAccountDetail(userId)).recurringGrants;
      expect(grant).toMatchObject({ grantId, minutes: 300, every: 'month', active: true, nextRunAt: '2026-11-01T10:00:00.000Z' });
    });

    it('should create one grant when retried with the same auditId', async () => {
      const audit = ctx();
      const first = await service.createRecurringGrant(userId, { minutes: 300, every: 'month', active: true }, audit);

      const second = await service.createRecurringGrant(userId, { minutes: 300, every: 'month', active: true }, audit);

      expect(second.grantId).toBe(first.grantId);
      expect((await service.getAccountDetail(userId)).recurringGrants).toHaveLength(1);
    });

    it('should reject a period other than month', async () => {
      const input = JSON.parse('{"minutes":300,"every":"week","active":true}');

      await expect(service.createRecurringGrant(userId, input, ctx())).rejects.toBeInstanceOf(ValidationError);
    });

    it('should delete a grant so it no longer lists', async () => {
      const { grantId } = await service.createRecurringGrant(userId, { minutes: 300, every: 'month', active: true }, ctx());

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
      ['grantLot', (uid, c) => service.grantLot(uid, { minutes: 10 }, c)],
      ['setCaps', (uid, c) => service.setCaps(uid, { blocked: true }, c)],
      ['adjust', (uid, c) => service.adjust(uid, { bucket: 'credit', minutes: 5 }, c)],
      ['createRecurringGrant', (uid, c) => service.createRecurringGrant(uid, { minutes: 10, every: 'month', active: true }, c)],
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
