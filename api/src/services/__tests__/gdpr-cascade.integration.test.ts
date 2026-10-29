import { describe, it, expect, beforeAll, afterAll, beforeEach, vi } from 'vitest';
import { MongoMemoryReplSet } from 'mongodb-memory-server';
import { MongoClient, Db, ObjectId } from 'mongodb';
import type { FastifyBaseLogger } from 'fastify';
import bcrypt from 'bcrypt';

import { createBillingIndexes } from '../../plugins/mongodb.js';
import { BillingRepository } from '../../repositories/billing.repository.js';
import { BillingErasureRepository, ERASED_ACTOR_ID } from '../../repositories/billing-erasure.repository.js';
import { UserRepository } from '../../repositories/user.repository.js';
import { UserDeletionRepository } from '../../repositories/user-deletion.repository.js';
import { BillingService } from '../billing/billing.service.js';
import { UserDeletionService, hashEmail } from '../user-deletion.service.js';
import { auditIdFor } from '../../../../scripts/migrate-billing-plan.js';

/**
 * Compliance-grade end-to-end integration test for the GDPR cascade.
 *
 * Seeds a user with rows in **every** user-scoped collection in the
 * inventory, fires the immediate-delete path, and asserts:
 *  - zero rows remain in every user-scoped collection
 *  - the audit row exists with the right hashed email and counts
 *  - other users' data is left untouched
 *  - Qdrant + S3 hooks return zero (placeholder steps documented in the
 *    inventory report; verified here to keep the contract honest).
 *  - the minutes ledger names the user nowhere afterwards, and keeps every
 *    amount (pseudonymised, brief Q12). A replica set: the billing step
 *    runs in a transaction.
 *
 * This is the single most important test for compliance — Phase 7 of the
 * task plan calls it out as the regression gate for every future change.
 */

const silentLogger = {
  info: vi.fn(),
  warn: vi.fn(),
  error: vi.fn(),
  debug: vi.fn(),
  trace: vi.fn(),
  fatal: vi.fn(),
  child: vi.fn(() => silentLogger),
  level: 'silent',
  silent: vi.fn(),
} as unknown as FastifyBaseLogger;

describe('GDPR cascade deletion — end-to-end integration', () => {
  let mongod: MongoMemoryReplSet;
  let client: MongoClient;
  let db: Db;
  let userRepo: UserRepository;
  let deletionRepo: UserDeletionRepository;
  let service: UserDeletionService;

  beforeAll(async () => {
    mongod = await MongoMemoryReplSet.create({ replSet: { count: 1 } });
    client = new MongoClient(mongod.getUri());
    await client.connect();
    db = client.db('gdpr-e2e');
    userRepo = new UserRepository(db);
    deletionRepo = new UserDeletionRepository(db);
    service = new UserDeletionService(db, userRepo, deletionRepo, silentLogger);
  });

  afterAll(async () => {
    await client.close();
    await mongod.stop();
  });

  beforeEach(async () => {
    const collections = await db.listCollections().toArray();
    for (const c of collections) await db.collection(c.name).deleteMany({});
  });

  it('removes every user-scoped row, leaves other users alone, and writes an audit', async () => {
    // ─── Seed the doomed user across the entire inventory ─────────────────
    const doomed = await userRepo.create({
      email: 'doomed@example.com',
      passwordHash: await bcrypt.hash('correct horse battery staple', 4),
      name: 'Doomed User',
    });
    const doomedId = doomed._id;

    // Three videos (the canonical Phase 7 ask: "Create user + 3 videos…")
    const videoSummaryIds = [new ObjectId(), new ObjectId(), new ObjectId()];
    for (const [i, vsid] of videoSummaryIds.entries()) {
      await db.collection('userVideos').insertOne({
        userId: doomedId,
        videoSummaryId: vsid,
        youtubeId: `yt-${i}`,
        title: `Video ${i}`,
        status: 'completed',
        folderId: null,
        addedAt: new Date(),
        createdAt: new Date(),
        updatedAt: new Date(),
      });
    }

    // Two folders + one nested
    await db.collection('folders').insertMany([
      { userId: doomedId, name: 'Work', path: '/Work', level: 0 },
      { userId: doomedId, name: 'Personal', path: '/Personal', level: 0 },
      { userId: doomedId, name: 'Notes', path: '/Work/Notes', level: 1 },
    ]);

    // Daily cost row + adjustment
    await db.collection('userCosts').insertOne({
      userId: doomedId,
      date: '2026-05-20',
      rawUsd: 4.2,
      adjustmentUsd: -1,
      effectiveUsd: 3.2,
    });
    await db.collection('userCostAdjustments').insertOne({
      userId: doomedId,
      date: '2026-05-20',
      amount: -1,
      reason: 'goodwill',
    });

    // Idempotency rows
    await db.collection('idempotencyKeys').insertMany([
      { userId: doomedId, hash: 'h1', status: 'completed', expiresAt: new Date(Date.now() + 86400000) },
      { userId: doomedId, hash: 'h2', status: 'pending', expiresAt: new Date(Date.now() + 86400000) },
    ]);

    // Assistant notes — stored with userId as a string in the agent service.
    await db.collection('agentNotes').insertMany([
      { _id: 'n1', userId: doomedId.toString(), videoId: 'yt-0', text: 'Important', createdAt: new Date() } as unknown as Document,
      { _id: 'n2', userId: doomedId.toString(), videoId: 'yt-1', text: 'Bookmark', createdAt: new Date() } as unknown as Document,
      { _id: 'n3', userId: doomedId.toString(), videoId: 'yt-2', text: 'Highlight', createdAt: new Date() } as unknown as Document,
    ]);

    // ─── Seed a second user whose data MUST NOT be touched ────────────────
    const witness = await userRepo.create({
      email: 'witness@example.com',
      passwordHash: await bcrypt.hash('not relevant', 4),
      name: 'Witness',
    });
    await db.collection('userVideos').insertOne({
      userId: witness._id,
      videoSummaryId: new ObjectId(),
      youtubeId: 'wit-1',
      status: 'completed',
      folderId: null,
      addedAt: new Date(),
      createdAt: new Date(),
      updatedAt: new Date(),
    });
    await db.collection('folders').insertOne({
      userId: witness._id,
      name: 'WitnessFolder',
      path: '/WitnessFolder',
      level: 0,
    });
    await db.collection('userCosts').insertOne({
      userId: witness._id,
      date: '2026-05-20',
      rawUsd: 0.5,
    });
    await db.collection('agentNotes').insertOne({
      _id: 'w1',
      userId: witness._id.toString(),
      videoId: 'wit-1',
      text: 'Witness note',
      createdAt: new Date(),
    } as unknown as Document);

    // ─── Shared cache row that BOTH users referenced. Must NOT be deleted ─
    await db.collection('videoSummaryCache').insertOne({
      _id: videoSummaryIds[0],
      youtubeId: 'yt-0',
      status: 'completed',
      isLatest: true,
      version: 1,
      retryCount: 0,
      createdAt: new Date(),
      updatedAt: new Date(),
    });

    // ─── Fire the cascade (admin immediate-delete path) ───────────────────
    const audit = await service.executeHardDelete(doomedId.toString(), {
      initiatedBy: 'admin',
      adminId: 'compliance-op-1',
      reason: 'GDPR request #4567',
    });

    // ─── 1. Doomed user is gone from every user-scoped collection ─────────
    expect(await userRepo.findById(doomedId.toString())).toBeNull();
    expect(await db.collection('userVideos').countDocuments({ userId: doomedId })).toBe(0);
    expect(await db.collection('folders').countDocuments({ userId: doomedId })).toBe(0);
    expect(await db.collection('userCosts').countDocuments({ userId: doomedId })).toBe(0);
    expect(await db.collection('userCostAdjustments').countDocuments({ userId: doomedId })).toBe(0);
    expect(await db.collection('idempotencyKeys').countDocuments({ userId: doomedId })).toBe(0);
    expect(await db.collection('agentNotes').countDocuments({ userId: doomedId.toString() })).toBe(0);

    // ─── 2. Audit row exists with hashed email and accurate counts ────────
    expect(audit.emailHash).toBe(hashEmail('doomed@example.com'));
    expect(audit.emailHash).not.toContain('doomed');
    expect(audit.emailHash).not.toContain('@');
    expect(audit.initiatedBy).toBe('admin');
    expect(audit.adminId).toBe('compliance-op-1');
    expect(audit.reason).toBe('GDPR request #4567');
    expect(audit.counts).toEqual({
      userVideos: 3,
      folders: 3,
      userCosts: 1,
      userCostAdjustments: 1,
      idempotencyKeys: 2,
      assistantNotes: 3,
      qdrantPoints: 0, // see reports/user-data-inventory.md — shared, not user-keyed
      s3Objects: 0,    // see reports/user-data-inventory.md — shared, not user-keyed
    });
    expect(audit.warnings ?? []).toEqual([]);
    expect(audit.durationMs).toBeGreaterThanOrEqual(0);

    // Audit row is queryable by hashed email — proof of cascade for legal.
    const byHash = await deletionRepo.findByEmailHash(audit.emailHash);
    expect(byHash?.originalUserId).toBe(doomedId.toString());

    // ─── 3. The witness user is fully intact ──────────────────────────────
    expect(await userRepo.findById(witness._id.toString())).not.toBeNull();
    expect(await db.collection('userVideos').countDocuments({ userId: witness._id })).toBe(1);
    expect(await db.collection('folders').countDocuments({ userId: witness._id })).toBe(1);
    expect(await db.collection('userCosts').countDocuments({ userId: witness._id })).toBe(1);
    expect(await db.collection('agentNotes').countDocuments({ userId: witness._id.toString() })).toBe(1);

    // ─── 4. Shared resources are untouched ────────────────────────────────
    expect(await db.collection('videoSummaryCache').countDocuments({})).toBe(1);
  });

  it('pseudonymises the minutes ledger: the user is named nowhere, every amount stays (Q12)', async () => {
    await createBillingIndexes(db);
    const billing = new BillingService(new BillingRepository(db), userRepo, silentLogger);
    const [doomed, witness] = await Promise.all(['ledger-doomed', 'ledger-witness'].map((name) => userRepo.create({
      email: `${name}@example.com`, passwordHash: '$2b$10$fake', name,
    })));
    const doomedId = doomed._id.toString();
    const ctx = (actorId: string) => ({ actorId, reason: 'gdpr test', auditId: new ObjectId().toHexString() });
    // The doomed user's own billing: a lot, a recurring grant, a settled run and an open hold.
    await billing.grantLot(doomedId, { minutes: 120 }, ctx('system:test'));
    await billing.createRecurringGrant(doomedId, { minutes: 300, every: 'month', active: true }, ctx('system:test'));
    await billing.hold('run-settled', await billing.quote(doomedId, 'dQw4w9WgXcQ', 600));
    await billing.settle('run-settled');
    await billing.hold('run-open', await billing.quote(doomedId, 'aaaaaaaaaaa', 300));
    await db.collection('billingShadow').insertOne({ requestId: 'run-settled', userId: doomed._id, path: 'completed' });
    // …and what they did as an admin to the witness's account.
    await billing.grantLot(witness._id.toString(), { minutes: 50 }, ctx(doomedId));
    const accountOf = (owner: ObjectId) => db.collection('billingAccounts').findOne({ ownerType: 'user', ownerId: owner });
    const doomedAccount = await accountOf(doomed._id);
    // Keys and refs are re-keyed (C1); what each entry did, and by how much, stays.
    const minutesOf = async (accountId: ObjectId) => (await db.collection('ledgerEntries').find({ accountId }).toArray())
      .map((entry) => [entry.type, entry.bucket, entry.minutes].join(':')).sort();
    const before = { doomed: await minutesOf(doomedAccount!._id), lots: await db.collection('creditLots').countDocuments() };

    const audit = await service.executeHardDelete(doomedId, { initiatedBy: 'admin', adminId: 'op-1', reason: 'GDPR' });

    expect(audit.warnings ?? []).toEqual([]);
    // Nothing in the billing collections names the user any more.
    const naming = await Promise.all(['billingAccounts', 'ledgerEntries', 'recurringGrants', 'billingShadow'].map((name) =>
      db.collection(name).countDocuments({ $or: [{ ownerId: doomed._id }, { userId: doomed._id }, { 'holds.userId': doomed._id }, { actorId: doomedId }] })));
    expect(naming).toEqual([0, 0, 0, 0]);
    // The account and its amounts survive under a tombstone owner; its grant is retired; the shadow row is gone.
    const erased = await db.collection('billingAccounts').findOne({ _id: doomedAccount!._id });
    expect(erased?.ownerId.equals(doomed._id)).toBe(false);
    expect(erased?.holds.map((hold: { userId: ObjectId }) => hold.userId.equals(erased.ownerId))).toEqual([true]);
    expect(await minutesOf(doomedAccount!._id)).toEqual(before.doomed);
    expect(await db.collection('creditLots').countDocuments()).toBe(before.lots);
    expect(await db.collection('recurringGrants').countDocuments({ accountId: doomedAccount!._id, active: true })).toBe(0);
    expect(await db.collection('billingShadow').countDocuments()).toBe(0);
    expect((await billing.verifyAccountById(doomedAccount!._id)).ok).toBe(true);
    // The witness keeps its account and minutes; the erased admin's grant names a stand-in actor.
    const witnessEntries = await db.collection('ledgerEntries').find({ userId: witness._id }).toArray();
    expect(witnessEntries.map((entry) => [entry.minutes, entry.actorId])).toEqual([[50, ERASED_ACTOR_ID]]);
    expect((await accountOf(witness._id))?.buckets.credit.minutesAvailable).toBe(50);
  });

  it('leaves no join back to the erased user: neither the migration ids nor the audit trail reach the real id (C1)', async () => {
    await createBillingIndexes(db);
    const billing = new BillingService(new BillingRepository(db), userRepo, silentLogger);
    const [doomed, witness] = await Promise.all(['join-doomed', 'join-witness'].map((name) => userRepo.create({
      email: `${name}@example.com`, passwordHash: '$2b$10$fake', name,
    })));
    const doomedId = doomed._id.toString();
    const witnessId = witness._id.toString();
    const migrationCtx = (userId: string, step: string) => ({ actorId: 'system:migration', reason: `migration: ${step}`, auditId: auditIdFor(userId, step) });
    // The P3.8 migration's steps, each with its derived auditId and adminAudit row (actor system:migration).
    await billing.setPlan(doomedId, { planId: 'pro' }, migrationCtx(doomedId, 'plan'));
    await billing.grantLot(doomedId, { minutes: 600 }, migrationCtx(doomedId, 'team-lot'));
    await billing.createRecurringGrant(doomedId, { minutes: 300, every: 'month', active: true }, migrationCtx(doomedId, 'recurring-demo'));
    await billing.grantLot(witnessId, { minutes: 50 }, migrationCtx(witnessId, 'team-lot'));
    const migrationRow = (userId: string, step: string, targetType: 'user' | 'role', action: string) => ({
      _id: new ObjectId(auditIdFor(userId, step)), actorId: 'system:migration', action, targetType, targetId: userId, createdAt: new Date(),
    });
    await db.collection('adminAudit').insertMany([
      migrationRow(doomedId, 'plan', 'user', 'billing.plan.set'),
      migrationRow(doomedId, 'team-lot', 'user', 'billing.credits.grant'),
      migrationRow(doomedId, 'recurring-demo', 'user', 'billing.recurring.create'),
      { ...migrationRow(doomedId, 'role', 'role', 'roles.migration.promote'), before: { roles: [] }, after: { roles: ['admin'], roleSource: 'env' } },
      migrationRow(witnessId, 'team-lot', 'user', 'billing.credits.grant'),
      // An admin route's row carries an AccountSnapshot, whose ownerId is the real id.
      { _id: new ObjectId(), actorId: witnessId, action: 'billing.caps.set', targetType: 'user', targetId: doomedId, after: { ownerId: doomedId }, createdAt: new Date() },
      // What the doomed user did as an admin to the witness (I13).
      { _id: new ObjectId(), actorId: doomedId, actorEmail: 'join-doomed@example.com', ip: '203.0.113.9', action: 'billing.adjust', targetType: 'user', targetId: witnessId, createdAt: new Date() },
    ]);

    await service.executeHardDelete(doomedId, { initiatedBy: 'admin', adminId: 'op-1', reason: 'GDPR' });

    // 1. Recomputing the migration's ids from the real id (userDeletions keeps it) finds nothing.
    for (const step of ['plan', 'team-lot', 'recurring-demo', 'role']) {
      const hex = auditIdFor(doomedId, step);
      const id = new ObjectId(hex);
      const found = await Promise.all([
        db.collection('ledgerEntries').countDocuments({ $or: [{ auditId: id }, { 'source.ref': hex }, { idempotencyKey: { $regex: hex } }] }),
        db.collection('creditLots').countDocuments({ 'source.ref': hex }),
        db.collection('recurringGrants').countDocuments({ auditId: id }),
        db.collection('adminAudit').countDocuments({ _id: id }),
      ]);
      expect(found, `step ${step}`).toEqual([0, 0, 0, 0]);
    }
    // 2. The audit trail names the real id nowhere (any field, any targetType), nor the erased admin's email or IP.
    const auditText = JSON.stringify(await db.collection('adminAudit').find({}).toArray());
    expect([auditText.includes(doomedId), auditText.includes('join-doomed@example.com'), auditText.includes('203.0.113.9')]).toEqual([false, false, false]);
    // 3. The erased account's own joins still hold — every ledger auditId reaches its re-keyed audit row — but only the tombstone.
    const account = await db.collection('billingAccounts').findOne({ ownerType: 'user', ownerId: { $nin: [doomed._id, witness._id] } });
    const auditIds = await db.collection('ledgerEntries').distinct('auditId', { accountId: account?._id, auditId: { $exists: true } });
    const joined = await db.collection('adminAudit').find({ _id: { $in: auditIds } }).toArray();
    expect([auditIds.length > 0, joined.length === auditIds.length, joined.every((row) => row.targetId === account?.ownerId.toHexString())])
      .toEqual([true, true, true]);
    // 4. A live user's migration step is still found by its derived id, so a rerun stays a no-op for them.
    expect(await db.collection('ledgerEntries').countDocuments({ 'source.ref': auditIdFor(witnessId, 'team-lot') })).toBeGreaterThan(0);
    expect(await db.collection('adminAudit').countDocuments({ _id: new ObjectId(auditIdFor(witnessId, 'team-lot')) })).toBe(1);
    // 5. The witness keeps a record of what the erased admin did, without naming them.
    const acted = await db.collection('adminAudit').findOne({ action: 'billing.adjust' });
    expect([acted?.actorId, acted?.targetId, 'actorEmail' in (acted ?? {}), 'ip' in (acted ?? {})]).toEqual([ERASED_ACTOR_ID, witnessId, false, false]);
  });

  it('re-runs the billing erasure as a no-op once the user is pseudonymised', async () => {
    await createBillingIndexes(db);
    const billing = new BillingService(new BillingRepository(db), userRepo, silentLogger);
    const user = await userRepo.create({ email: 'twice@example.com', passwordHash: '$2b$10$fake', name: 'Twice' });
    await billing.grantLot(user._id.toString(), { minutes: 30 }, { actorId: 'system:test', reason: 'gdpr test', auditId: new ObjectId().toHexString() });
    const erasure = new BillingErasureRepository(db);
    const first = await erasure.eraseUser(user._id.toString(), new Date());
    const owner = (await db.collection('billingAccounts').findOne({}))?.ownerId;

    const second = await erasure.eraseUser(user._id.toString(), new Date());

    expect([first.accounts, first.ledgerEntries]).toEqual([1, 1]);
    expect(Object.values(second).every((count) => count === 0)).toBe(true);
    expect((await db.collection('billingAccounts').findOne({}))?.ownerId.equals(owner)).toBe(true);
  });

  it('completes the full self-service flow: soft-delete → wait → scheduler hard-deletes', async () => {
    const target = await userRepo.create({
      email: 'self-flow@example.com',
      passwordHash: '$2b$10$fake',
      name: 'Self-flow',
    });
    await db.collection('userVideos').insertOne({
      userId: target._id,
      videoSummaryId: new ObjectId(),
      youtubeId: 'self-1',
      status: 'completed',
      folderId: null,
      addedAt: new Date(),
      createdAt: new Date(),
      updatedAt: new Date(),
    });

    // Step 1: user requests deletion → soft-deleted, hardDeleteAt in 30 days.
    const requestedAt = new Date('2026-05-20T12:00:00Z');
    const flow = new UserDeletionService(
      db,
      userRepo,
      deletionRepo,
      silentLogger,
      () => requestedAt,
    );
    const result = await flow.requestDeletion(target._id.toString());
    expect(result.scheduledHardDeleteAt).toEqual(new Date('2026-06-19T12:00:00Z'));

    const afterSoftDelete = await userRepo.findById(target._id.toString());
    expect(afterSoftDelete?.deletedAt).toEqual(requestedAt);
    // Data still exists in the grace window — recovery must be possible.
    expect(await db.collection('userVideos').countDocuments({ userId: target._id })).toBe(1);

    // Step 2: scheduler runs at T+15 days (still in grace) — must not delete.
    const fifteenDaysLater = new Date('2026-06-04T12:00:00Z');
    const earlyRun = await flow.runScheduledDeletions(fifteenDaysLater);
    expect(earlyRun.processed).toBe(0);
    expect(await userRepo.findById(target._id.toString())).not.toBeNull();

    // Step 3: scheduler runs at T+31 days — past the grace window → cascade.
    const thirtyOneDaysLater = new Date('2026-06-20T12:00:00Z');
    const lateRun = await flow.runScheduledDeletions(thirtyOneDaysLater);
    expect(lateRun.processed).toBe(1);
    expect(lateRun.succeeded).toBe(1);

    // Step 4: every trace of the user is gone, audit exists.
    expect(await userRepo.findById(target._id.toString())).toBeNull();
    expect(await db.collection('userVideos').countDocuments({ userId: target._id })).toBe(0);
    const audit = await deletionRepo.findByEmailHash(hashEmail('self-flow@example.com'));
    expect(audit).not.toBeNull();
    expect(audit?.initiatedBy).toBe('scheduler');
  });
});
