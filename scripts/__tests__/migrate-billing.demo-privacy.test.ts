import { describe, it, expect, beforeAll, beforeEach } from 'vitest';
import { ObjectId } from 'mongodb';
import type { FastifyBaseLogger } from 'fastify';
import { getTestDb, mockLogger } from '../../api/src/test/setup.js';
import { createBillingIndexes } from '../../api/src/plugins/mongodb.js';
import { BillingRepository } from '../../api/src/repositories/billing.repository.js';
import { UserRepository } from '../../api/src/repositories/user.repository.js';
import { BillingService } from '../../api/src/services/billing/billing.service.js';
import { migrateBilling } from '../migrate-billing.js';
import { auditIdFor } from '../migrate-billing-plan.js';
import { DEMO, IN_A_YEAR, NOW, options, seedFixture } from './migrate-billing.fixtures.js';
import type { Fixture } from './migrate-billing.fixtures.js';

describe('migrate-billing: the demo account plan and the assistant prompt previews', () => {
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

  describe('the demo account plan', () => {
    const setByHand = (userId: string) =>
      billing.setPlan(
        userId,
        { planId: 'pro', periodEnd: new Date('2027-01-01T00:00:00Z') },
        { actorId: new ObjectId().toHexString(), reason: 'set by hand', auditId: new ObjectId().toHexString() },
      );

    it('should plan it as its own step', async () => {
      const plan = await migrateBilling(getTestDb(), options({ dryRun: true }));

      expect(plan.demoPlan).toEqual([{ userId: ids.demo, email: DEMO }]);
    });

    it('should plan nothing when there is no demo account', async () => {
      const plan = await migrateBilling(getTestDb(), options({ dryRun: true, demoEmail: null }));

      expect(plan.demoPlan).toEqual([]);
    });

    it('should leave a plan set by hand as it is, say so, and still add the monthly grant', async () => {
      await setByHand(ids.demo);

      const plan = await migrateBilling(getTestDb(), options());

      const detail = await billing.getAccountDetail(ids.demo);
      expect([plan.demoPlan, plan.skipped]).toEqual([
        [],
        expect.arrayContaining([{ email: DEMO, why: 'plan-demo: already on a Pro plan; left as it is' }]),
      ]);
      expect([detail.account.plan.periodEnd, detail.recurringGrants.length]).toEqual(['2027-01-01T00:00:00.000Z', 1]);
    });

    it('should not set the plan twice for a demo account that is also on tier pro', async () => {
      await getTestDb().collection('users').updateOne({ _id: new ObjectId(ids.demo) }, { $set: { tier: 'pro' } });

      const plan = await migrateBilling(getTestDb(), options());

      const { ledger } = await billing.getAccountDetail(ids.demo);
      expect(plan.demoPlan).toEqual([]);
      expect(ledger.filter((entry) => entry.type === 'grant' && entry.bucket === 'plan')).toHaveLength(1);
    });

    it('should say the tier step of the same run already covers it', async () => {
      await getTestDb().collection('users').updateOne({ _id: new ObjectId(ids.demo) }, { $set: { tier: 'pro' } });

      const plan = await migrateBilling(getTestDb(), options({ dryRun: true }));

      expect(plan.skipped).toContainEqual({
        email: DEMO,
        why: 'plan-demo: the tier step of this run already gives it Pro; not added',
      });
    });

    it('should stay quiet about a demo account the tier step already moved on an earlier run', async () => {
      await getTestDb().collection('users').updateOne({ _id: new ObjectId(ids.demo) }, { $set: { tier: 'pro' } });
      await migrateBilling(getTestDb(), options());

      const second = await migrateBilling(getTestDb(), options({ dryRun: true }));

      expect(second.skipped.filter((entry) => entry.email === DEMO)).toEqual([]);
    });
  });

  describe('assistant prompt previews', () => {
    const assistantRows = () => getTestDb().collection('llm_usage').find({ service: 'assistant' }).sort({ cost_usd: 1 }).toArray();

    it('should count the assistant rows that still hold prompt text on a dry run', async () => {
      const plan = await migrateBilling(getTestDb(), options({ dryRun: true }));

      expect(plan.assistantPromptPreviews).toBe(3);
    });

    it('should leave the stored prompt text alone on a dry run', async () => {
      await migrateBilling(getTestDb(), options({ dryRun: true }));

      expect((await assistantRows()).map((row) => row.prompt_preview)).toEqual([
        'what did she say about my diagnosis?',
        'which of my videos mention taxes?',
        'organize my folders',
        '',
      ]);
    });

    it('should unset prompt_preview on every assistant row that held text', async () => {
      await migrateBilling(getTestDb(), options());

      const withText = (await assistantRows()).filter((row) => row.cost_usd < 0.04);
      expect(withText.map((row) => row.prompt_preview)).toEqual([undefined, undefined, undefined]);
    });

    it('should keep cost, feature and prompt_hash on the rows it clears', async () => {
      await migrateBilling(getTestDb(), options());

      const [first] = await assistantRows();
      expect(first).toMatchObject({ feature: 'rag_chat', prompt_hash: 'h1', cost_usd: 0.01 });
    });

    it('should leave the summarizer rows as they are', async () => {
      await migrateBilling(getTestDb(), options());

      const row = await getTestDb().collection('llm_usage').findOne({ service: 'summarizer' });
      expect(row?.prompt_preview).toBe('Summarize this transcript');
    });

    it('should find nothing to clear on a second run', async () => {
      await migrateBilling(getTestDb(), options());

      const second = await migrateBilling(getTestDb(), options({ dryRun: true }));

      expect(second.assistantPromptPreviews).toBe(0);
    });

    it('should report nothing to do once the previews are cleared and every other step is done', async () => {
      await migrateBilling(getTestDb(), options());
      const afterFirst = await getTestDb().collection('llm_usage').find({}).sort({ _id: 1 }).toArray();

      await migrateBilling(getTestDb(), options());

      expect(await getTestDb().collection('llm_usage').find({}).sort({ _id: 1 }).toArray()).toEqual(afterFirst);
    });
  });

  describe('adminAudit row of the demo plan', () => {
    it('should write a billing.plan.set row for the demo plan with its own reason', async () => {
      await migrateBilling(getTestDb(), options());

      const entry = await getTestDb().collection('ledgerEntries').findOne({ 'source.ref': auditIdFor(ids.demo, 'plan-demo') });
      const row = await getTestDb().collection('adminAudit').findOne({ _id: entry?.auditId });
      expect(row).toMatchObject({ action: 'billing.plan.set', targetId: ids.demo, reason: 'migration: demo account', actorId: 'system:migration' });
    });
  });
});
