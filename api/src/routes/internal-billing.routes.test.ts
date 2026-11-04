/**
 * The token ledger's two cost hand-backs (internal-billing.routes.ts, D13/D14):
 * POST /internal/run-cost (the summarizer, a run's total LLM $) and POST
 * /internal/assistant/usage (the assistant, one chat turn / library chat /
 * action). Both need INTERNAL_SECRET. The wiring is checked on the mock
 * container; the usage debit over a real BillingService. The run-cost settle
 * over a real ledger is billing-enforce.routes.test.ts.
 */
import { describe, it, expect, beforeAll, afterAll, beforeEach, vi } from 'vitest';
import { ObjectId } from 'mongodb';
import type { FastifyBaseLogger, FastifyInstance } from 'fastify';
import { buildTestApp, createMockContainer, type MockContainer } from '../test/helpers.js';
import { getTestDb, mockLogger } from '../test/setup.js';
import { createBillingIndexes } from '../plugins/mongodb.js';
import { BillingRepository } from '../repositories/billing.repository.js';
import { UserRepository } from '../repositories/user.repository.js';
import { BillingService } from '../services/billing/billing.service.js';
import { insertUser } from '../services/billing/__tests__/billing-fixtures.js';

const INTERNAL_SECRET = 'dev-internal-secret-change-me';
const VIDEO_SUMMARY_ID = '507f1f77bcf86cd799439011';
const USER_ID = 'aaaaaaaaaaaaaaaaaaaaaaaa';
const OTHER_USER_ID = 'bbbbbbbbbbbbbbbbbbbbbbbb';

function post(app: FastifyInstance, url: string, payload: Record<string, unknown>, headers: Record<string, string>) {
  return app.inject({ method: 'POST', url, headers: { 'content-type': 'application/json', ...headers }, payload });
}

describe('internal billing routes', () => {
  let app: FastifyInstance;
  let mockContainer: MockContainer;

  beforeAll(async () => {
    mockContainer = createMockContainer();
    app = await buildTestApp(mockContainer);
    await app.ready();
  });

  afterAll(async () => {
    await app.close();
  });

  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('POST /internal/run-cost', () => {
    const runCost = (payload: Record<string, unknown>, headers: Record<string, string> = { 'x-internal-secret': INTERNAL_SECRET }) =>
      post(app, '/internal/run-cost', payload, headers);

    it.each([
      ['no secret', {}],
      ['a wrong secret', { 'x-internal-secret': 'wrong-secret' }],
    ])('should answer 401 with %s and settle nothing', async (_label, headers) => {
      const response = await runCost({ videoSummaryId: VIDEO_SUMMARY_ID, billingRunId: 'run-1', costUsd: 0.25 }, headers);

      expect(response.statusCode).toBe(401);
      expect(mockContainer.billingEnforcement.runCosted).not.toHaveBeenCalled();
    });

    it('should settle the stamped run on the reported cost and answer the outcome', async () => {
      mockContainer.billingEnforcement.runCosted.mockResolvedValueOnce('debited');

      const response = await runCost({ videoSummaryId: VIDEO_SUMMARY_ID, billingRunId: 'run-1', costUsd: 0.25, llmCalls: 14 });

      expect([response.statusCode, response.json()]).toEqual([200, { received: true, status: 'debited' }]);
      expect(mockContainer.billingEnforcement.runCosted).toHaveBeenCalledWith(VIDEO_SUMMARY_ID, 'run-1', 0.25);
    });

    it.each([
      ['absent', {}],
      ['null', { billingRunId: null }],
    ])('should pass no run id when billingRunId is %s (a row stamped before it existed)', async (_label, extra) => {
      await runCost({ videoSummaryId: VIDEO_SUMMARY_ID, costUsd: 0.25, ...extra });

      expect(mockContainer.billingEnforcement.runCosted).toHaveBeenCalledWith(VIDEO_SUMMARY_ID, null, 0.25);
    });

    it.each(['ignored', 'already_debited', 'no_hold'])('should answer 200 with %s, so the summarizer does not retry', async (status) => {
      mockContainer.billingEnforcement.runCosted.mockResolvedValueOnce(status);

      const response = await runCost({ videoSummaryId: VIDEO_SUMMARY_ID, billingRunId: 'run-1', costUsd: 0.25 });

      expect([response.statusCode, response.json().status]).toEqual([200, status]);
    });

    it.each([
      ['no cost', { videoSummaryId: VIDEO_SUMMARY_ID }],
      ['a negative cost', { videoSummaryId: VIDEO_SUMMARY_ID, costUsd: -0.1 }],
      ['a cost that is not a number', { videoSummaryId: VIDEO_SUMMARY_ID, costUsd: '0.25' }],
      ['a malformed video id', { videoSummaryId: 'not-an-id', costUsd: 0.25 }],
      ['an empty run id', { videoSummaryId: VIDEO_SUMMARY_ID, billingRunId: '', costUsd: 0.25 }],
    ])('should answer 400 for %s', async (_label, payload) => {
      const response = await runCost(payload);

      expect(response.statusCode).toBe(400);
      expect(mockContainer.billingEnforcement.runCosted).not.toHaveBeenCalled();
    });
  });

  describe('POST /internal/assistant/usage', () => {
    const usageBody = { executionId: 'exec-1', requestId: 'req-1', kind: 'chat', costUsd: 0.01, calls: 2 };
    const usage = (payload: Record<string, unknown> = usageBody, headers: Record<string, string> = {}) =>
      post(app, '/internal/assistant/usage', payload, { 'x-internal-secret': INTERNAL_SECRET, 'x-user-id': USER_ID, ...headers });

    it('should answer 401 without the secret', async () => {
      const response = await post(app, '/internal/assistant/usage', usageBody, { 'x-user-id': USER_ID });

      expect(response.statusCode).toBe(401);
      expect(mockContainer.billingService.debitExecution).not.toHaveBeenCalled();
    });

    it('should answer 401 without X-User-Id', async () => {
      const response = await post(app, '/internal/assistant/usage', usageBody, { 'x-internal-secret': INTERNAL_SECRET });

      expect(response.statusCode).toBe(401);
      expect(mockContainer.billingService.debitExecution).not.toHaveBeenCalled();
    });

    it('should debit the user named by X-User-Id (D14)', async () => {
      const response = await usage();

      expect([response.statusCode, response.json()]).toEqual([200, { received: true, status: 'debited', tokens: 1 }]);
      expect(mockContainer.billingService.debitExecution).toHaveBeenCalledWith({
        userId: USER_ID, executionId: 'exec-1', kind: 'chat', requestId: 'req-1', costUsd: 0.01,
      });
    });

    it('should refuse a body that names a user: the user is X-User-Id only (D14)', async () => {
      const response = await usage({ ...usageBody, userId: OTHER_USER_ID });

      expect(response.statusCode).toBe(400);
      expect(mockContainer.billingService.debitExecution).not.toHaveBeenCalled();
    });

    it.each(['chat', 'library_chat', 'action'])('should accept a %s execution', async (kind) => {
      expect((await usage({ ...usageBody, kind })).statusCode).toBe(200);
    });

    it.each([
      ['a video kind (videos settle through run-cost)', { ...usageBody, kind: 'video' }],
      ['no execution id', { ...usageBody, executionId: undefined }],
      ['no request id', { ...usageBody, requestId: undefined }],
      ['a negative cost', { ...usageBody, costUsd: -1 }],
    ])('should answer 400 for %s', async (_label, payload) => {
      const response = await usage(payload);

      expect(response.statusCode).toBe(400);
      expect(mockContainer.billingService.debitExecution).not.toHaveBeenCalled();
    });
  });
});

describe('POST /internal/assistant/usage over the token ledger', () => {
  let app: FastifyInstance;
  let billing: BillingService;
  let userId: string;

  const entries = (executionId: string) => getTestDb().collection('ledgerEntries').find({ executionId }).toArray();
  const usage = (payload: Record<string, unknown>, user = userId) => post(
    app, '/internal/assistant/usage', { executionId: 'exec-1', requestId: 'req-1', kind: 'chat', ...payload },
    { 'x-internal-secret': INTERNAL_SECRET, 'x-user-id': user },
  );

  beforeAll(async () => {
    await createBillingIndexes(getTestDb());
    billing = new BillingService(new BillingRepository(getTestDb()), new UserRepository(getTestDb()), mockLogger as unknown as FastifyBaseLogger);
    app = await buildTestApp({ ...createMockContainer(), billingService: billing } as unknown as Partial<MockContainer>);
    await app.ready();
  });

  afterAll(async () => {
    await app.close();
  });

  beforeEach(async () => {
    vi.clearAllMocks();
    userId = await insertUser(getTestDb());
  });

  it('should debit the execution its real cost once, however many times the assistant reports it', async () => {
    const responses = [await usage({ costUsd: 0.0123 }), await usage({ costUsd: 0.0123 })];

    expect(responses.map((r) => [r.statusCode, r.json().status, r.json().tokens])).toEqual([[200, 'debited', 37], [200, 'already_debited', 37]]);
    expect((await entries('exec-1')).map((e) => [e.type, e.tokens, e.executionKind, e.source.ref])).toEqual([['debit', -37, 'chat', 'req-1']]);
    expect((await billing.summary(userId)).balance).toBe(963);
  });

  it('should debit two executions of one user apart', async () => {
    await usage({ executionId: 'exec-1', costUsd: 0.01 });
    await usage({ executionId: 'exec-2', requestId: 'req-2', kind: 'action', costUsd: 0.01 });

    expect((await billing.summary(userId)).balance).toBe(940);
  });

  it('should write nothing for a free execution (cost 0)', async () => {
    const response = await usage({ costUsd: 0 });

    expect(response.json()).toEqual({ received: true, status: 'free', tokens: 0 });
    expect(await entries('exec-1')).toEqual([]);
    expect((await billing.summary(userId)).balance).toBe(1000);
  });

  it('should take a turn that cost more than the balance into debt (the turn already happened)', async () => {
    await usage({ costUsd: 0.5 });

    expect(await billing.summary(userId)).toMatchObject({ balance: -500, debt: 500 });
    expect((await billing.verifyAccount(userId)).drift).toEqual([]);
  });

  it('should answer 404 for a user that does not exist, opening no account (D23)', async () => {
    const ghost = new ObjectId().toHexString();

    const response = await usage({ costUsd: 0.01 }, ghost);

    expect(response.statusCode).toBe(404);
    expect(await getTestDb().collection('billingAccounts').countDocuments({ ownerId: new ObjectId(ghost) })).toBe(0);
  });
});
