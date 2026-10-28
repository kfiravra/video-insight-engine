import { describe, it, expect, beforeAll, afterAll, beforeEach, afterEach, vi } from 'vitest';
import type { FastifyBaseLogger, FastifyInstance } from 'fastify';
import { ObjectId } from 'mongodb';

import { buildApp } from '../../../app.js';
import type { Container } from '../../../container.js';
import { config } from '../../../config.js';
import { createBillingIndexes } from '../../../plugins/mongodb.js';
import { AdminAuditRepository } from '../../../repositories/admin-audit.repository.js';
import { BillingRepository, StaleAccountVersionError } from '../../../repositories/billing.repository.js';
import { UserRepository } from '../../../repositories/user.repository.js';
import { AuditService } from '../../../services/audit.service.js';
import { BillingService } from '../../../services/billing/billing.service.js';
import { addMonthsUtc } from '../../../services/billing/minutes.js';
import { insertUser } from '../../../services/billing/__tests__/billing-fixtures.js';
import { adminActorRequest, createMockContainer, TEST_ADMIN_USER_ID, type MockContainer } from '../../../test/helpers.js';
import { getTestDb } from '../../../test/setup.js';

type Method = 'GET' | 'POST' | 'DELETE';

interface MutationRoute {
  name: string;
  method: Method;
  permission: string;
  path: (userId: string) => string;
  body: Record<string, unknown>;
}

const GRANT_ID = new ObjectId().toHexString();

const MUTATIONS: MutationRoute[] = [
  { name: 'plan', method: 'POST', permission: 'plan:set', path: (u) => `/accounts/${u}/plan`, body: { planId: 'pro', reason: 'comp' } },
  { name: 'credits', method: 'POST', permission: 'billing:grant', path: (u) => `/accounts/${u}/credits`, body: { minutes: 30, reason: 'comp' } },
  { name: 'caps', method: 'POST', permission: 'caps:set', path: (u) => `/accounts/${u}/caps`, body: { blocked: true, reason: 'abuse' } },
  { name: 'adjust', method: 'POST', permission: 'billing:grant', path: (u) => `/accounts/${u}/adjust`, body: { bucket: 'credit', minutes: 5, reason: 'fix' } },
  {
    name: 'recurring-grants (create)', method: 'POST', permission: 'billing:grant',
    path: (u) => `/accounts/${u}/recurring-grants`, body: { minutes: 300, every: 'month', active: true, reason: 'demo' },
  },
  {
    name: 'recurring-grants (delete)', method: 'DELETE', permission: 'billing:grant',
    path: (u) => `/accounts/${u}/recurring-grants/${GRANT_ID}`, body: { reason: 'stop' },
  },
];

const BASE = '/api/admin/billing';

function createLogger() {
  return { info: vi.fn(), warn: vi.fn(), error: vi.fn(), debug: vi.fn() };
}

describe('admin billing routes', () => {
  let app: FastifyInstance;
  let mocks: MockContainer;
  let logger: ReturnType<typeof createLogger>;
  let billing: BillingService;
  let userId: string;

  beforeAll(async () => {
    const db = getTestDb();
    await createBillingIndexes(db);
    mocks = createMockContainer();
    logger = createLogger();
    const log = logger as unknown as FastifyBaseLogger;
    billing = new BillingService(new BillingRepository(db), new UserRepository(db), log);
    const auditService = new AuditService(new AdminAuditRepository(db), log);
    app = await buildApp({
      logger: false,
      container: { ...mocks, billingService: billing, auditService } as unknown as Partial<Container>,
    });
    await app.ready();
  });

  beforeEach(async () => {
    mocks.userRepository.hasPermission.mockResolvedValue(true);
    mocks.adminReplayGuard.claim.mockResolvedValue(true);
    userId = await insertUser(getTestDb());
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  afterAll(async () => {
    await app?.close();
  });

  /** Send `method path` the way vie-admin does: the shared key plus an actor header signed for exactly this request. */
  async function send(method: Method, path: string, body?: Record<string, unknown>, extraHeaders: Record<string, string> = {}) {
    const url = `${BASE}${path}`;
    const request = adminActorRequest(method, url, body);
    return app.inject({ method, url, headers: { ...request.headers, ...extraHeaders }, payload: request.payload });
  }

  const post = (path: string, body: Record<string, unknown>, headers: Record<string, string> = {}) =>
    send('POST', path, body, headers);
  const auditRows = () => getTestDb().collection('adminAudit').find({}).toArray();
  const ledgerFor = (auditId: string) => getTestDb().collection('ledgerEntries').find({ 'source.ref': auditId }).toArray();

  describe.each(MUTATIONS)('$method $name', (route) => {
    it('should answer 401 without any credentials', async () => {
      const res = await app.inject({ method: route.method, url: `${BASE}${route.path(userId)}` });

      expect(res.statusCode).toBe(401);
    });

    it('should answer 403 to the shared machine key', async () => {
      const res = await app.inject({
        method: route.method,
        url: `${BASE}${route.path(userId)}`,
        headers: { 'x-admin-key': config.ADMIN_API_KEY, 'content-type': 'application/json' },
        payload: JSON.stringify(route.body),
      });

      expect(res.statusCode).toBe(403);
    });

    it('should answer 403 to an admin who no longer holds the permission', async () => {
      mocks.userRepository.hasPermission.mockResolvedValue(false);

      const res = await send(route.method, route.path(userId), route.body);

      expect(res.statusCode).toBe(403);
    });

    it('should check the actor against its own permission', async () => {
      await send(route.method, route.path(userId), route.body);

      expect(mocks.userRepository.hasPermission).toHaveBeenCalledWith(TEST_ADMIN_USER_ID, route.permission);
    });

    it('should require a reason', async () => {
      const { reason: _omitted, ...withoutReason } = route.body;

      const res = await send(route.method, route.path(userId), withoutReason);

      expect(res.statusCode).toBe(400);
    });

    it('should reject a blank reason', async () => {
      const res = await send(route.method, route.path(userId), { ...route.body, reason: '   ' });

      expect(res.statusCode).toBe(400);
    });

    it('should reject a reason over 500 characters', async () => {
      const res = await send(route.method, route.path(userId), { ...route.body, reason: 'x'.repeat(501) });

      expect(res.statusCode).toBe(400);
    });

    it('should reject a self-attested adminId in the body', async () => {
      const res = await send(route.method, route.path(userId), { ...route.body, adminId: TEST_ADMIN_USER_ID });

      expect(res.statusCode).toBe(400);
    });

    it('should reject a malformed user id', async () => {
      const res = await send(route.method, route.path('not-an-id'), route.body);

      expect(res.statusCode).toBe(400);
    });

    it('should answer 404 for a user that does not exist', async () => {
      const res = await send(route.method, route.path(new ObjectId().toHexString()), route.body);

      expect(res.statusCode).toBe(404);
    });
  });

  describe('GET /accounts/:userId', () => {
    it('should answer 401 without credentials', async () => {
      const res = await app.inject({ method: 'GET', url: `${BASE}/accounts/${userId}` });

      expect(res.statusCode).toBe(401);
    });

    it('should answer 403 to an admin without billing:read', async () => {
      mocks.userRepository.hasPermission.mockResolvedValue(false);

      const res = await send('GET', `/accounts/${userId}`);

      expect(res.statusCode).toBe(403);
    });

    it('should return the account, lots, holds, ledger and recurring grants', async () => {
      await post(`/accounts/${userId}/credits`, { minutes: 40, reason: 'comp' });

      const res = await send('GET', `/accounts/${userId}`);

      expect(res.statusCode).toBe(200);
      const detail = res.json();
      expect(detail.account.credits.available).toBe(40);
      expect(detail.lots).toHaveLength(1);
      expect(detail.ledger[0]).toMatchObject({ type: 'grant', bucket: 'credit', minutes: 40, reason: 'comp' });
      expect(detail.recurringGrants).toEqual([]);
    });

    it('should be readable with the shared machine key', async () => {
      const res = await app.inject({
        method: 'GET',
        url: `${BASE}/accounts/${userId}`,
        headers: { 'x-admin-key': config.ADMIN_API_KEY },
      });

      expect(res.statusCode).toBe(200);
    });

    it('should page the ledger', async () => {
      await post(`/accounts/${userId}/credits`, { minutes: 10, reason: 'one' });
      await post(`/accounts/${userId}/credits`, { minutes: 20, reason: 'two' });

      const res = await send('GET', `/accounts/${userId}?ledgerLimit=1`);

      expect(res.json().ledger).toHaveLength(1);
    });

    it('should reject an out-of-range ledgerLimit', async () => {
      const res = await send('GET', `/accounts/${userId}?ledgerLimit=0`);

      expect(res.statusCode).toBe(400);
    });

    it('should answer 404 for an unknown user', async () => {
      const res = await send('GET', `/accounts/${new ObjectId().toHexString()}`);

      expect(res.statusCode).toBe(404);
    });
  });

  describe('POST /accounts/:userId/plan', () => {
    it('should start a manual Pro plan with 600 minutes for the term\'s first cycle', async () => {
      const res = await post(`/accounts/${userId}/plan`, { planId: 'pro', reason: 'comp' });

      expect(res.statusCode).toBe(200);
      const { before, after } = res.json();
      expect(before.plan.id).toBe('free');
      expect(after.plan).toMatchObject({ id: 'pro', status: 'active', source: 'manual' });
      expect(after.planMinutes).toMatchObject({ granted: 600, used: 0, remaining: 600 });
    });

    it('should set a 12-month term unless the admin gives an end date', async () => {
      const res = await post(`/accounts/${userId}/plan`, { planId: 'pro', reason: 'comp' });

      const periodEnd = new Date(res.json().after.plan.periodEnd).getTime();
      expect(Math.abs(periodEnd - addMonthsUtc(new Date(), 12).getTime())).toBeLessThan(60_000);
    });

    it('should honour a given periodEnd', async () => {
      const periodEnd = addMonthsUtc(new Date(), 3).toISOString();

      const res = await post(`/accounts/${userId}/plan`, { planId: 'pro', periodEnd, reason: 'trial' });

      expect(res.json().after.plan.periodEnd).toBe(periodEnd);
    });

    it('should stop plan minutes when set back to free', async () => {
      await post(`/accounts/${userId}/plan`, { planId: 'pro', reason: 'comp' });

      const res = await post(`/accounts/${userId}/plan`, { planId: 'free', reason: 'ended' });

      expect(res.json().after).toMatchObject({ plan: { id: 'free' }, planMinutes: { remaining: 0 } });
    });

    it('should reject a periodEnd in the past with 400', async () => {
      const res = await post(`/accounts/${userId}/plan`, { planId: 'pro', periodEnd: '2020-01-01T00:00:00Z', reason: 'x' });

      expect(res.statusCode).toBe(400);
    });

    it('should reject an unknown plan', async () => {
      const res = await post(`/accounts/${userId}/plan`, { planId: 'team', reason: 'x' });

      expect(res.statusCode).toBe(400);
    });

    it('should write the audit row with the signed admin, the target and both snapshots', async () => {
      const res = await post(`/accounts/${userId}/plan`, { planId: 'pro', reason: 'comp for outage' });

      const [row] = await auditRows();
      expect(row._id.toHexString()).toBe(res.json().auditId);
      expect(row).toMatchObject({
        actorId: TEST_ADMIN_USER_ID,
        action: 'billing.plan.set',
        targetType: 'user',
        targetId: userId,
        reason: 'comp for outage',
        before: { plan: { id: 'free' } },
        after: { plan: { id: 'pro' }, planMinutes: { granted: 600 } },
      });
    });

    it('should stamp the ledger entries with the same audit id, admin and reason', async () => {
      const res = await post(`/accounts/${userId}/plan`, { planId: 'pro', reason: 'comp' });

      const entries = await ledgerFor(res.json().auditId);
      expect(entries).toEqual([expect.objectContaining({ type: 'grant', bucket: 'plan', minutes: 600, actorId: TEST_ADMIN_USER_ID, reason: 'comp' })]);
    });
  });

  describe('POST /accounts/:userId/credits', () => {
    it('should create a credit lot and return its id', async () => {
      const res = await post(`/accounts/${userId}/credits`, { minutes: 150, reason: 'goodwill' });

      expect(res.statusCode).toBe(200);
      const { lotId, after } = res.json();
      expect(after.credits.available).toBe(150);
      expect(await getTestDb().collection('creditLots').findOne({ _id: new ObjectId(lotId) })).toMatchObject({ minutesGranted: 150, expiresAt: null });
    });

    it('should keep an optional expiry', async () => {
      const expiresAt = addMonthsUtc(new Date(), 6).toISOString();

      const res = await post(`/accounts/${userId}/credits`, { minutes: 60, expiresAt, reason: 'promo' });

      const lot = await getTestDb().collection('creditLots').findOne({ _id: new ObjectId(res.json().lotId) });
      expect(lot?.expiresAt).toEqual(new Date(expiresAt));
    });

    it('should reject an expiry in the past with 400', async () => {
      const res = await post(`/accounts/${userId}/credits`, { minutes: 60, expiresAt: '2020-01-01T00:00:00Z', reason: 'x' });

      expect(res.statusCode).toBe(400);
    });

    it.each([0, -5, 1.5, 100_001])('should reject %s minutes with 400', async (minutes) => {
      const res = await post(`/accounts/${userId}/credits`, { minutes, reason: 'x' });

      expect(res.statusCode).toBe(400);
    });

    it('should write an audit row naming the lot', async () => {
      const res = await post(`/accounts/${userId}/credits`, { minutes: 30, reason: 'comp' });

      const [row] = await auditRows();
      expect(row).toMatchObject({
        action: 'billing.credits.grant',
        actorId: TEST_ADMIN_USER_ID,
        targetId: userId,
        after: { lotId: res.json().lotId, minutes: 30, credits: { available: 30 } },
      });
    });
  });

  describe('POST /accounts/:userId/caps', () => {
    it('should set caps and clear one with null', async () => {
      await post(`/accounts/${userId}/caps`, { blocked: true, maxVideoMinutes: 20, reason: 'abuse' });

      const res = await post(`/accounts/${userId}/caps`, { maxVideoMinutes: null, reason: 'relaxed' });

      expect(res.json().after.caps).toEqual({ blocked: true });
    });

    it('should reject a request that sets no cap', async () => {
      const res = await post(`/accounts/${userId}/caps`, { reason: 'nothing' });

      expect(res.statusCode).toBe(400);
    });

    it('should reject an out-of-range cap', async () => {
      const res = await post(`/accounts/${userId}/caps`, { maxVideoMinutes: 10_000, reason: 'x' });

      expect(res.statusCode).toBe(400);
    });

    it('should audit the change with the caps before and after', async () => {
      await post(`/accounts/${userId}/caps`, { blocked: true, reason: 'abuse' });

      const [row] = await auditRows();
      expect(row).toMatchObject({ action: 'billing.caps.set', before: { caps: {} }, after: { caps: { blocked: true } } });
    });
  });

  describe('POST /accounts/:userId/adjust', () => {
    it('should add credit minutes as a new lot', async () => {
      const res = await post(`/accounts/${userId}/adjust`, { bucket: 'credit', minutes: 25, reason: 'correction' });

      expect(res.json().after.credits.available).toBe(25);
    });

    it('should take credit minutes back', async () => {
      await post(`/accounts/${userId}/credits`, { minutes: 50, reason: 'comp' });

      const res = await post(`/accounts/${userId}/adjust`, { bucket: 'credit', minutes: -20, reason: 'clawback' });

      expect(res.json().after.credits.available).toBe(30);
    });

    it('should refuse an adjustment that would go below zero', async () => {
      const res = await post(`/accounts/${userId}/adjust`, { bucket: 'credit', minutes: -5, reason: 'x' });

      expect(res.statusCode).toBe(400);
    });

    it('should reject 0 minutes', async () => {
      const res = await post(`/accounts/${userId}/adjust`, { bucket: 'credit', minutes: 0, reason: 'x' });

      expect(res.statusCode).toBe(400);
    });

    it('should audit the adjustment with its bucket and minutes', async () => {
      await post(`/accounts/${userId}/adjust`, { bucket: 'credit', minutes: 25, reason: 'correction' });

      const [row] = await auditRows();
      expect(row).toMatchObject({ action: 'billing.adjust', after: { bucket: 'credit', minutes: 25 } });
    });
  });

  describe('recurring grants', () => {
    const create = () => post(`/accounts/${userId}/recurring-grants`, { minutes: 300, every: 'month', active: true, reason: 'demo budget' });

    it('should create the grant row with the next run time', async () => {
      const res = await create();

      expect(res.statusCode).toBe(200);
      const row = await getTestDb().collection('recurringGrants').findOne({ _id: new ObjectId(res.json().grantId) });
      expect(row).toMatchObject({ minutes: 300, every: 'month', active: true, lastRunAt: null, actorId: TEST_ADMIN_USER_ID });
      expect(row?.nextRunAt).toBeInstanceOf(Date);
    });

    it('should list the grant on the account', async () => {
      const { grantId } = (await create()).json();

      const res = await send('GET', `/accounts/${userId}`);

      expect(res.json().recurringGrants).toEqual([expect.objectContaining({ grantId, minutes: 300 })]);
    });

    it('should reject a period other than month', async () => {
      const res = await post(`/accounts/${userId}/recurring-grants`, { minutes: 300, every: 'week', active: true, reason: 'x' });

      expect(res.statusCode).toBe(400);
    });

    it('should audit the creation with the grant id and terms', async () => {
      const { grantId } = (await create()).json();

      const [row] = await auditRows();
      expect(row).toMatchObject({
        action: 'billing.recurring.create',
        after: { grantId, grant: { minutes: 300, every: 'month', active: true } },
      });
    });

    it('should delete a grant so it no longer lists, and audit it', async () => {
      const { grantId } = (await create()).json();
      const res = await send('DELETE', `/accounts/${userId}/recurring-grants/${grantId}`, { reason: 'demo over' });

      expect(res.statusCode).toBe(200);
      const detail = (await send('GET', `/accounts/${userId}`)).json();
      expect(detail.recurringGrants).toEqual([]);
      expect((await auditRows()).map((row) => row.action).sort()).toEqual(['billing.recurring.create', 'billing.recurring.delete']);
    });

    it('should answer 404 for an unknown grant', async () => {
      const res = await send('DELETE', `/accounts/${userId}/recurring-grants/${new ObjectId().toHexString()}`, { reason: 'x' });

      expect(res.statusCode).toBe(404);
    });
  });

  describe('retries', () => {
    const KEY = 'dialog-open-0001';
    const grant = (key: string, id = userId) =>
      post(`/accounts/${id}/credits`, { minutes: 30, reason: 'comp' }, { 'idempotency-key': key });

    it('should apply a repeated Idempotency-Key once', async () => {
      const first = await grant(KEY);
      const second = await grant(KEY);

      expect(second.json().auditId).toBe(first.json().auditId);
      expect(second.json().lotId).toBe(first.json().lotId);
      expect(second.json().after.credits.available).toBe(30);
    });

    it('should leave one lot, one ledger entry and one audit row after a retry', async () => {
      const first = await grant(KEY);
      await grant(KEY);

      expect(await getTestDb().collection('creditLots').countDocuments()).toBe(1);
      expect(await ledgerFor(first.json().auditId)).toHaveLength(1);
      expect(await auditRows()).toHaveLength(1);
    });

    it('should treat a different key as a new grant', async () => {
      await grant(KEY);
      const second = await grant('dialog-open-0002');

      expect(second.json().after.credits.available).toBe(60);
    });

    it('should not let a key leak across users', async () => {
      const other = await insertUser(getTestDb());
      await grant(KEY);

      const second = await grant(KEY, other);

      expect(second.json().after.credits.available).toBe(30);
    });

    it('should not let a key leak across actions', async () => {
      await grant(KEY);

      const res = await post(`/accounts/${userId}/adjust`, { bucket: 'credit', minutes: 7, reason: 'x' }, { 'idempotency-key': KEY });

      expect(res.json().after.credits.available).toBe(37);
    });

    it('should give every request its own audit id without a key', async () => {
      const first = await post(`/accounts/${userId}/credits`, { minutes: 5, reason: 'a' });
      const second = await post(`/accounts/${userId}/credits`, { minutes: 5, reason: 'b' });

      expect(second.json().auditId).not.toBe(first.json().auditId);
    });

    describe('a key reused for a different request', () => {
      const withKey = (path: string, body: Record<string, unknown>) => post(path, body, { 'idempotency-key': KEY });

      it('should refuse different credit minutes with 422 IDEMPOTENCY_KEY_REUSED and grant nothing more', async () => {
        await withKey(`/accounts/${userId}/credits`, { minutes: 30, reason: 'comp' });

        const second = await withKey(`/accounts/${userId}/credits`, { minutes: 5000, reason: 'comp' });

        expect([second.statusCode, second.json().error]).toEqual([422, 'IDEMPOTENCY_KEY_REUSED']);
        expect(await getTestDb().collection('creditLots').countDocuments()).toBe(1);
      });

      it('should refuse a different reason too: the request is not the same', async () => {
        await withKey(`/accounts/${userId}/credits`, { minutes: 30, reason: 'comp' });

        const second = await withKey(`/accounts/${userId}/credits`, { minutes: 30, reason: 'another story' });

        expect(second.statusCode).toBe(422);
      });

      it('should refuse a changed cap and leave the first cap in force (setCaps writes no ledger rows)', async () => {
        await withKey(`/accounts/${userId}/caps`, { blocked: true, reason: 'abuse' });

        const second = await withKey(`/accounts/${userId}/caps`, { blocked: false, reason: 'abuse' });

        const detail = (await send('GET', `/accounts/${userId}`)).json();
        expect([second.statusCode, detail.account.caps.blocked]).toEqual([422, true]);
      });

      it('should refuse a different plan and leave the first plan in force', async () => {
        await withKey(`/accounts/${userId}/plan`, { planId: 'pro', reason: 'comp' });

        const second = await withKey(`/accounts/${userId}/plan`, { planId: 'free', reason: 'comp' });

        const detail = (await send('GET', `/accounts/${userId}`)).json();
        expect([second.statusCode, detail.account.plan.id]).toEqual([422, 'pro']);
      });

      it('should write no second audit row for the refused request', async () => {
        await withKey(`/accounts/${userId}/caps`, { blocked: true, reason: 'abuse' });
        await withKey(`/accounts/${userId}/caps`, { blocked: false, reason: 'abuse' });

        expect(await auditRows()).toHaveLength(1);
      });

      it('should still answer an identical retry with the first result', async () => {
        const first = await withKey(`/accounts/${userId}/caps`, { blocked: true, reason: 'abuse' });

        const retry = await withKey(`/accounts/${userId}/caps`, { blocked: true, reason: 'abuse' });

        expect([retry.statusCode, retry.json().auditId]).toEqual([200, first.json().auditId]);
      });

      it('should apply a retry whose audit row was never written, once the first attempt\'s ledger entries exist', async () => {
        vi.spyOn(AdminAuditRepository.prototype, 'insert').mockRejectedValueOnce(new Error('audit down'));
        await withKey(`/accounts/${userId}/credits`, { minutes: 30, reason: 'comp' });

        const retry = await withKey(`/accounts/${userId}/credits`, { minutes: 30, reason: 'comp' });

        expect(retry.statusCode).toBe(200);
        expect(await getTestDb().collection('creditLots').countDocuments()).toBe(1);
        expect(await auditRows()).toHaveLength(1);
      });

      it('should record a hash of the request on the audit row without exposing it in the audit view', async () => {
        await withKey(`/accounts/${userId}/credits`, { minutes: 30, reason: 'comp' });

        const [row] = await auditRows();
        const view = (await app.inject({ method: 'GET', url: '/api/admin/audit', headers: adminActorRequest('GET', '/api/admin/audit').headers })).json();
        expect(row.requestHash).toMatch(/^[0-9a-f]{64}$/);
        expect(view.entries[0]).not.toHaveProperty('requestHash');
      });
    });

    it('should reject a malformed Idempotency-Key', async () => {
      const res = await grant('short');

      expect(res.statusCode).toBe(400);
    });
  });

  describe('failures', () => {
    it('should still succeed when the audit write fails, logging the audit id at error', async () => {
      vi.spyOn(AdminAuditRepository.prototype, 'insert').mockRejectedValueOnce(new Error('audit down'));

      const res = await post(`/accounts/${userId}/credits`, { minutes: 30, reason: 'comp' });

      expect(res.statusCode).toBe(200);
      expect(logger.error).toHaveBeenCalledWith(
        expect.objectContaining({ auditId: res.json().auditId, action: 'billing.credits.grant' }),
        'admin_audit_write_failed',
      );
    });

    it('should keep the ledger change when the audit write fails', async () => {
      vi.spyOn(AdminAuditRepository.prototype, 'insert').mockRejectedValueOnce(new Error('audit down'));

      const res = await post(`/accounts/${userId}/credits`, { minutes: 30, reason: 'comp' });

      expect(await ledgerFor(res.json().auditId)).toHaveLength(1);
    });

    it('should answer 409 ACCOUNT_BUSY when the transaction retries are exhausted', async () => {
      vi.spyOn(billing, 'grantLot').mockRejectedValueOnce(new StaleAccountVersionError());

      const res = await post(`/accounts/${userId}/credits`, { minutes: 30, reason: 'comp' });

      expect(res.statusCode).toBe(409);
      expect(res.json().error).toBe('ACCOUNT_BUSY');
    });

    it('should not write an audit row for a refused mutation', async () => {
      await post(`/accounts/${userId}/credits`, { minutes: -1, reason: 'x' });

      expect(await auditRows()).toEqual([]);
    });
  });
});
