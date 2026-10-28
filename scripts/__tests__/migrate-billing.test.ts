import { describe, it, expect, beforeAll, beforeEach } from 'vitest';
import { ObjectId } from 'mongodb';
import type { FastifyBaseLogger } from 'fastify';
import { getTestDb, mockLogger } from '../../api/src/test/setup.js';
import { createBillingIndexes } from '../../api/src/plugins/mongodb.js';
import { BillingRepository } from '../../api/src/repositories/billing.repository.js';
import { UserRepository } from '../../api/src/repositories/user.repository.js';
import { BillingService } from '../../api/src/services/billing/billing.service.js';
import {
  NoAdminsRemainError,
  auditIdFor,
  looksLikeProd,
  migrateBilling,
  parseCli,
  unconsumedMinutes,
} from '../migrate-billing.js';
import type { MigrateOptions, MigrationPlan } from '../migrate-billing.js';

const NOW = new Date('2026-11-02T10:00:00Z');
const DAY = '2026-11-02';
const YESTERDAY = '2026-11-01';
const IN_A_YEAR = '2027-11-02T10:00:00.000Z';
const KEEPER = 'kfir@example.com';
const DEMO = 'demo@example.com';
const EVAL_A = 'eval-a@example.com';
const EVAL_B = 'eval-b@example.com';

const options = (overrides: Partial<MigrateOptions> = {}): MigrateOptions => ({
  dryRun: false,
  adminEmails: [KEEPER],
  demoEmail: DEMO,
  evalEmails: [EVAL_A, EVAL_B],
  day: DAY,
  now: NOW,
  ...overrides,
});

async function seedUser(email: string, extra: Record<string, unknown> = {}): Promise<string> {
  const _id = new ObjectId();
  await getTestDb().collection('users').insertOne({
    _id,
    email,
    passwordHash: 'hash-never-printed',
    name: email.split('@')[0],
    createdAt: new Date('2026-01-01T00:00:00Z'),
    updatedAt: new Date('2026-01-01T00:00:00Z'),
    ...extra,
  });
  return _id.toHexString();
}

async function seedAdjustment(userId: string, date: string, amountUsd: number): Promise<void> {
  await getTestDb().collection('userCostAdjustments').insertOne({
    userId: new ObjectId(userId),
    date,
    amountUsd,
    reason: 'fixture',
    adminId: new ObjectId(),
    createdAt: new Date('2026-11-02T08:00:00Z'),
  });
}

async function seedSpend(userId: string, date: string, totalCostUsd: number): Promise<void> {
  await getTestDb().collection('userCosts').insertOne({
    userId: new ObjectId(userId),
    date,
    totalCostUsd,
    videoCount: 1,
    creditAdjustmentUsd: 0,
    updatedAt: new Date(),
  });
}

interface Fixture {
  keeper: string;
  oldAdmin: string;
  oldProAdmin: string;
  pro: string;
  team: string;
  free: string;
  deletedPro: string;
  demo: string;
  evalA: string;
  evalB: string;
  creditor: string;
  spender: string;
  charged: string;
}

/** A small host as the dollar gate left it: legacy admins, tiers, and migration-day credit. */
async function seedFixture(): Promise<Fixture> {
  const fixture: Fixture = {
    keeper: await seedUser(KEEPER, { role: 'admin' }),
    oldAdmin: await seedUser('old-admin@example.com', { role: 'admin' }),
    oldProAdmin: await seedUser('old-pro-admin@example.com', { role: 'admin', tier: 'pro' }),
    pro: await seedUser('pro@example.com', { tier: 'pro' }),
    team: await seedUser('team@example.com', { tier: 'team' }),
    free: await seedUser('free@example.com'),
    deletedPro: await seedUser('deleted@example.com', { tier: 'pro', deletedAt: new Date('2026-10-30T00:00:00Z') }),
    demo: await seedUser(DEMO),
    evalA: await seedUser(EVAL_A),
    evalB: await seedUser(EVAL_B),
    creditor: await seedUser('creditor@example.com'),
    spender: await seedUser('spender@example.com'),
    charged: await seedUser('charged@example.com'),
  };
  await seedAdjustment(fixture.creditor, DAY, -3);
  await seedAdjustment(fixture.creditor, DAY, -2);
  await seedAdjustment(fixture.creditor, YESTERDAY, -10);
  await seedSpend(fixture.creditor, DAY, 1);
  await seedAdjustment(fixture.spender, DAY, -2);
  await seedSpend(fixture.spender, DAY, 5);
  await seedAdjustment(fixture.charged, DAY, 4);
  return fixture;
}

/** Every document of every collection, so "writes nothing" and "no-op" mean the whole database. */
async function snapshot(): Promise<Record<string, unknown[]>> {
  const db = getTestDb();
  const names = (await db.listCollections().toArray()).map((collection) => collection.name).sort();
  const entries = await Promise.all(
    names.map(async (name) => [name, await db.collection(name).find({}).sort({ _id: 1 }).toArray()] as const),
  );
  return Object.fromEntries(entries);
}

function emptyCounts(plan: MigrationPlan): number[] {
  return [
    plan.accounts, plan.pro, plan.team, plan.teamLots, plan.roles, plan.creditLots, plan.demoGrants, plan.evalGrants,
  ].map((steps) => steps.length);
}

describe('migrate-billing', () => {
  let billing: BillingService;
  let ids: Fixture;

  beforeAll(async () => {
    await createBillingIndexes(getTestDb());
  });

  beforeEach(async () => {
    billing = new BillingService(
      new BillingRepository(getTestDb()),
      new UserRepository(getTestDb()),
      mockLogger as unknown as FastifyBaseLogger,
      () => NOW,
    );
    ids = await seedFixture();
  });

  describe('--dry-run', () => {
    it('should write nothing to any collection', async () => {
      const before = await snapshot();

      await migrateBilling(getTestDb(), options({ dryRun: true }));

      expect(await snapshot()).toEqual(before);
    });

    it('should plan every step', async () => {
      const plan = await migrateBilling(getTestDb(), options({ dryRun: true }));

      expect(emptyCounts(plan)).toEqual([12, 2, 1, 1, 3, 1, 1, 2]);
    });

    it('should say who is skipped and why', async () => {
      const plan = await migrateBilling(getTestDb(), options({ dryRun: true }));

      expect(plan.skipped).toEqual([
        { email: 'spender@example.com', why: 'credit of $2.00 converts to 0 minutes; not granted' },
      ]);
    });
  });

  describe('--run', () => {
    it('should create an account for every active user and none for a soft-deleted one', async () => {
      await migrateBilling(getTestDb(), options());

      const accounts = await getTestDb().collection('billingAccounts').find({}).toArray();
      expect(accounts).toHaveLength(12);
      expect(accounts.map((account) => account.ownerId.toHexString())).not.toContain(ids.deletedPro);
    });

    it('should turn tier pro into a manual Pro plan for 12 months', async () => {
      await migrateBilling(getTestDb(), options());

      const { account } = await billing.getAccountDetail(ids.pro);
      expect(account.plan).toMatchObject({ id: 'pro', source: 'manual', periodEnd: IN_A_YEAR });
      expect(account.planMinutes.granted).toBe(600);
    });

    it('should turn tier team into Pro plus a 600-minute lot that expires in 12 months', async () => {
      await migrateBilling(getTestDb(), options());

      const detail = await billing.getAccountDetail(ids.team);
      expect(detail.account.plan.id).toBe('pro');
      expect(detail.lots).toEqual([
        expect.objectContaining({ minutesGranted: 600, minutesRemaining: 600, expiresAt: IN_A_YEAR }),
      ]);
    });

    it('should word the team lot "migration: team tier" with the migration actor', async () => {
      await migrateBilling(getTestDb(), options());

      const { ledger } = await billing.getAccountDetail(ids.team);
      expect(ledger).toContainEqual(
        expect.objectContaining({ type: 'grant', bucket: 'credit', minutes: 600, reason: 'migration: team tier', actorId: 'system:migration' }),
      );
    });

    it('should leave a soft-deleted pro user and a free user without a plan', async () => {
      await migrateBilling(getTestDb(), options());

      const free = await billing.getAccountDetail(ids.free);
      expect(free.account.plan.id).toBe('free');
      expect(await getTestDb().collection('billingAccounts').countDocuments({ ownerId: new ObjectId(ids.deletedPro) })).toBe(0);
    });

    it('should keep the listed legacy admin as an env admin', async () => {
      await migrateBilling(getTestDb(), options());

      const keeper = await getTestDb().collection('users').findOne({ _id: new ObjectId(ids.keeper) });
      expect(keeper).toMatchObject({ roles: ['admin'], roleSource: 'env' });
    });

    it('should demote every other legacy admin and unset the legacy role', async () => {
      await migrateBilling(getTestDb(), options());

      const demoted = await getTestDb()
        .collection('users')
        .find({ _id: { $in: [new ObjectId(ids.oldAdmin), new ObjectId(ids.oldProAdmin)] } })
        .toArray();
      expect(demoted.map((user) => [user.roles, user.role, user.roleSource])).toEqual([
        [[], undefined, undefined],
        [[], undefined, undefined],
      ]);
    });

    it('should leave a user who was not an admin without roles', async () => {
      await migrateBilling(getTestDb(), options());

      const user = await getTestDb().collection('users').findOne({ _id: new ObjectId(ids.free) });
      expect(user?.roles).toBeUndefined();
    });

    it('should audit each role change under the migration actor', async () => {
      await migrateBilling(getTestDb(), options());

      const rows = await getTestDb().collection('adminAudit').find({}).toArray();
      expect(rows.map((row) => [row.action, row.actorId]).sort()).toEqual([
        ['roles.migration.demote', 'system:migration'],
        ['roles.migration.demote', 'system:migration'],
        ['roles.migration.promote', 'system:migration'],
      ]);
    });

    it('should still give a demoted legacy admin who was on tier pro the Pro plan', async () => {
      await migrateBilling(getTestDb(), options());

      const { account } = await billing.getAccountDetail(ids.oldProAdmin);
      expect(account.plan.id).toBe('pro');
    });

    it('should convert the migration day credit the spend has not used into one lot at $1 = 30 minutes', async () => {
      await migrateBilling(getTestDb(), options());

      const detail = await billing.getAccountDetail(ids.creditor);
      expect(detail.lots).toEqual([
        expect.objectContaining({ minutesGranted: 120, expiresAt: IN_A_YEAR }),
      ]);
      expect(detail.ledger[0]).toMatchObject({ reason: `migration: unconsumed credit from ${DAY}`, actorId: 'system:migration' });
    });

    it('should drop credit that the day spend already used up', async () => {
      await migrateBilling(getTestDb(), options());

      expect((await billing.getAccountDetail(ids.spender)).lots).toEqual([]);
    });

    it('should ignore a charge and credit dated another day', async () => {
      await migrateBilling(getTestDb(), options());

      expect((await billing.getAccountDetail(ids.charged)).lots).toEqual([]);
    });

    it('should give the demo account 300 minutes a month', async () => {
      await migrateBilling(getTestDb(), options());

      const { recurringGrants } = await billing.getAccountDetail(ids.demo);
      expect(recurringGrants).toEqual([expect.objectContaining({ minutes: 300, every: 'month', active: true })]);
    });

    it('should give each eval account 3,000 minutes a month', async () => {
      await migrateBilling(getTestDb(), options());

      const grants = [(await billing.getAccountDetail(ids.evalA)).recurringGrants, (await billing.getAccountDetail(ids.evalB)).recurringGrants];
      expect(grants.map((rows) => rows.map((grant) => grant.minutes))).toEqual([[3000], [3000]]);
    });

    it('should leave every account consistent with its ledger', async () => {
      await migrateBilling(getTestDb(), options());

      const accounts = await getTestDb().collection('billingAccounts').find({}).toArray();
      const drifts = await Promise.all(accounts.map(async (account) => (await billing.verifyAccount(account.ownerId.toHexString())).drift));
      expect(drifts.flat()).toEqual([]);
    });
  });

  describe('a second run', () => {
    it('should plan nothing', async () => {
      await migrateBilling(getTestDb(), options());

      const second = await migrateBilling(getTestDb(), options());

      expect(emptyCounts(second)).toEqual([0, 0, 0, 0, 0, 0, 0, 0]);
    });

    it('should change nothing in the database', async () => {
      await migrateBilling(getTestDb(), options());
      const afterFirst = await snapshot();

      await migrateBilling(getTestDb(), options());

      expect(await snapshot()).toEqual(afterFirst);
    });

    it('should change nothing when it runs later in the day', async () => {
      await migrateBilling(getTestDb(), options());
      const afterFirst = await snapshot();

      await migrateBilling(getTestDb(), options({ now: new Date(NOW.getTime() + 3 * 60 * 60 * 1000) }));

      expect(await snapshot()).toEqual(afterFirst);
    });

    it('should finish a run that stopped before its last step', async () => {
      await migrateBilling(getTestDb(), options({ evalEmails: [] }));

      const second = await migrateBilling(getTestDb(), options());

      expect(emptyCounts(second)).toEqual([0, 0, 0, 0, 0, 0, 0, 2]);
    });
  });

  describe('an account that is already on Pro', () => {
    it('should be left as it is and reported', async () => {
      const ctx = { actorId: new ObjectId().toHexString(), reason: 'set by hand', auditId: new ObjectId().toHexString() };
      await billing.setPlan(ids.pro, { planId: 'pro', periodEnd: new Date('2027-01-01T00:00:00Z') }, ctx);

      const plan = await migrateBilling(getTestDb(), options());

      expect(plan.skipped).toContainEqual({ email: 'pro@example.com', why: 'already on a Pro plan; left as it is' });
      expect((await billing.getAccountDetail(ids.pro)).account.plan.periodEnd).toBe('2027-01-01T00:00:00.000Z');
    });
  });

  describe('when the result would leave no admin', () => {
    async function dropKeeper(): Promise<void> {
      await getTestDb().collection('users').updateOne({ _id: new ObjectId(ids.keeper) }, { $unset: { role: '' } });
    }

    it.each([true, false])('should refuse with dryRun=%s', async (dryRun) => {
      await dropKeeper();

      await expect(migrateBilling(getTestDb(), options({ dryRun }))).rejects.toBeInstanceOf(NoAdminsRemainError);
    });

    it('should write nothing when it refuses', async () => {
      await dropKeeper();
      const before = await snapshot();

      await migrateBilling(getTestDb(), options()).catch(() => undefined);

      expect(await snapshot()).toEqual(before);
    });

    it('should refuse an empty admin list', async () => {
      await expect(migrateBilling(getTestDb(), options({ adminEmails: [] }))).rejects.toThrow('--admin-emails');
    });

    it('should count a soft-deleted admin as no admin', async () => {
      await getTestDb().collection('users').updateOne({ _id: new ObjectId(ids.keeper) }, { $set: { deletedAt: new Date() } });

      await expect(migrateBilling(getTestDb(), options())).rejects.toBeInstanceOf(NoAdminsRemainError);
    });
  });
});

describe('unconsumedMinutes', () => {
  it.each([
    [5, 1, 120],
    [2, 5, 0],
    [2, 2, 0],
    [0.1, 0, 3],
    [1.1, 0, 33],
    [2.3, 0, 69],
    [1, -4, 30],
    [0.03, 0, 0],
  ])('should turn $%d of credit after $%d of spend into %d minutes', (credit, spend, minutes) => {
    expect(unconsumedMinutes(credit, spend)).toBe(minutes);
  });
});

describe('auditIdFor', () => {
  it('should be a 24-char hex id that depends only on the user and the step', () => {
    expect(auditIdFor('6a7b2f6abacdb32871507996', 'plan')).toMatch(/^[0-9a-f]{24}$/);
    expect(auditIdFor('6a7b2f6abacdb32871507996', 'plan')).toBe(auditIdFor('6a7b2f6abacdb32871507996', 'plan'));
  });

  it('should differ per step and per user', () => {
    const ids = new Set([
      auditIdFor('6a7b2f6abacdb32871507996', 'plan'),
      auditIdFor('6a7b2f6abacdb32871507996', 'team-lot'),
      auditIdFor('6a7b2f6abacdb32871507997', 'plan'),
    ]);
    expect(ids.size).toBe(3);
  });
});

describe('looksLikeProd', () => {
  it.each([
    ['mongodb://localhost:27017/video-insight-engine?directConnection=true', false],
    ['mongodb://127.0.0.1:27017/x', false],
    ['mongodb://vie-mongodb:27017/video-insight-engine', false],
    ['mongodb://vieadmin:secret@vie-mongodb:27017/video-insight-engine?authSource=admin', true],
    ['mongodb://db.example.com:27017/x', true],
    ['mongodb+srv://cluster0.example.mongodb.net/x', true],
    ['not a uri', true],
  ])('should say %s is prod: %s', (uri, prod) => {
    expect(looksLikeProd(uri)).toBe(prod);
  });
});

describe('parseCli', () => {
  const NOW_CLI = new Date('2026-11-02T10:00:00Z');
  const parse = (argv: string[], env: NodeJS.ProcessEnv = {}) => parseCli(argv, env, NOW_CLI);

  it('should refuse neither --dry-run nor --run, and both', () => {
    expect([parse([]), parse(['--dry-run', '--run'])]).toEqual([null, null]);
  });

  it('should default the day to today in UTC and the admins to ADMIN_EMAILS', () => {
    const cli = parse(['--dry-run'], { ADMIN_EMAILS: 'A@example.com, b@example.com' });

    expect(cli?.options).toMatchObject({ dryRun: true, day: DAY, adminEmails: ['a@example.com', 'b@example.com'] });
  });

  it('should take the demo account from DEMO_USER_EMAIL and the eval accounts from the flag', () => {
    const cli = parse(['--run', '--eval-emails', 'e1@example.com,e2@example.com'], { DEMO_USER_EMAIL: 'Demo@Example.com' });

    expect(cli?.options).toMatchObject({ dryRun: false, demoEmail: 'demo@example.com', evalEmails: ['e1@example.com', 'e2@example.com'] });
  });

  it('should reject a malformed day and an unknown flag', () => {
    expect([parse(['--run', '--day', '02/11/2026']), parse(['--run', '--wat'])]).toEqual([null, null]);
  });

  it('should read an empty --eval-emails, as the runbook passes when there are none, as no accounts', () => {
    expect(parse(['--run', '--eval-emails', ''])?.options.evalEmails).toEqual([]);
  });

  it('should carry --yes-prod', () => {
    expect(parse(['--run', '--yes-prod'])?.yesProd).toBe(true);
  });
});
