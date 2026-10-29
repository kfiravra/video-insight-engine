import { describe, it, expect, beforeAll, beforeEach } from 'vitest';
import type { FastifyInstance } from 'fastify';
import { ObjectId } from 'mongodb';

import { config } from '../../../config.js';
import { addMonthsUtc } from '../../../services/billing/minutes.js';
import { TEST_ADMIN_USER_ID } from '../../../test/helpers.js';
import { getTestDb } from '../../../test/setup.js';
import { BASE, useBillingRoutesApp } from './billing-routes.harness.js';
import type { Method } from './billing-routes.harness.js';

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

describe('admin billing routes', () => {
  const ctx = useBillingRoutesApp();
  const { mocks, send, post, auditRows, ledgerFor } = ctx;
  let app: FastifyInstance;
  let userId: string;

  beforeAll(() => {
    app = ctx.app;
  });

  beforeEach(() => {
    userId = ctx.userId;
  });

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
});
