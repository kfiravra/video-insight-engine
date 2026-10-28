import { describe, it, expect, beforeAll, beforeEach, vi } from 'vitest';
import { ObjectId } from 'mongodb';
import type { Db } from 'mongodb';
import type { FastifyBaseLogger } from 'fastify';
import { getTestDb, mockLogger } from '../../api/src/test/setup.js';
import { createBillingIndexes } from '../../api/src/plugins/mongodb.js';
import { BillingRepository } from '../../api/src/repositories/billing.repository.js';
import { UserRepository } from '../../api/src/repositories/user.repository.js';
import { BillingService } from '../../api/src/services/billing/billing.service.js';
import { NoAdminsRemainError, StandaloneMongoError, auditIdFor, migrateBilling } from '../migrate-billing.js';
import { DAY, DEMO, EVAL_A, EVAL_B, IN_A_YEAR, KEEPER, NOW, emptyCounts, options, seedFixture, snapshot } from './migrate-billing.fixtures.js';
import type { Fixture } from './migrate-billing.fixtures.js';

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

      expect(emptyCounts(plan)).toEqual([12, 2, 1, 1, 3, 1, 1, 1, 2, 3]);
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

      const rows = await getTestDb().collection('adminAudit').find({ action: /^roles\./ }).toArray();
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

    it('should put the demo account on manual Pro for 12 months, beside its monthly grant', async () => {
      await migrateBilling(getTestDb(), options());

      const detail = await billing.getAccountDetail(ids.demo);
      expect(detail.account.plan).toMatchObject({ id: 'pro', source: 'manual', periodEnd: IN_A_YEAR });
      expect(detail.recurringGrants).toHaveLength(1);
    });

    it('should word the demo plan "migration: demo account" with the migration actor', async () => {
      await migrateBilling(getTestDb(), options());

      const { ledger } = await billing.getAccountDetail(ids.demo);
      expect(ledger).toContainEqual(
        expect.objectContaining({ type: 'grant', bucket: 'plan', minutes: 600, reason: 'migration: demo account', actorId: 'system:migration' }),
      );
    });

    it('should leave every account consistent with its ledger', async () => {
      await migrateBilling(getTestDb(), options());

      const accounts = await getTestDb().collection('billingAccounts').find({}).toArray();
      const drifts = await Promise.all(accounts.map(async (account) => (await billing.verifyAccount(account.ownerId.toHexString())).drift));
      expect(drifts.flat()).toEqual([]);
    });
  });

  describe('adminAudit rows', () => {
    const billingRows = () => getTestDb().collection('adminAudit').find({ action: /^billing\./ }).toArray();

    it('should write one row for every plan, lot and recurring grant step', async () => {
      await migrateBilling(getTestDb(), options());

      const actions = (await billingRows()).map((row) => row.action).sort();
      expect(actions).toEqual([
        ...Array(2).fill('billing.credits.grant'),
        ...Array(4).fill('billing.plan.set'),
        ...Array(3).fill('billing.recurring.create'),
      ].sort());
    });

    it('should sign every row with the migration actor and the migrated user as target', async () => {
      await migrateBilling(getTestDb(), options());

      const rows = await billingRows();
      expect(new Set(rows.map((row) => row.actorId))).toEqual(new Set(['system:migration']));
      expect(rows.find((row) => row.action === 'billing.plan.set' && row.targetId === ids.pro)).toMatchObject({
        targetType: 'user',
        reason: 'migration: pro tier',
      });
    });

    it('should use the audit id its ledger entry carries as the row id', async () => {
      await migrateBilling(getTestDb(), options());

      const lotEntry = await getTestDb().collection('ledgerEntries').findOne({ 'source.ref': auditIdFor(ids.team, 'team-lot') });
      const row = await getTestDb().collection('adminAudit').findOne({ _id: lotEntry?.auditId });
      expect(row).toMatchObject({ action: 'billing.credits.grant', targetId: ids.team, reason: 'migration: team tier' });
    });

    it('should write none on a second run', async () => {
      await migrateBilling(getTestDb(), options());
      const before = await getTestDb().collection('adminAudit').countDocuments();

      await migrateBilling(getTestDb(), options());

      expect(await getTestDb().collection('adminAudit').countDocuments()).toBe(before);
    });

    it('should write none on a dry run', async () => {
      await migrateBilling(getTestDb(), options({ dryRun: true }));

      expect(await getTestDb().collection('adminAudit').countDocuments()).toBe(0);
    });
  });

  describe('recurring grants', () => {
    const handMadeGrant = (userId: string, active: boolean) =>
      billing.createRecurringGrant(
        userId,
        { minutes: 500, every: 'month', active },
        { actorId: new ObjectId().toHexString(), reason: 'set by hand', auditId: new ObjectId().toHexString() },
      );

    it('should not add a second grant to an account that already has an active one', async () => {
      await handMadeGrant(ids.demo, true);

      await migrateBilling(getTestDb(), options());

      expect((await billing.getAccountDetail(ids.demo)).recurringGrants.map((grant) => grant.minutes)).toEqual([500]);
    });

    it('should report the active grant it left alone', async () => {
      await handMadeGrant(ids.demo, true);

      const plan = await migrateBilling(getTestDb(), options({ dryRun: true }));

      expect(plan.skipped).toContainEqual({
        email: DEMO,
        why: 'recurring-demo: already has an active recurring grant of 500 minutes/month; not added',
      });
    });

    it('should still add its grant next to one that is switched off', async () => {
      await handMadeGrant(ids.demo, false);

      await migrateBilling(getTestDb(), options());

      expect((await billing.getAccountDetail(ids.demo)).recurringGrants.map((grant) => grant.minutes).sort()).toEqual([300, 500]);
    });

    it('should give an email on both the demo and the eval list one grant', async () => {
      const plan = await migrateBilling(getTestDb(), options({ evalEmails: [EVAL_A, DEMO] }));

      expect(plan.evalGrants.map((grant) => grant.email)).toEqual([EVAL_A]);
      expect((await billing.getAccountDetail(ids.demo)).recurringGrants).toHaveLength(1);
    });

    it('should say the email on both lists was covered by the demo step', async () => {
      const plan = await migrateBilling(getTestDb(), options({ dryRun: true, evalEmails: [DEMO] }));

      expect(plan.skipped).toContainEqual({
        email: DEMO,
        why: 'recurring-eval: another recurring step of this run already covers it; not added',
      });
    });
  });

  describe('on a standalone Mongo', () => {
    /** A server with no replica set: hello has no setName, and nothing else may be touched. */
    function standaloneDb(): { db: Db; collection: ReturnType<typeof vi.fn> } {
      const collection = vi.fn();
      const db = { admin: () => ({ command: async () => ({ ok: 1, isWritablePrimary: true }) }), collection };
      return { db: db as unknown as Db, collection };
    }

    it.each([true, false])('should refuse with dryRun=%s before reading a single collection', async (dryRun) => {
      const { db, collection } = standaloneDb();

      await expect(migrateBilling(db, options({ dryRun }))).rejects.toBeInstanceOf(StandaloneMongoError);
      expect(collection).not.toHaveBeenCalled();
    });

    it('should name the fix in the message', async () => {
      await expect(migrateBilling(standaloneDb().db, options())).rejects.toThrow('Convert Mongo to a replica set');
    });
  });

  describe('a second run', () => {
    it('should plan nothing', async () => {
      await migrateBilling(getTestDb(), options());

      const second = await migrateBilling(getTestDb(), options());

      expect(emptyCounts(second)).toEqual([0, 0, 0, 0, 0, 0, 0, 0, 0, 0]);
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

      expect(emptyCounts(second)).toEqual([0, 0, 0, 0, 0, 0, 0, 0, 2, 0]);
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
