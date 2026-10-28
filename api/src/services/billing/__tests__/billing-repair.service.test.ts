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
    it('should settle a hold whose run completed but whose completion never arrived, with its cost', async () => {
      await holdVideo('req-1', 10);
      await stampRun('req-1', 'completed');
      await db().collection('llm_usage').insertOne({ request_id: 'req-1', cost_usd: 0.2, timestamp: clock.now() });
      clock.advance(4 * HOUR_MS);

      const report = await run();

      expect(report.holdsSettled).toBe(1);
      expect((await ledger({ type: 'debit' })).map((d) => d.costUsd)).toEqual([0.2]);
      await expectConsistent();
    });

    it.each([
      ['failed', () => stampRun('req-1', 'failed')],
      ['missing (never stamped)', async () => undefined],
      ['unstamped (a later dispatch took the stamp)', () => stampRun('req-other', 'processing')],
      ['stalled for 3 h (B13)', () => stampRun('req-1', 'pending')],
    ])('should release a hold whose run is %s and give the free video back', async (_label, arrange) => {
      await holdVideo('req-1', 10);
      await arrange();
      clock.advance(4 * HOUR_MS);

      const report = await run();

      expect(report.holdsReleased).toBe(1);
      expect((await billing.summary(userId)).free.available).toBe(true);
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
      await billing.grantLot(userId, { minutes: 100, expiresAt: new Date(clock.now().getTime() + HOUR_MS) }, ctx());
      clock.advance(2 * HOUR_MS);

      const first = await run();
      const second = await run();

      expect([first.lotMinutesExpired, second.lotMinutesExpired]).toEqual([100, 0]);
      expect((await ledger({ type: 'expire' })).map((e) => e.minutes)).toEqual([-100]);
      await expectConsistent();
    });
  });

  describe('recurring grants (P2.6 end-to-end)', () => {
    it('should grant a lot that expires at the grant\'s next run, once per month', async () => {
      const { grantId } = await billing.createRecurringGrant(userId, { minutes: 300, every: 'month', active: true }, ctx());

      const first = await run();
      const again = await run();

      expect([first.recurringLots, again.recurringLots]).toEqual([1, 0]);
      const detail = await billing.getAccountDetail(userId);
      expect(detail.lots).toEqual([expect.objectContaining({ minutesGranted: 300, expiresAt: '2026-12-01T10:00:00.000Z', source: { kind: 'recurring', ref: grantId } })]);
      expect(detail.recurringGrants[0]).toMatchObject({ nextRunAt: '2026-12-01T10:00:00.000Z', lastRunAt: '2026-11-01T10:00:00.000Z' });
    });

    it('should expire last month\'s lot and grant a fresh one at the next run — no rollover', async () => {
      await billing.createRecurringGrant(userId, { minutes: 300, every: 'month', active: true }, ctx());
      await run();
      clock.set('2026-12-01T10:00:00Z');

      const report = await run();

      expect([report.lotMinutesExpired, report.recurringLots]).toEqual([300, 1]);
      expect((await billing.summary(userId)).credits.available).toBe(300);
      await expectConsistent();
    });

    it('should skip inactive and deleted grants', async () => {
      await billing.createRecurringGrant(userId, { minutes: 300, every: 'month', active: false }, ctx());
      const { grantId } = await billing.createRecurringGrant(userId, { minutes: 300, every: 'month', active: true }, ctx());
      await billing.deleteRecurringGrant(userId, grantId, ctx());

      expect((await run()).recurringLots).toBe(0);
    });
  });

  describe('projection drift', () => {
    it('should warn and write one llm_alerts row for an account that no longer equals its ledger', async () => {
      await billing.grantLot(userId, { minutes: 100 }, ctx());
      await db().collection('billingAccounts').updateOne({}, { $set: { 'buckets.credit.minutesAvailable': 999, updatedAt: clock.now() } });

      const report = await run();
      await run();
      await repairWith().runOnce();

      expect(report.driftAccounts).toBe(1);
      const alerts = await db().collection('llm_alerts').find({ type: 'billing_drift' }).toArray();
      expect(alerts).toHaveLength(1);
      expect(mockLogger.warn).toHaveBeenCalledWith(expect.objectContaining({ drift: expect.any(Array) }), 'billing_drift');
    });

    it('should not flag a consistent account', async () => {
      await billing.grantLot(userId, { minutes: 100 }, ctx());

      expect((await run()).driftAccounts).toBe(0);
    });
  });

  describe('llm_usage privacy (docs/PRIVACY.md)', () => {
    it('should drop user_id from rows older than 90 days only, and re-run as a no-op', async () => {
      const at = (days: number) => new Date(clock.now().getTime() - days * DAY_MS);
      await db().collection('llm_usage').insertMany([
        { user_id: 'u-old', cost_usd: 0.5, model: 'm', timestamp: at(91) },
        { user_id: 'u-recent', cost_usd: 0.5, model: 'm', timestamp: at(89) },
      ]);

      const first = await run();
      const second = await run();

      expect([first.llmUsagePseudonymised, second.llmUsagePseudonymised]).toEqual([1, 0]);
      const rows = await db().collection('llm_usage').find({}).sort({ timestamp: 1 }).toArray();
      expect(rows.map((row) => row.user_id)).toEqual([undefined, 'u-recent']);
      expect(rows[0]).toMatchObject({ cost_usd: 0.5, model: 'm' });
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
      await billing.grantLot(userId, { minutes: 100, expiresAt: new Date(clock.now().getTime() + HOUR_MS) }, ctx());
      clock.advance(2 * HOUR_MS);
      const held: RepairLock = { acquire: async () => null, release: vi.fn() };

      expect(await repairWith({ lock: held }).runOnce()).toBe('locked');
      expect(await ledger({ type: 'expire' })).toEqual([]);
    });

    it('should do nothing when nothing is due', async () => {
      const report = await run();

      expect(report).toEqual({
        holdsSettled: 0, holdsReleased: 0, lotMinutesExpired: 0, recurringLots: 0,
        driftAccounts: 0, llmUsagePseudonymised: 0, deletionsProcessed: 0, failedSteps: [],
      });
      expect(await ledger({})).toEqual([]);
    });

    it('should run every other step when one fails, and release the lock', async () => {
      await billing.grantLot(userId, { minutes: 100, expiresAt: new Date(clock.now().getTime() + HOUR_MS) }, ctx());
      clock.advance(2 * HOUR_MS);
      const lock: RepairLock = { acquire: async () => 'token-1', release: vi.fn().mockResolvedValue(undefined) };
      const failing = { runScheduledDeletions: vi.fn().mockRejectedValue(new Error('cascade down')) };

      const report = await run({ lock, deletions: failing });

      expect(report.failedSteps).toEqual(['gdpr_deletions']);
      expect(report.lotMinutesExpired).toBe(100);
      expect(lock.release).toHaveBeenCalledWith('token-1');
    });
  });
});
