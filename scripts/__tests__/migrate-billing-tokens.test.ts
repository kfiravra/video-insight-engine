import { describe, it, expect, beforeAll } from 'vitest';
import { ObjectId } from 'mongodb';
import type { FastifyBaseLogger } from 'fastify';
import { getTestDb, mockLogger } from '../../api/src/test/setup.js';
import { createBillingIndexes } from '../../api/src/plugins/mongodb.js';
import { BillingRepository } from '../../api/src/repositories/billing.repository.js';
import { UserRepository } from '../../api/src/repositories/user.repository.js';
import { BillingService } from '../../api/src/services/billing/billing.service.js';
import {
  OpenMinuteHoldsError,
  formatReport,
  looksLikeProd,
  migrateBillingTokens,
  parseCli,
  tokensForMinutes,
} from '../migrate-billing-tokens.js';

// A host as the minutes ledger left it: Kfir on free with nothing, the demo on
// manual Pro with a recurring 300 min/month and a credit lot, an eval account,
// a user who never got an account, a soft-deleted user, and an erased user's
// tombstone account.

const NOW = new Date('2026-11-04T09:00:00Z');
const IN_A_YEAR = new Date('2027-11-01T00:00:00Z');

interface Seeded {
  kfir: ObjectId;
  demo: ObjectId;
  noAccount: ObjectId;
  softDeleted: ObjectId;
  tombstoneOwner: ObjectId;
  demoAccount: ObjectId;
  kfirAccount: ObjectId;
  tombstoneAccount: ObjectId;
  demoLot: ObjectId;
  expiredLot: ObjectId;
  demoGrant: ObjectId;
}

function minuteAccount(ownerId: ObjectId, overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    _id: new ObjectId(),
    ownerType: 'user',
    ownerId,
    plan: { id: 'free', status: 'active', source: null, periodStart: null, periodEnd: null },
    buckets: {
      free: { recentFreeVideoAt: [new Date('2026-11-03T12:00:00Z')] },
      plan: { cycleStart: null, cycleEnd: null, minutesGranted: 0, minutesUsed: 0 },
      credit: { minutesAvailable: 0 },
    },
    holds: [],
    caps: {},
    version: 3,
    createdAt: new Date('2026-10-29T00:00:00Z'),
    updatedAt: new Date('2026-11-03T00:00:00Z'),
    ...overrides,
  };
}

async function seed(): Promise<Seeded> {
  const db = getTestDb();
  const user = async (email: string, extra: Record<string, unknown> = {}): Promise<ObjectId> => {
    const _id = new ObjectId();
    await db.collection('users').insertOne({ _id, email, name: email, createdAt: new Date('2026-01-01'), ...extra });
    return _id;
  };
  const kfir = await user('kfir@example.com');
  const demo = await user('demo@example.com');
  const noAccount = await user('new@example.com');
  const softDeleted = await user('gone@example.com', { deletedAt: new Date('2026-11-01') });
  const tombstoneOwner = new ObjectId();

  const kfirAccount = minuteAccount(kfir);
  const demoAccount = minuteAccount(demo, {
    plan: { id: 'pro', status: 'active', source: 'manual', periodStart: new Date('2026-10-29T00:00:00Z'), periodEnd: new Date('2027-10-29T00:00:00Z') },
    buckets: {
      free: { recentFreeVideoAt: [] },
      plan: { cycleStart: new Date('2026-10-29T00:00:00Z'), cycleEnd: new Date('2026-11-29T00:00:00Z'), minutesGranted: 600, minutesUsed: 45 },
      credit: { minutesAvailable: 210 },
    },
    caps: { freeVideosPerDay: 0, maxConcurrent: 3 },
  });
  const tombstoneAccount = minuteAccount(tombstoneOwner, {
    buckets: { free: { recentFreeVideoAt: [] }, plan: { cycleStart: null, cycleEnd: null, minutesGranted: 0, minutesUsed: 0 }, credit: { minutesAvailable: 0 } },
  });
  await db.collection('billingAccounts').insertMany([kfirAccount, demoAccount, tombstoneAccount]);

  const demoLot = new ObjectId();
  const expiredLot = new ObjectId();
  await db.collection('creditLots').insertMany([
    { _id: demoLot, accountId: demoAccount._id, minutesGranted: 300, minutesRemaining: 180, source: { kind: 'recurring', ref: 'g' }, expiresAt: IN_A_YEAR, createdAt: new Date('2026-10-29') },
    { _id: expiredLot, accountId: demoAccount._id, minutesGranted: 30, minutesRemaining: 30, source: { kind: 'admin', ref: 'a' }, expiresAt: new Date('2026-11-01'), createdAt: new Date('2026-10-01') },
    { _id: new ObjectId(), accountId: demoAccount._id, minutesGranted: 50, minutesRemaining: 0, source: { kind: 'admin', ref: 'b' }, expiresAt: null, createdAt: new Date('2026-10-02') },
  ]);
  const demoGrant = new ObjectId();
  await db.collection('recurringGrants').insertOne({
    _id: demoGrant, accountId: demoAccount._id, minutes: 300, every: 'month', active: true,
    nextRunAt: new Date('2026-11-29'), lastRunAt: new Date('2026-10-29'), actorId: 'system:migration',
    auditId: new ObjectId(), reason: 'migration: demo account monthly grant',
    createdAt: new Date('2026-10-29'), updatedAt: new Date('2026-10-29'), deletedAt: null,
  });
  await db.collection('billingShadow').insertOne({ requestId: 'r', userId: kfir });
  return {
    kfir, demo, noAccount, softDeleted, tombstoneOwner, demoGrant, demoLot, expiredLot,
    demoAccount: demoAccount._id as ObjectId,
    kfirAccount: kfirAccount._id as ObjectId,
    tombstoneAccount: tombstoneAccount._id as ObjectId,
  };
}

async function snapshot(): Promise<Record<string, unknown[]>> {
  const db = getTestDb();
  const names = ['billingAccounts', 'ledgerEntries', 'creditLots', 'recurringGrants', 'billingShadow', 'users'];
  const entries = await Promise.all(names.map(async (name) => [name, await db.collection(name).find({}).sort({ _id: 1 }).toArray()]));
  return Object.fromEntries(entries);
}

function billing(): BillingService {
  return new BillingService(new BillingRepository(getTestDb()), new UserRepository(getTestDb()), mockLogger as unknown as FastifyBaseLogger, () => NOW);
}

const run = (dryRun = false) => migrateBillingTokens(getTestDb(), { dryRun, now: NOW });

describe('migrate-billing-tokens', () => {
  beforeAll(async () => {
    await createBillingIndexes(getTestDb());
  });

  it('should convert minutes at the pack rate, 100/3 tokens a minute', () => {
    expect([tokensForMinutes(150), tokensForMinutes(300), tokensForMinutes(3000), tokensForMinutes(45), tokensForMinutes(1)]).toEqual([5000, 10000, 100000, 1500, 33]);
  });

  describe('--dry-run', () => {
    it('should write nothing and plan every account', async () => {
      await seed();
      const before = await snapshot();

      const { plan } = await run(true);

      expect(await snapshot()).toEqual(before);
      expect(plan.accounts).toHaveLength(3);
      expect(plan.newAccounts.map((user) => user.email)).toEqual(['new@example.com']);
      expect(plan.recurringGrants.map((grant) => grant.tokens)).toEqual([10000]);
      expect(plan.shadowRows).toBe(1);
    });

    it('should print a table of every account before and after', async () => {
      await seed();

      const report = formatReport(await run(true), true);

      expect(report).toContain('DRY RUN');
      expect(report).toMatch(/demo@example\.com\s+pro\s+555\s+210\s+18500\s+7000\s+yes/);
      expect(report).toMatch(/kfir@example\.com\s+free\s+0\s+0\s+0\s+1000\s+yes/);
      expect(report).toContain('300 min/month → 10000 tokens/month');
    });
  });

  describe('--run', () => {
    it('should give a running Pro cycle the 20,000 allowance minus what was used', async () => {
      const ids = await seed();

      await run();

      const account = await getTestDb().collection('billingAccounts').findOne({ _id: ids.demoAccount });
      expect(account?.buckets.plan).toMatchObject({ tokensGranted: 20000, tokensUsed: 1500 });
      expect(account?.buckets.plan).not.toHaveProperty('minutesGranted');
      expect(account?.plan.periodEnd).toEqual(new Date('2027-10-29T00:00:00Z'));
    });

    it('should turn the open lot into a token lot with the same expiry and close the minute lots', async () => {
      const ids = await seed();

      await run();

      const lots = getTestDb().collection('creditLots');
      const minuteLot = await lots.findOne({ _id: ids.demoLot });
      const tokenLot = await lots.findOne({ _id: minuteLot?.convertedToLotId });
      expect(minuteLot?.minutesRemaining).toBe(0);
      expect(tokenLot).toMatchObject({ tokensGranted: 6000, tokensRemaining: 6000, expiresAt: IN_A_YEAR, source: { kind: 'migration' } });
      const expired = await lots.findOne({ _id: ids.expiredLot });
      expect(expired?.minutesRemaining).toBe(0);
      expect(expired?.convertedToLotId).toBeUndefined();
    });

    it('should close every minute balance with a minute convert entry and open its token one', async () => {
      const ids = await seed();

      await run();

      const entries = await getTestDb().collection('ledgerEntries').find({ accountId: ids.demoAccount, type: 'convert' }).toArray();
      const byKey = Object.fromEntries(entries.map((entry) => [entry.idempotencyKey.split(':').slice(2).join(':'), entry]));
      expect(byKey.plan).toMatchObject({ bucket: 'plan', minutes: -555, actorId: 'system:migration' });
      expect(byKey.plan.unit).toBeUndefined();
      expect(byKey['plan:tokens']).toMatchObject({ unit: 'tokens', bucket: 'plan', tokens: 18500 });
      expect(byKey[`lot:${ids.demoLot.toHexString()}`]).toMatchObject({ minutes: -180 });
      expect(byKey[`lot:${ids.demoLot.toHexString()}:tokens`]).toMatchObject({ unit: 'tokens', tokens: 6000 });
      expect(byKey[`lot:${ids.expiredLot.toHexString()}`]).toMatchObject({ minutes: -30 });
      expect(byKey[`lot:${ids.expiredLot.toHexString()}:tokens`]).toBeUndefined();
    });

    it('should give every live user the signup grant once, and none to an erased user', async () => {
      const ids = await seed();

      await run();

      const grants = await getTestDb().collection('ledgerEntries').find({ idempotencyKey: /^grant:signup:/ }).toArray();
      expect(grants.map((grant) => grant.idempotencyKey).sort()).toEqual(
        [ids.kfir, ids.demo, ids.noAccount].map((id) => `grant:signup:${id.toHexString()}`).sort(),
      );
      const tombstone = await getTestDb().collection('billingAccounts').findOne({ _id: ids.tombstoneAccount });
      expect(tombstone?.signupGrantAt).toBeUndefined();
      expect(tombstone?.buckets).toEqual({ plan: { cycleStart: null, cycleEnd: null, tokensGranted: 0, tokensUsed: 0 }, credit: { tokensAvailable: 0 }, debt: { tokens: 0 } });
    });

    it('should create an account for a user without one and none for a soft-deleted user', async () => {
      const ids = await seed();

      const result = await run();

      const accounts = getTestDb().collection('billingAccounts');
      expect(await accounts.countDocuments({ ownerId: ids.noAccount })).toBe(1);
      expect(await accounts.countDocuments({ ownerId: ids.softDeleted })).toBe(0);
      expect(result.accountsCreated).toBe(1);
      expect((await billing().summary(ids.noAccount.toHexString())).balance).toBe(1000);
    });

    it('should drop the minute fields, the free-video cap, billingShadow and the minute lot indexes', async () => {
      const ids = await seed();
      await getTestDb().collection('creditLots').createIndex(
        { accountId: 1, expiresAt: 1, createdAt: 1 },
        { partialFilterExpression: { minutesRemaining: { $gt: 0 } } },
      );

      await run();

      const demo = await getTestDb().collection('billingAccounts').findOne({ _id: ids.demoAccount });
      expect(demo?.buckets).not.toHaveProperty('free');
      expect(demo?.buckets.credit).toEqual({ tokensAvailable: 7000 });
      expect(demo?.caps).toEqual({ maxConcurrent: 3 });
      expect(await getTestDb().listCollections({ name: 'billingShadow' }).hasNext()).toBe(false);
      const indexes = await getTestDb().collection('creditLots').indexes();
      expect(indexes.some((index) => JSON.stringify(index.partialFilterExpression ?? {}).includes('minutesRemaining'))).toBe(false);
    });

    it('should convert the recurring grant so the demo gets 10,000 tokens a month', async () => {
      const ids = await seed();

      await run();

      const grant = await getTestDb().collection('recurringGrants').findOne({ _id: ids.demoGrant });
      expect(grant).toMatchObject({ tokens: 10000, minutes: 300 });
    });

    it('should leave every account equal to its ledger, with the expected balances', async () => {
      const ids = await seed();

      const result = await run();

      expect(result.drift).toEqual([]);
      const demo = await billing().summary(ids.demo.toHexString());
      expect(demo).toMatchObject({ balance: 18500 + 7000, debt: 0, planTokens: { granted: 20000, used: 1500, remaining: 18500 }, signupGrantTokens: 1000 });
      expect((await billing().summary(ids.kfir.toHexString())).balance).toBe(1000);
    });

    it('should do nothing on a second run', async () => {
      await seed();
      await run();
      const after = await snapshot();

      const second = await run();

      expect(await snapshot()).toEqual(after);
      expect([second.accountsConverted, second.accountsCreated, second.signupGrants, second.recurringGrantsConverted]).toEqual([0, 0, 0, 0]);
      expect(second.plan.accounts.every((account) => account.done)).toBe(true);
    });

    it('should leave an account vie-api already created in tokens alone', async () => {
      await seed();
      const fresh = new ObjectId();
      await getTestDb().collection('users').insertOne({ _id: fresh, email: 'fresh@example.com', createdAt: NOW });
      await billing().ensureAccount(fresh.toHexString());
      const before = await getTestDb().collection('billingAccounts').findOne({ ownerId: fresh });

      const { plan } = await run();

      expect(plan.accounts.find((account) => account.ownerId.equals(fresh))?.done).toBe(true);
      expect(await getTestDb().collection('billingAccounts').findOne({ ownerId: fresh })).toEqual(before);
    });

    it('should refuse while a minute hold is open and write nothing', async () => {
      const ids = await seed();
      await getTestDb().collection('billingAccounts').updateOne(
        { _id: ids.kfirAccount },
        { $set: { holds: [{ requestId: 'run-1', userId: ids.kfir, youtubeId: 'abc', durationSec: 300, minutes: 5, split: { free: 5, plan: 0, credit: 0, lots: [] }, freeVideoAt: NOW, planCycleStart: null, createdAt: NOW }] } },
      );
      const before = await snapshot();

      await expect(run()).rejects.toBeInstanceOf(OpenMinuteHoldsError);
      expect(await snapshot()).toEqual(before);
    });
  });

  describe('CLI', () => {
    it('should take exactly one of --dry-run / --run', () => {
      expect(parseCli(['--dry-run'])).toEqual({ dryRun: true, yesProd: false });
      expect(parseCli(['--run', '--yes-prod'])).toEqual({ dryRun: false, yesProd: true });
      expect(() => parseCli([])).toThrow(/exactly one/);
      expect(() => parseCli(['--dry-run', '--run'])).toThrow(/exactly one/);
    });

    it.each([
      ['mongodb://localhost:27017/video-insight-engine?directConnection=true', false],
      ['mongodb://vie-mongodb:27017/video-insight-engine', false],
      ['mongodb://user:pw@localhost:27017/x', true],
      ['mongodb+srv://cluster.example.net/x', true],
      ['mongodb://10.0.0.5:27017/x', true],
    ])('should tell %s looks like production: %s', (uri, prod) => {
      expect(looksLikeProd(uri)).toBe(prod);
    });
  });
});
