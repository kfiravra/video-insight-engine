import { describe, it, expect, beforeAll, beforeEach, vi } from 'vitest';
import type { FastifyInstance } from 'fastify';

import { AdminAuditRepository } from '../../../repositories/admin-audit.repository.js';
import { StaleAccountVersionError } from '../../../repositories/billing.repository.js';
import { insertUser } from '../../../services/billing/__tests__/billing-fixtures.js';
import type { BillingService } from '../../../services/billing/billing.service.js';
import { adminActorRequest } from '../../../test/helpers.js';
import { getTestDb } from '../../../test/setup.js';
import { useBillingRoutesApp } from './billing-routes.harness.js';

/**
 * What happens when an admin billing mutation is repeated or fails part-way:
 * the Idempotency-Key (one effect per key, a reused key refused), and the audit
 * write or the transaction failing after the ledger committed. Split from
 * billing.routes.test.ts, which covers each route's own contract.
 */
describe('admin billing routes: retries and failures', () => {
  const ctx = useBillingRoutesApp();
  const { logger, send, post, auditRows, ledgerFor } = ctx;
  let app: FastifyInstance;
  let billing: BillingService;
  let userId: string;

  beforeAll(() => {
    app = ctx.app;
    billing = ctx.billing;
  });

  beforeEach(() => {
    userId = ctx.userId;
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
