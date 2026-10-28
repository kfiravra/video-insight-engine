import { ObjectId } from 'mongodb';
import { getTestDb } from '../../api/src/test/setup.js';
import type { MigrateOptions, MigrationPlan } from '../migrate-billing.js';

// Shared by the migrate-billing test files: a small host as the dollar gate left it.

export const NOW = new Date('2026-11-02T10:00:00Z');
export const DAY = '2026-11-02';
export const YESTERDAY = '2026-11-01';
export const IN_A_YEAR = '2027-11-02T10:00:00.000Z';
export const KEEPER = 'kfir@example.com';
export const DEMO = 'demo@example.com';
export const EVAL_A = 'eval-a@example.com';
export const EVAL_B = 'eval-b@example.com';

export const options = (overrides: Partial<MigrateOptions> = {}): MigrateOptions => ({
  dryRun: false,
  adminEmails: [KEEPER],
  envAdminEmails: [KEEPER],
  demoEmail: DEMO,
  evalEmails: [EVAL_A, EVAL_B],
  day: DAY,
  now: NOW,
  ...overrides,
});

export async function seedUser(email: string, extra: Record<string, unknown> = {}): Promise<string> {
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

export async function seedAdjustment(userId: string, date: string, amountUsd: number): Promise<void> {
  await getTestDb().collection('userCostAdjustments').insertOne({
    userId: new ObjectId(userId),
    date,
    amountUsd,
    reason: 'fixture',
    adminId: new ObjectId(),
    createdAt: new Date('2026-11-02T08:00:00Z'),
  });
}

export async function seedSpend(userId: string, date: string, totalCostUsd: number): Promise<void> {
  await getTestDb().collection('userCosts').insertOne({
    userId: new ObjectId(userId),
    date,
    totalCostUsd,
    videoCount: 1,
    creditAdjustmentUsd: 0,
    updatedAt: new Date(),
  });
}

export interface Fixture {
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

/** Chat prompts the assistant stored before docs/PRIVACY.md said it never would, beside rows that must survive. */
export async function seedUsage(): Promise<void> {
  await getTestDb().collection('llm_usage').insertMany([
    { service: 'assistant', feature: 'rag_chat', prompt_preview: 'what did she say about my diagnosis?', prompt_hash: 'h1', cost_usd: 0.01 },
    { service: 'assistant', feature: 'library_chat', prompt_preview: 'which of my videos mention taxes?', prompt_hash: 'h2', cost_usd: 0.02 },
    { service: 'assistant', feature: 'actions', prompt_preview: 'organize my folders', prompt_hash: 'h3', cost_usd: 0.03 },
    { service: 'assistant', feature: 'rag_chat', prompt_preview: '', prompt_hash: '', cost_usd: 0.04 },
    { service: 'summarizer', feature: 'extraction', prompt_preview: 'Summarize this transcript', prompt_hash: 'h4', cost_usd: 0.5 },
  ]);
}

/** A small host as the dollar gate left it: legacy admins, tiers, and migration-day credit. */
export async function seedFixture(): Promise<Fixture> {
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
  await seedUsage();
  return fixture;
}

/** Every document of every collection, so "writes nothing" and "no-op" mean the whole database. */
export async function snapshot(): Promise<Record<string, unknown[]>> {
  const db = getTestDb();
  const names = (await db.listCollections().toArray()).map((collection) => collection.name).sort();
  const entries = await Promise.all(
    names.map(async (name) => [name, await db.collection(name).find({}).sort({ _id: 1 }).toArray()] as const),
  );
  return Object.fromEntries(entries);
}

export function emptyCounts(plan: MigrationPlan): number[] {
  const lists = [
    plan.accounts, plan.pro, plan.team, plan.teamLots, plan.roles, plan.creditLots, plan.demoPlan, plan.demoGrants, plan.evalGrants,
  ];
  return [...lists.map((steps) => steps.length), plan.assistantPromptPreviews];
}
