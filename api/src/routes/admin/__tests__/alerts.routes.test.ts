import { describe, it, expect, beforeAll, afterAll, beforeEach, vi } from 'vitest';
import { FastifyInstance } from 'fastify';
import {
  adminActorRequest,
  buildTestApp,
  createMockContainer,
  TEST_ADMIN_USER_ID,
  type MockContainer,
} from '../../../test/helpers.js';
import { config } from '../../../config.js';

const URL_PATH = '/api/admin/alerts/config';
const THRESHOLDS = { cost_threshold_usd: 1.5, daily_spike_multiplier: 3, failure_rate_threshold: 0.1 };
const BEFORE = { cost_threshold_usd: 0.5, daily_spike_multiplier: 2, failure_rate_threshold: 0.2 };

describe('admin alerts routes', () => {
  let app: FastifyInstance;
  let mocks: MockContainer;

  beforeAll(async () => {
    mocks = createMockContainer();
    app = await buildTestApp(mocks);
    await app.ready();
  });

  afterAll(async () => {
    await app.close();
  });

  beforeEach(() => {
    vi.clearAllMocks();
    mocks.userRepository.hasPermission.mockResolvedValue(true);
    mocks.adminReplayGuard.claim.mockResolvedValue(true);
    mocks.alertConfigService.replace.mockResolvedValue({ before: BEFORE, after: THRESHOLDS });
    mocks.auditService.recordBestEffort.mockResolvedValue('audit-1');
  });

  /** The request vie-admin makes: signed over the method, the exact target and the body. */
  function save(body: Record<string, unknown> = { ...THRESHOLDS, reason: 'noisy week' }) {
    const signed = adminActorRequest('POST', URL_PATH, body);
    return app.inject({ method: 'POST', url: URL_PATH, headers: signed.headers, payload: signed.payload });
  }

  describe('who may change the thresholds', () => {
    it('should answer 401 without credentials', async () => {
      const res = await app.inject({ method: 'POST', url: URL_PATH, payload: { ...THRESHOLDS, reason: 'x' } });

      expect(res.statusCode).toBe(401);
    });

    it('should refuse the shared key alone: a change needs a named admin', async () => {
      const res = await app.inject({
        method: 'POST',
        url: URL_PATH,
        headers: { 'x-admin-key': config.ADMIN_API_KEY },
        payload: { ...THRESHOLDS, reason: 'x' },
      });

      expect(res.statusCode).toBe(403);
      expect(mocks.alertConfigService.replace).not.toHaveBeenCalled();
    });

    it('should refuse an admin who does not hold alerts:write', async () => {
      mocks.userRepository.hasPermission.mockResolvedValue(false);

      const res = await save();

      expect(res.statusCode).toBe(403);
      expect(mocks.alertConfigService.replace).not.toHaveBeenCalled();
    });

    it('should check the signed admin against alerts:write', async () => {
      await save();

      expect(mocks.userRepository.hasPermission).toHaveBeenCalledWith(TEST_ADMIN_USER_ID, 'alerts:write');
    });

    it('should refuse a request whose body was changed after signing', async () => {
      const signed = adminActorRequest('POST', URL_PATH, { ...THRESHOLDS, reason: 'noisy week' });

      const res = await app.inject({
        method: 'POST',
        url: URL_PATH,
        headers: signed.headers,
        payload: JSON.stringify({ ...THRESHOLDS, cost_threshold_usd: 0, reason: 'noisy week' }),
      });

      expect(res.statusCode).toBe(401);
      expect(mocks.alertConfigService.replace).not.toHaveBeenCalled();
    });
  });

  describe('saving the thresholds', () => {
    it('should replace the three thresholds and leave the reason out of them', async () => {
      await save();

      expect(mocks.alertConfigService.replace).toHaveBeenCalledWith(THRESHOLDS);
    });

    it('should answer with what the thresholds were, what they are, and the audit id', async () => {
      const res = await save();

      expect(res.json()).toEqual({ before: BEFORE, after: THRESHOLDS, auditId: 'audit-1' });
    });

    it('should audit the change with the signed admin, before and after, and the trimmed reason', async () => {
      await save({ ...THRESHOLDS, reason: '  noisy week  ' });

      expect(mocks.auditService.recordBestEffort).toHaveBeenCalledWith({
        actorId: TEST_ADMIN_USER_ID,
        requestId: expect.any(String),
        ip: expect.any(String),
        action: 'alerts.config.set',
        targetType: 'alert',
        targetId: 'default',
        before: BEFORE,
        after: THRESHOLDS,
        reason: 'noisy week',
      });
    });

    it('should keep the change when the audit write fails, answering with no audit id', async () => {
      mocks.auditService.recordBestEffort.mockResolvedValue(null);

      const res = await save();

      expect([res.statusCode, res.json().auditId]).toEqual([200, null]);
    });

    it('should not audit a change that did not happen', async () => {
      mocks.alertConfigService.replace.mockRejectedValue(new Error('mongo down'));

      const res = await save();

      expect(res.statusCode).toBe(500);
      expect(mocks.auditService.recordBestEffort).not.toHaveBeenCalled();
    });

    it('should accept the boundary values: free calls, a 1x multiplier, a 0 and a 1 failure rate', async () => {
      expect((await save({ cost_threshold_usd: 0, daily_spike_multiplier: 1, failure_rate_threshold: 0, reason: 'r' })).statusCode).toBe(200);
      expect((await save({ cost_threshold_usd: 0, daily_spike_multiplier: 1, failure_rate_threshold: 1, reason: 'r' })).statusCode).toBe(200);
    });
  });

  describe('what it refuses', () => {
    const invalid: Array<[string, Record<string, unknown>]> = [
      ['no reason', { ...THRESHOLDS }],
      ['a blank reason', { ...THRESHOLDS, reason: '   ' }],
      ['a reason over 500 characters', { ...THRESHOLDS, reason: 'x'.repeat(501) }],
      ['a negative cost threshold', { ...THRESHOLDS, cost_threshold_usd: -0.01, reason: 'r' }],
      ['a spike multiplier under 1', { ...THRESHOLDS, daily_spike_multiplier: 0.99, reason: 'r' }],
      ['a failure rate over 1', { ...THRESHOLDS, failure_rate_threshold: 1.01, reason: 'r' }],
      ['a failure rate under 0', { ...THRESHOLDS, failure_rate_threshold: -0.1, reason: 'r' }],
      ['a threshold sent as a string', { ...THRESHOLDS, cost_threshold_usd: '1.5', reason: 'r' }],
      ['a missing threshold', { cost_threshold_usd: 1, daily_spike_multiplier: 2, reason: 'r' }],
      ['a field it does not know', { ...THRESHOLDS, reason: 'r', updated_at: '2020-01-01' }],
    ];

    it.each(invalid)('should answer 400 to %s, changing and auditing nothing', async (_name, body) => {
      const res = await save(body);

      expect(res.statusCode).toBe(400);
      expect(mocks.alertConfigService.replace).not.toHaveBeenCalled();
      expect(mocks.auditService.recordBestEffort).not.toHaveBeenCalled();
    });
  });
});
