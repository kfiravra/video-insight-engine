/**
 * Q-ACCT: an account that getOrCreateAccount opened before the billing
 * migration ran (a submit between the billing deploy and the migration) is
 * migrated in place: the accounts step skips it, the tier and credit steps
 * apply on top of it, and its free-video window (`recentFreeVideoAt`) survives.
 * The migration is scripts/migrate-billing.ts, run as the runbook runs it.
 */
import { describe, it, expect, beforeAll, beforeEach } from 'vitest';
import { ObjectId } from 'mongodb';
import { getTestDb } from '../../../test/setup.js';
import { createBillingIndexes } from '../../../plugins/mongodb.js';
import { migrateBilling, utcDayKey } from '../../../../../scripts/migrate-billing.js';
import { insertUser, makeBillingHarness, makeEnforcement } from './billing-fixtures.js';
import type { MigrateOptions } from '../../../../../scripts/migrate-billing.js';
import type { BillingHarness } from './billing-fixtures.js';

const KEEPER = 'keeper@example.com';

describe('the billing migration on an account opened before it ran (Q-ACCT)', () => {
  let h: BillingHarness;
  let userId: string;
  let now: Date;

  const options = (): MigrateOptions => ({
    dryRun: false, adminEmails: [KEEPER], envAdminEmails: [KEEPER], demoEmail: null, evalEmails: [], day: utcDayKey(now), now,
  });
  const accountOf = () => getTestDb().collection('billingAccounts').findOne({ ownerId: new ObjectId(userId) });
  const setUser = (fields: Record<string, unknown>) => getTestDb().collection('users').updateOne({ _id: new ObjectId(userId) }, { $set: fields });

  beforeAll(async () => {
    await createBillingIndexes(getTestDb());
  });

  beforeEach(async () => {
    now = new Date();
    h = makeBillingHarness(getTestDb());
    // The migration refuses to leave no admin: a legacy admin on the keep-list.
    await getTestDb().collection('users').insertOne({ _id: new ObjectId(), email: KEEPER, role: 'admin', createdAt: now, updatedAt: now });
    userId = await insertUser(getTestDb());
    // The free video, run and settled before the migration: this opens the account.
    await makeEnforcement(getTestDb(), h).admit({ requestId: 'pre-migration-run', userId, youtubeId: 'premigrate1', durationSec: 600 });
    await h.billing.settle('pre-migration-run');
  });

  it('should put a pre-migration pro user on Pro in the same account, keeping its free-video window', async () => {
    await setUser({ tier: 'pro' });
    const freeVideoAt = (await accountOf())?.buckets.free.recentFreeVideoAt;

    const plan = await migrateBilling(getTestDb(), options());

    const after = await accountOf();
    expect(freeVideoAt).toHaveLength(1);
    expect([plan.accounts.some((step) => step.userId === userId), after?.plan.id, after?.buckets.free.recentFreeVideoAt]).toEqual([false, 'pro', freeVideoAt]);
    expect(await getTestDb().collection('billingAccounts').countDocuments({ ownerId: new ObjectId(userId) })).toBe(1);
  });

  it('should pay a video the used free video cannot cover from the migrated Pro plan', async () => {
    await setUser({ tier: 'pro' });
    await migrateBilling(getTestDb(), options());

    const hold = await makeEnforcement(getTestDb(), h).admit({ requestId: 'post-migration-run', userId, youtubeId: 'postmigrat1', durationSec: 45 * 60 });

    expect(hold.split).toMatchObject({ free: 0, plan: 45 });
    expect((await h.billing.verifyAccount(userId)).drift).toEqual([]);
  });

  it('should turn the day\'s dollar credit into a lot while the free video stays used', async () => {
    await getTestDb().collection('userCostAdjustments').insertOne({
      userId: new ObjectId(userId), date: utcDayKey(now), amountUsd: -2, reason: 'fixture', adminId: new ObjectId(), createdAt: now,
    });

    await migrateBilling(getTestDb(), options());

    const summary = await h.billing.summary(userId);
    expect([summary.free.available, summary.credits.available, summary.blocked]).toEqual([false, 60, false]);
  });
});
