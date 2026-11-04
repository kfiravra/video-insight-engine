import { describe, it, expect, beforeAll, beforeEach, vi } from 'vitest';
import { ObjectId } from 'mongodb';
import type { FastifyBaseLogger } from 'fastify';
import { getTestDb, mockLogger } from '../../../test/setup.js';
import { createBillingIndexes } from '../../../plugins/mongodb.js';
import { BillingRepository } from '../../../repositories/billing.repository.js';
import { BillingMaintenanceRepository } from '../../../repositories/billing-maintenance.repository.js';
import { LlmAlertsRepository } from '../../../repositories/llm-alerts.repository.js';
import { LlmUsageRepository } from '../../../repositories/llm-usage.repository.js';
import { UserDeletionRepository } from '../../../repositories/user-deletion.repository.js';
import { UserRepository } from '../../../repositories/user.repository.js';
import { UserDeletionService } from '../../user-deletion.service.js';
import { BillingService } from '../billing.service.js';
import { BillingRepairService } from '../billing-repair.service.js';
import { singleProcessRepairLock } from '../repair-lock.js';
import { DAY_MS, HOUR_MS, insertUser, makeClock } from './billing-fixtures.js';
import type { BillingRepairDeps, RepairReport } from '../billing-repair.service.js';
import type { RepairLock } from '../repair-lock.js';
import type { TestClock } from './billing-fixtures.js';

const YOUTUBE_ID = 'dQw4w9WgXcQ';
const logger = mockLogger as unknown as FastifyBaseLogger;

describe('BillingRepairService (P1.6, real Mongo replica set)', () => {
  let clock: TestClock;
  let billing: BillingService;
  let deletions: UserDeletionService;
  let userId: string;

  const db = () => getTestDb();
  const ctx = () => ({ actorId: 'system:test', reason: 'test', auditId: new ObjectId().toHexString() });
  const repairWith = (overrides: Partial<BillingRepairDeps> = {}) =>
    new BillingRepairService({
      billing,
      maintenance: new BillingMaintenanceRepository(db()),
      llmUsage: new LlmUsageRepository(db()),
      alerts: new LlmAlertsRepository(db()),
      deletions,
      lock: singleProcessRepairLock,
      logger,
      holdMaxAgeHours: 3,
      clock: clock.now,
      ...overrides,
    });
  const run = async (overrides: Partial<BillingRepairDeps> = {}): Promise<RepairReport> => {
    const report = await repairWith(overrides).runOnce();
    if (report === 'locked') throw new Error('unexpectedly locked');
    return report;
  };
  const holdVideo = async (requestId: string, minutes: number) =>
    billing.hold(requestId, await billing.quote(userId, YOUTUBE_ID, minutes * 60));
  const stampRun = (requestId: string, status: string, updatedAt: Date = clock.now()) =>
    db().collection('videoSummaryCache').insertOne({ youtubeId: YOUTUBE_ID, requestId, status, updatedAt });
  const ledger = (filter: Record<string, unknown>) => db().collection('ledgerEntries').find(filter).toArray();
  const expectConsistent = async () => expect((await billing.verifyAccount(userId)).drift).toEqual([]);

  beforeAll(async () => {
    await createBillingIndexes(db());
  });

  beforeEach(async () => {
    vi.clearAllMocks();
    clock = makeClock('2026-11-01T10:00:00Z');
    billing = new BillingService(new BillingRepository(db()), new UserRepository(db()), logger, clock.now);
    deletions = new UserDeletionService(db(), new UserRepository(db()), new UserDeletionRepository(db()), logger, clock.now);
    userId = await insertUser(db());
  });

  describe('stale holds', () => {
    it('should settle a hold whose run completed but whose run cost never arrived, on its llm_usage cost', async () => {
      await holdVideo('req-1', 10);
      const { insertedId } = await stampRun('req-1', 'completed');
      await db().collection('llm_usage').insertMany([
        { request_id: 'req-1', video_summary_id: insertedId.toHexString(), cost_usd: 0.2, timestamp: clock.now() },
        // The same client request id reused on another video is not this run's spend (I4).
        { request_id: 'req-1', video_summary_id: 'another-video', cost_usd: 1.5, timestamp: clock.now() },
      ]);
      clock.advance(4 * HOUR_MS);

      const report = await run();

      expect([report.holdsSettled, report.holdsReleased]).toEqual([1, 0]);
      expect((await ledger({ type: 'debit' })).map((d) => [d.tokens, d.costUsd, d.mirrorsHold])).toEqual([[-600, 0.2, true]]);
      expect((await ledger({ type: 'release' })).map((d) => d.tokens)).toEqual([240]);
      expect((await billing.summary(userId)).balance).toBe(400);
      await expectConsistent();
    });

    it('should settle a stale completed run that cost more than its hold into debt', async () => {
      await holdVideo('req-1', 10);
      const { insertedId } = await stampRun('req-1', 'completed');
      await db().collection('llm_usage').insertOne({ request_id: 'req-1', video_summary_id: insertedId.toHexString(), cost_usd: 0.6, timestamp: clock.now() });
      clock.advance(4 * HOUR_MS);

      expect((await run()).holdsSettled).toBe(1);

      expect(await billing.summary(userId)).toMatchObject({ debt: 800, balance: -800 });
      await expectConsistent();
    });

    it.each([
      ['failed', () => stampRun('req-1', 'failed')],
      ['missing (never stamped)', async () => undefined],
      ['unstamped (a later dispatch took the stamp)', () => stampRun('req-other', 'processing')],
      ['stalled for 3 h (B13)', () => stampRun('req-1', 'pending')],
      ['"completed" by an older run, before the hold (W2)', () => stampRun('req-1', 'completed', new Date(clock.now().getTime() - HOUR_MS))],
    ])('should release a hold whose run is %s and give every token back', async (_label, arrange) => {
      await holdVideo('req-1', 10);
      await arrange();
      clock.advance(4 * HOUR_MS);

      const report = await run();

      expect(report.holdsReleased).toBe(1);
      expect((await billing.summary(userId)).balance).toBe(1000);
      expect((await ledger({ type: 'release' })).map((e) => e.reason)).toEqual(['stale']);
      await expectConsistent();
    });

    it('should keep a hold whose run is still moving', async () => {
      await holdVideo('req-1', 10);
      clock.advance(4 * HOUR_MS);
      await stampRun('req-1', 'processing', clock.now());

      const report = await run();

      expect([report.holdsSettled, report.holdsReleased]).toEqual([0, 0]);
      expect((await billing.summary(userId)).holds).toHaveLength(1);
    });

    it('should keep a hold younger than BILLING_HOLD_MAX_AGE_HOURS', async () => {
      await holdVideo('req-1', 10);
      clock.advance(2 * HOUR_MS);

      expect((await run()).holdsReleased).toBe(0);
    });
  });

  describe('lot expiry', () => {
    it('should expire what is left of a lot past its expiry, exactly once', async () => {
      await billing.grantLot(userId, { tokens: 100, expiresAt: new Date(clock.now().getTime() + HOUR_MS) }, ctx());
      clock.advance(2 * HOUR_MS);

      const first = await run();
      const second = await run();

      expect([first.lotTokensExpired, second.lotTokensExpired]).toEqual([100, 0]);
      expect((await ledger({ type: 'expire' })).map((e) => [e.unit, e.tokens])).toEqual([['tokens', -100]]);
      expect((await billing.summary(userId)).balance).toBe(1000);
      await expectConsistent();
    });

    it('should leave a minute lot from before the token migration alone', async () => {
      await billing.ensureAccount(userId);
      const account = await db().collection('billingAccounts').findOne({ ownerId: new ObjectId(userId) });
      await db().collection('creditLots').insertOne({
        accountId: account?._id, minutesGranted: 100, minutesRemaining: 100, source: { kind: 'admin', ref: 'legacy' },
        expiresAt: new Date(clock.now().getTime() - HOUR_MS), createdAt: new Date('2026-10-01T00:00:00Z'),
      });

      expect((await run()).lotTokensExpired).toBe(0);
      expect(await ledger({ type: 'expire' })).toEqual([]);
    });
  });

  describe('recurring grants (P2.6 end-to-end)', () => {
    it('should grant a lot that expires at the grant\'s next run, once per month', async () => {
      const { grantId } = await billing.createRecurringGrant(userId, { tokens: 10_000, every: 'month', active: true }, ctx());

      const first = await run();
      const again = await run();

      expect([first.recurringLots, again.recurringLots]).toEqual([1, 0]);
      const detail = await billing.getAccountDetail(userId);
      expect(detail.lots.filter((lot) => lot.source.kind === 'recurring')).toEqual([
        expect.objectContaining({ tokensGranted: 10_000, expiresAt: '2026-12-01T10:00:00.000Z', source: { kind: 'recurring', ref: grantId } }),
      ]);
      expect(detail.recurringGrants[0]).toMatchObject({ nextRunAt: '2026-12-01T10:00:00.000Z', lastRunAt: '2026-11-01T10:00:00.000Z' });
    });

    it('should expire last month\'s lot and grant a fresh one at the next run — no rollover', async () => {
      await billing.createRecurringGrant(userId, { tokens: 10_000, every: 'month', active: true }, ctx());
      await run();
      clock.set('2026-12-01T10:00:00Z');

      const report = await run();

      expect([report.lotTokensExpired, report.recurringLots]).toEqual([10_000, 1]);
      expect((await billing.summary(userId)).credits.available).toBe(11_000);
      await expectConsistent();
    });

    it('should leave no gap at the turn of the month: the old lot lasts until the new one is in (I3)', async () => {
      const graced = { recurringLotGraceMs: 10 * 60 * 1000 };
      await billing.createRecurringGrant(userId, { tokens: 10_000, every: 'month', active: true }, ctx());
      await run(graced);
      clock.set('2026-12-01T10:01:00Z');

      const beforeTick = (await billing.summary(userId)).credits.available;
      await run(graced);
      clock.set('2026-12-01T10:11:00Z');
      const afterGrace = await run(graced);

      expect(beforeTick).toBe(11_000);
      expect(afterGrace.lotTokensExpired).toBe(10_000);
      expect((await billing.summary(userId)).credits.available).toBe(11_000);
      await expectConsistent();
    });

    it('should retire the grant of an erased user and pause it while the user is soft-deleted (I7)', async () => {
      const { grantId } = await billing.createRecurringGrant(userId, { tokens: 300, every: 'month', active: true }, ctx());
      await db().collection('users').updateOne({ _id: new ObjectId(userId) }, { $set: { deletedAt: clock.now() } });

      expect((await run()).recurringLots).toBe(0);
      expect((await billing.getAccountDetail(userId)).recurringGrants).toHaveLength(1);

      await db().collection('users').deleteOne({ _id: new ObjectId(userId) });
      expect((await run()).recurringLots).toBe(0);
      expect(await db().collection('recurringGrants').findOne({ _id: new ObjectId(grantId) })).toMatchObject({ active: false, deletedAt: expect.any(Date) });
    });

    it('should skip inactive and deleted grants', async () => {
      await billing.createRecurringGrant(userId, { tokens: 300, every: 'month', active: false }, ctx());
      const { grantId } = await billing.createRecurringGrant(userId, { tokens: 300, every: 'month', active: true }, ctx());
      await billing.deleteRecurringGrant(userId, grantId, ctx());

      expect((await run()).recurringLots).toBe(0);
    });

    it('should leave a minute grant from before the token migration for the migration to convert', async () => {
      await billing.ensureAccount(userId);
      const account = await db().collection('billingAccounts').findOne({ ownerId: new ObjectId(userId) });
      await db().collection('recurringGrants').insertOne({
        accountId: account?._id, minutes: 300, every: 'month', active: true, nextRunAt: clock.now(), lastRunAt: null,
        actorId: 'system:test', auditId: new ObjectId(), reason: 'demo', createdAt: clock.now(), updatedAt: clock.now(), deletedAt: null,
      });

      expect((await run()).recurringLots).toBe(0);
      expect((await billing.getAccountDetail(userId)).recurringGrants).toEqual([]);
    });
  });

  describe('unsettled assistant executions (usage callback lost)', () => {
    const MINUTE_MS = 60 * 1000;
    const usageRow = (fields: Record<string, unknown>) => ({
      service: 'assistant', request_id: 'req-a', user_id: userId, cost_usd: 0.01, feature: 'assistant:rag_chat',
      timestamp: new Date(clock.now().getTime() - 20 * MINUTE_MS), ...fields,
    });
    const insertUsage = (...rows: Array<Record<string, unknown>>) => db().collection('llm_usage').insertMany(rows.map(usageRow));
    const execDebits = () => ledger({ type: 'debit', executionId: { $exists: true } });

    // The account entered the token ledger (its signup grant) an hour ago, before every usage row below.
    beforeEach(async () => {
      clock.advance(-HOUR_MS);
      await billing.summary(userId);
      clock.advance(HOUR_MS);
    });

    it('should leave a chat from before the account entered the token ledger free (minutes billing did not charge it)', async () => {
      await insertUsage({ timestamp: new Date(clock.now().getTime() - 2 * HOUR_MS) });

      expect((await run()).executionsBackfilled).toBe(0);
      expect(await execDebits()).toEqual([]);
    });

    it('should leave an account the token migration has not reached alone', async () => {
      await db().collection('billingAccounts').updateOne({ ownerId: new ObjectId(userId) }, { $unset: { signupGrantAt: '' } });
      await insertUsage({});

      expect((await run()).executionsBackfilled).toBe(0);
      expect(await execDebits()).toEqual([]);
    });

    it('should debit an unreported request its llm_usage total once, keyed debit:exec:req:<requestId>', async () => {
      await insertUsage({ cost_usd: 0.01, timestamp: new Date(clock.now().getTime() - 30 * MINUTE_MS) }, { cost_usd: 0.02 });

      const first = await run();
      const second = await run();

      const [debit, ...others] = await execDebits();
      expect([first.executionsBackfilled, second.executionsBackfilled, others]).toEqual([1, 0, []]);
      expect(debit).toMatchObject({
        executionId: 'req:req-a', executionKind: 'chat', tokens: -90, costUsd: 0.03, reason: 'backfill',
        source: { kind: 'run', ref: 'req-a' },
      });
      expect(debit.idempotencyKey).toMatch(/^debit:exec:req:req-a:lot:[0-9a-f]{24}$/);
      expect((await billing.summary(userId)).balance).toBe(910);
      await expectConsistent();
    });

    it.each([
      ['assistant:library_chat', 'library_chat'],
      ['assistant:action:save_note', 'action'],
      ['assistant:rag_chat', 'chat'],
    ])('should name a request tagged %s a %s execution', async (feature, kind) => {
      await insertUsage({ feature });

      await run();

      expect((await execDebits()).map((d) => d.executionKind)).toEqual([kind]);
    });

    it('should skip a request whose usage callback landed', async () => {
      await billing.debitExecution({ userId, executionId: 'exec-1', kind: 'chat', requestId: 'req-a', costUsd: 0.03 });
      await insertUsage({ cost_usd: 0.03 });

      const report = await run();

      expect(report.executionsBackfilled).toBe(0);
      expect((await execDebits()).map((d) => d.executionId)).toEqual(['exec-1']);
    });

    it('should wait until a request has been quiet for 10 minutes', async () => {
      await insertUsage({ timestamp: new Date(clock.now().getTime() - 5 * MINUTE_MS) });

      const early = await run();
      clock.advance(6 * MINUTE_MS);
      const later = await run();

      expect([early.executionsBackfilled, later.executionsBackfilled]).toEqual([0, 1]);
    });

    it('should skip an erased user, opening no account for the id', async () => {
      const erased = new ObjectId().toHexString();
      await insertUsage({ user_id: erased });

      const report = await run();

      expect(report.executionsBackfilled).toBe(0);
      expect(await db().collection('billingAccounts').countDocuments({ ownerId: new ObjectId(erased) })).toBe(0);
    });

    it('should skip a user whose deletion is pending', async () => {
      await db().collection('users').updateOne({ _id: new ObjectId(userId) }, { $set: { deletedAt: clock.now() } });
      await insertUsage({});

      expect((await run()).executionsBackfilled).toBe(0);
    });

    it.each([
      ['a free request (no cost)', { cost_usd: 0 }],
      ['a pipeline row', { service: 'summarizer' }],
      ['a request older than 24 h', { ageMs: 25 * HOUR_MS }],
      ['a row with no request id', { request_id: null }],
    ])('should leave %s alone', async (_label, { ageMs, ...fields }: Record<string, unknown>) => {
      await insertUsage({ ...fields, ...(typeof ageMs === 'number' && { timestamp: new Date(clock.now().getTime() - ageMs) }) });

      expect((await run()).executionsBackfilled).toBe(0);
      expect(await execDebits()).toEqual([]);
    });
  });

  describe('projection drift', () => {
    it('should warn and write one llm_alerts row for an account that no longer equals its ledger', async () => {
      await billing.grantLot(userId, { tokens: 100 }, ctx());
      await db().collection('billingAccounts').updateOne({}, { $set: { 'buckets.credit.tokensAvailable': 999, updatedAt: clock.now() } });

      const report = await run();
      await run();
      await repairWith().runOnce();

      expect(report.driftAccounts).toBe(1);
      const alerts = await db().collection('llm_alerts').find({ type: 'billing_drift' }).toArray();
      expect(alerts).toHaveLength(1);
      expect(mockLogger.warn).toHaveBeenCalledWith(expect.objectContaining({ drift: expect.any(Array) }), 'billing_drift');
    });

    it('should not flag a consistent account', async () => {
      await billing.grantLot(userId, { tokens: 100 }, ctx());

      expect((await run()).driftAccounts).toBe(0);
    });
  });

  describe('llm_usage privacy (docs/PRIVACY.md)', () => {
    const at = (days: number) => new Date(clock.now().getTime() - days * DAY_MS);

    it('should drop user_id from rows older than 90 days only, and re-run as a no-op', async () => {
      await db().collection('llm_usage').insertMany([
        { user_id: 'u-old', request_id: 'r-old', cost_usd: 0.5, model: 'm', timestamp: at(91) },
        { request_id: 'r-anonymous-old', cost_usd: 0.5, model: 'm', timestamp: at(92) },
        { user_id: 'u-recent', request_id: 'r-recent', cost_usd: 0.5, model: 'm', timestamp: at(89) },
      ]);

      const first = await run();
      const second = await run();

      expect([first.llmUsagePseudonymised, second.llmUsagePseudonymised]).toEqual([2, 0]);
      const rows = await db().collection('llm_usage').find({}).sort({ timestamp: -1 }).toArray();
      expect(rows.map((row) => [row.user_id, row.request_id])).toEqual([['u-recent', 'r-recent'], [undefined, undefined], [undefined, undefined]]);
      expect(rows[1]).toMatchObject({ cost_usd: 0.5, model: 'm' });
    });

    it('should drop prompt_preview from rows older than 90 days, whatever service wrote them', async () => {
      await db().collection('llm_usage').insertMany([
        { service: 'summarizer', prompt_preview: 'Summarize this video', prompt_hash: 'h1', cost_usd: 0.5, timestamp: at(91) },
        { service: 'assistant', prompt_preview: 'old chat question', prompt_hash: 'h2', cost_usd: 0.5, timestamp: at(120) },
      ]);

      await run();

      const rows = await db().collection('llm_usage').find({}).toArray();
      expect(rows.map((row) => row.prompt_preview)).toEqual([undefined, undefined]);
    });

    it('should clear an old row that has only a prompt_preview, so no user link is needed to match it', async () => {
      await db().collection('llm_usage').insertOne({ service: 'summarizer', prompt_preview: 'anonymous prompt', cost_usd: 0.5, timestamp: at(100) });

      const report = await run();

      expect(report.llmUsagePseudonymised).toBe(1);
    });

    it('should keep prompt_preview, cost, model and prompt_hash on rows 90 days old or newer', async () => {
      await db().collection('llm_usage').insertOne(
        { service: 'summarizer', prompt_preview: 'recent prompt', prompt_hash: 'h3', cost_usd: 0.5, model: 'm', timestamp: at(89) },
      );

      await run();

      expect(await db().collection('llm_usage').findOne({})).toMatchObject({ prompt_preview: 'recent prompt', prompt_hash: 'h3', cost_usd: 0.5, model: 'm' });
    });

    it('should keep prompt_hash, cost and model on the rows it clears', async () => {
      await db().collection('llm_usage').insertOne(
        { service: 'summarizer', user_id: 'u', prompt_preview: 'old prompt', prompt_hash: 'h4', cost_usd: 0.5, model: 'm', timestamp: at(91) },
      );

      await run();

      expect(await db().collection('llm_usage').findOne({})).toMatchObject({ prompt_hash: 'h4', cost_usd: 0.5, model: 'm' });
    });
  });

  describe('GDPR hard-delete sweep', () => {
    it('should hard-delete due accounts as the scheduler, keep an admin\'s origin, and leave the rest', async () => {
      const selfDeleted = userId;
      const adminDeleted = await insertUser(db());
      const notYetDue = await insertUser(db());
      await deletions.requestDeletion(selfDeleted);
      await deletions.requestDeletion(adminDeleted, { origin: { initiatedBy: 'admin', adminId: 'admin-1', reason: 'ticket' } });
      clock.advance(20 * DAY_MS);
      await deletions.requestDeletion(notYetDue);
      clock.advance(11 * DAY_MS);

      const report = await run();

      expect(report.deletionsProcessed).toBe(2);
      const remaining = await db().collection('users').find({}).toArray();
      expect(remaining.map((u) => u._id.toHexString())).toEqual([notYetDue]);
      const audits = await db().collection('userDeletions').find({}).toArray();
      const originOf = (id: string) => audits.find((a) => a.originalUserId?.toString() === id)?.initiatedBy;
      expect([originOf(selfDeleted), originOf(adminDeleted)]).toEqual(['scheduler', 'admin']);
    });
  });

  describe('run control', () => {
    it('should skip the whole run while another process holds the lock', async () => {
      await billing.grantLot(userId, { tokens: 100, expiresAt: new Date(clock.now().getTime() + HOUR_MS) }, ctx());
      clock.advance(2 * HOUR_MS);
      const held: RepairLock = { acquire: async () => null, release: vi.fn() };

      expect(await repairWith({ lock: held }).runOnce()).toBe('locked');
      expect(await ledger({ type: 'expire' })).toEqual([]);
    });

    it('should do nothing when nothing is due', async () => {
      const report = await run();

      expect(report).toEqual({
        holdsSettled: 0, holdsReleased: 0, lotTokensExpired: 0, recurringLots: 0, executionsBackfilled: 0,
        driftAccounts: 0, llmUsagePseudonymised: 0, deletionsProcessed: 0, failedSteps: [],
      });
      expect(await ledger({})).toEqual([]);
    });

    it('should run every other step when one fails, and release the lock', async () => {
      await billing.grantLot(userId, { tokens: 100, expiresAt: new Date(clock.now().getTime() + HOUR_MS) }, ctx());
      clock.advance(2 * HOUR_MS);
      const lock: RepairLock = { acquire: async () => 'token-1', release: vi.fn().mockResolvedValue(undefined) };
      const failing = { runScheduledDeletions: vi.fn().mockRejectedValue(new Error('cascade down')) };

      const report = await run({ lock, deletions: failing });

      expect(report.failedSteps).toEqual(['gdpr_deletions']);
      expect(report.lotTokensExpired).toBe(100);
      expect(lock.release).toHaveBeenCalledWith('token-1');
    });
  });
});
