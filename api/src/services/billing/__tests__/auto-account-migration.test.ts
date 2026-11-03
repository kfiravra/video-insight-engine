/**
 * D4 / D10: an account written by the minutes ledger, read and written by the
 * token ledger before the token migration (scripts/migrate-billing-tokens.ts)
 * has run — the window between the deploy and the migration. Its token fields
 * read as empty (0 tokens, no debt, its minute hold as a 0-token hold), it
 * gets no signup grant lazily (the migration writes it), minute lots and
 * minute recurring grants stay invisible, and a token write keeps every
 * minute field the migration converts.
 */
import { describe, it, expect, beforeAll, beforeEach } from 'vitest';
import { ObjectId } from 'mongodb';
import { getTestDb } from '../../../test/setup.js';
import { createBillingIndexes } from '../../../plugins/mongodb.js';
import { InsufficientTokensError } from '../../../utils/errors.js';
import { adminCtx, insertUser, makeBillingHarness } from './billing-fixtures.js';
import type { BillingHarness } from './billing-fixtures.js';

const MINUTE_ERA = new Date('2026-10-30T00:00:00Z');

describe('an account written before the token migration (D4, D10)', () => {
  let h: BillingHarness;
  let userId: string;
  let accountId: ObjectId;
  let minuteLotId: ObjectId;

  const db = () => getTestDb();
  const storedAccount = () => db().collection('billingAccounts').findOne({ _id: accountId });

  beforeAll(async () => {
    await createBillingIndexes(db());
  });

  beforeEach(async () => {
    h = makeBillingHarness(db());
    userId = await insertUser(db());
    const owner = new ObjectId(userId);
    accountId = new ObjectId();
    minuteLotId = new ObjectId();
    await db().collection('billingAccounts').insertOne({
      _id: accountId,
      ownerType: 'user',
      ownerId: owner,
      plan: { id: 'free', status: 'active', source: null, periodStart: null, periodEnd: null },
      buckets: {
        free: { recentFreeVideoAt: [MINUTE_ERA] },
        plan: { cycleStart: null, cycleEnd: null, minutesGranted: 0, minutesUsed: 0 },
        credit: { minutesAvailable: 150 },
      },
      holds: [{
        requestId: 'minute-run', userId: owner, youtubeId: 'premigrate1', durationSec: 600, minutes: 10,
        split: { free: 0, plan: 0, credit: 10, lots: [{ lotId: minuteLotId, minutes: 10 }] }, planCycleStart: null, createdAt: MINUTE_ERA,
      }],
      caps: { freeVideosPerDay: 1 },
      version: 3,
      createdAt: MINUTE_ERA,
      updatedAt: MINUTE_ERA,
    });
    await db().collection('creditLots').insertOne({
      _id: minuteLotId, accountId, minutesGranted: 160, minutesRemaining: 150,
      source: { kind: 'admin', ref: 'legacy-audit' }, expiresAt: null, createdAt: MINUTE_ERA,
    });
    const entry = { accountId, userId: owner, createdAt: MINUTE_ERA };
    await db().collection('ledgerEntries').insertMany([
      { ...entry, type: 'grant', bucket: 'credit', minutes: 160, lotId: minuteLotId, source: { kind: 'admin', ref: 'legacy-audit' }, idempotencyKey: 'admin:legacy-audit' },
      { ...entry, type: 'hold', bucket: 'credit', minutes: -10, lotId: minuteLotId, source: { kind: 'run', ref: 'minute-run' }, idempotencyKey: `hold:minute-run:lot:${minuteLotId.toHexString()}` },
    ]);
    await db().collection('recurringGrants').insertOne({
      accountId, minutes: 300, every: 'month', active: true, nextRunAt: MINUTE_ERA, lastRunAt: null,
      actorId: 'system:test', auditId: new ObjectId(), reason: 'demo', createdAt: MINUTE_ERA, updatedAt: MINUTE_ERA, deletedAt: null,
    });
  });

  it('should read as 0 tokens with its minute hold as a 0-token hold', async () => {
    const summary = await h.billing.summary(userId);

    expect(summary).toEqual({
      plan: { id: 'free', periodEnd: null },
      balance: 0,
      planTokens: null,
      credits: { available: 0, lots: [] },
      debt: 0,
      holds: [{ youtubeId: 'premigrate1', tokens: 0 }],
      maxVideoMinutes: 180,
      blocked: false,
      signupGrantTokens: 0,
    });
  });

  it('should never get the signup grant lazily: the migration writes it', async () => {
    await h.billing.ensureAccount(userId);
    await h.billing.summary(userId);

    expect(await db().collection('ledgerEntries').countDocuments({ idempotencyKey: `grant:signup:${userId}` })).toBe(0);
    expect(await db().collection('billingAccounts').countDocuments({ ownerId: new ObjectId(userId) })).toBe(1);
  });

  it('should hide its minute lots and minute recurring grants from the token views', async () => {
    const detail = await h.billing.getAccountDetail(userId);

    expect([detail.lots, detail.recurringGrants]).toEqual([[], []]);
    expect(detail.ledger.map((e) => [e.unit, e.amount])).toEqual([['minutes', -10], ['minutes', 160]]);
  });

  it('should refuse runs and chats until the migration converts its minutes', async () => {
    await expect(h.billing.admitExecution(userId)).rejects.toBeInstanceOf(InsufficientTokensError);
    await expect(h.gate.admit({ requestId: 'run-1', userId, youtubeId: 'dQw4w9WgXcQ', durationSec: 600 }))
      .resolves.toMatchObject({ admitted: false, rejection: { reason: 'insufficient', needed: 840, available: 0 } });
  });

  it('should keep every minute field through a token write', async () => {
    await h.billing.grantLot(userId, { tokens: 500 }, adminCtx());

    const stored = await storedAccount();
    expect(stored?.buckets).toMatchObject({
      free: { recentFreeVideoAt: [MINUTE_ERA] },
      plan: { minutesGranted: 0, minutesUsed: 0, tokensGranted: 0, tokensUsed: 0 },
      credit: { minutesAvailable: 150, tokensAvailable: 500 },
      debt: { tokens: 0 },
    });
    expect(stored?.caps).toEqual({ freeVideosPerDay: 1 });
    expect(stored?.holds).toEqual([expect.objectContaining({ requestId: 'minute-run', minutes: 10, tokens: 0 })]);
    expect(stored?.version).toBe(4);
    expect(await db().collection('creditLots').findOne({ _id: minuteLotId })).toMatchObject({ minutesRemaining: 150 });
    expect((await h.billing.summary(userId)).balance).toBe(500);
  });

  it('should keep the token projection exact next to the minute history', async () => {
    expect((await h.billing.verifyAccount(userId)).drift).toEqual([]);

    await h.billing.grantLot(userId, { tokens: 500 }, adminCtx());

    expect((await h.billing.verifyAccount(userId)).drift).toEqual([]);
  });

  it('should release its minute hold on the run-cost settle, never charge it as an overrun', async () => {
    const outcome = await h.billing.settleActual('minute-run', 0.31);

    expect(outcome).toEqual({ requestId: 'minute-run', status: 'released', tokens: 0 });
    const summary = await h.billing.summary(userId);
    expect([summary.debt, summary.balance, summary.holds]).toEqual([0, 0, []]);
    expect((await h.billing.verifyAccount(userId)).ok).toBe(true);
  });
});
