/**
 * The token gate on the four LLM routes a user drives directly — the
 * single-video chat, the library chat, a video action and a library action —
 * over a real BillingService on the test replica set. Each one is admitted
 * on a positive balance before anything is proxied (402 INSUFFICIENT_TOKENS
 * {needed: 1, available}, 403 ACCOUNT_BLOCKED, 503 when the ledger cannot be
 * read: fails closed), and forwards a fresh execution id the assistant
 * reports the turn's cost under (POST /internal/assistant/usage).
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
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

type Upstream = 'chat' | 'libraryChat' | 'action';

type ChatMockContainer = MockContainer & {
  assistantClient: MockContainer['assistantClient'] & { libraryChat: ReturnType<typeof vi.fn>; librarySearch: ReturnType<typeof vi.fn> };
};

interface GatedRoute {
  name: string;
  url: string;
  payload: Record<string, unknown>;
  upstream: Upstream;
}

const ROUTES: GatedRoute[] = [
  { name: 'the single-video chat', url: `/api/videos/${VIDEO_SUMMARY_ID}/chat`, payload: { message: 'What is this about?' }, upstream: 'chat' },
  { name: 'the library chat', url: '/api/assistant/library/chat', payload: { message: 'Which video covers sorting?' }, upstream: 'libraryChat' },
  { name: 'a video action', url: `/api/videos/${VIDEO_SUMMARY_ID}/action`, payload: { action: 'quiz_me' }, upstream: 'action' },
  { name: 'a library action', url: '/api/assistant/action', payload: { action: 'organize_library' }, upstream: 'action' },
];

function sseStream(): ReadableStream<Uint8Array> {
  return new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode('data: [DONE]\n\n'));
      controller.close();
    },
  });
}

describe('the token gate on chat turns and actions', () => {
  let app: FastifyInstance;
  let mockContainer: ChatMockContainer;
  let billing: BillingService;
  let userId: string;

  // The routes read `billingService` when they register, so the real one must exist before the app.
  beforeAll(async () => {
    await createBillingIndexes(getTestDb());
    billing = new BillingService(new BillingRepository(getTestDb()), new UserRepository(getTestDb()), mockLogger as unknown as FastifyBaseLogger);
    const base = createMockContainer();
    mockContainer = {
      ...base,
      billingService: billing,
      assistantClient: { ...base.assistantClient, libraryChat: vi.fn(), librarySearch: vi.fn() },
    } as unknown as ChatMockContainer;
    app = await buildTestApp(mockContainer);
    await app.ready();
  });

  afterAll(async () => {
    await app.close();
  });

  beforeEach(async () => {
    vi.clearAllMocks();
    mockContainer.videoRepository.userHasAccessToSummary.mockResolvedValue(true);
    mockContainer.assistantClient.chat.mockImplementation(async () => sseStream());
    mockContainer.assistantClient.libraryChat.mockImplementation(async () => sseStream());
    mockContainer.assistantClient.action.mockResolvedValue({ status: 200, body: { success: true } });
    mockContainer.assistantClient.librarySearch.mockResolvedValue({ status: 200, body: { results: [] } });
    userId = await insertUser(getTestDb());
  });

  const send = (route: GatedRoute) => {
    const token = app.jwt.sign({ userId, email: 'chat@example.com', type: 'access' });
    return app.inject({ method: 'POST', url: route.url, headers: { authorization: `Bearer ${token}` }, payload: route.payload });
  };
  const upstreamOf = (route: GatedRoute): ReturnType<typeof vi.fn> => mockContainer.assistantClient[route.upstream];
  /** Spend the account down: a reported turn of `costUsd` (1/3 $ = the whole 1,000-token signup grant). */
  const spend = (costUsd: number) => billing.debitExecution({ userId, executionId: 'exec-spend', kind: 'chat', requestId: 'req-spend', costUsd });

  describe.each(ROUTES)('$name', (route) => {
    it('should let a user with tokens through, forwarding a fresh execution id with the user', async () => {
      const response = await send(route);

      expect(response.statusCode).toBe(200);
      expect(upstreamOf(route)).toHaveBeenCalledWith(expect.objectContaining({ userId, executionId: expect.stringMatching(UUID) }));
    });

    it('should mint a new execution id for every request', async () => {
      await send(route);
      await send(route);

      const ids = upstreamOf(route).mock.calls.map((call) => call[0].executionId);
      expect(new Set(ids).size).toBe(2);
    });

    it('should answer 402 INSUFFICIENT_TOKENS {needed: 1, available: 0} at a zero balance, before the assistant is called', async () => {
      await spend(1 / 3);

      const response = await send(route);

      expect([response.statusCode, response.json()]).toEqual([402, expect.objectContaining({ error: 'INSUFFICIENT_TOKENS', needed: 1, available: 0 })]);
      expect(upstreamOf(route)).not.toHaveBeenCalled();
    });

    it('should answer 402 with the negative balance of an account in debt', async () => {
      await spend(0.5);

      const response = await send(route);

      expect([response.statusCode, response.json().needed, response.json().available]).toEqual([402, 1, -500]);
      expect(upstreamOf(route)).not.toHaveBeenCalled();
    });

    it('should answer 403 ACCOUNT_BLOCKED to a blocked account, before the assistant is called', async () => {
      await billing.setCaps(userId, { blocked: true }, { actorId: 'system:test', reason: 'abuse', auditId: new ObjectId().toHexString() });

      const response = await send(route);

      expect([response.statusCode, response.json().error]).toEqual([403, 'ACCOUNT_BLOCKED']);
      expect(upstreamOf(route)).not.toHaveBeenCalled();
    });

    it('should answer 503 when the ledger cannot be read, never a free turn', async () => {
      vi.spyOn(billing, 'admitExecution').mockRejectedValueOnce(new Error('mongo: connection reset'));

      const response = await send(route);

      expect([response.statusCode, response.json().error]).toEqual([503, 'SERVICE_UNAVAILABLE']);
      expect(upstreamOf(route)).not.toHaveBeenCalled();
    });
  });

  it('should not consult the ledger for a chat refused for another reason (no access to the video)', async () => {
    mockContainer.videoRepository.userHasAccessToSummary.mockResolvedValue(false);
    const admit = vi.spyOn(billing, 'admitExecution');

    const response = await send(ROUTES[0]);

    expect([response.statusCode, admit.mock.calls.length]).toEqual([404, 0]);
  });

  it('should leave the library search ungated: it runs no LLM', async () => {
    await spend(0.5);
    const token = app.jwt.sign({ userId, email: 'chat@example.com', type: 'access' });

    const response = await app.inject({
      method: 'POST', url: '/api/assistant/library/search', headers: { authorization: `Bearer ${token}` }, payload: { query: 'sorting' },
    });

    expect(response.statusCode).toBe(200);
    expect(mockContainer.assistantClient.librarySearch.mock.calls[0][0]).not.toHaveProperty('executionId');
  });

  it('should debit the turn under the execution id it forwarded, once the assistant reports the cost', async () => {
    await send(ROUTES[0]);
    const { executionId, requestId } = mockContainer.assistantClient.chat.mock.calls[0][0];

    const response = await app.inject({
      method: 'POST', url: '/internal/assistant/usage',
      headers: { 'content-type': 'application/json', 'x-internal-secret': INTERNAL_SECRET, 'x-user-id': userId },
      payload: { executionId, requestId, kind: 'chat', costUsd: 0.01 },
    });

    expect(response.json()).toMatchObject({ status: 'debited', tokens: 30 });
    expect((await billing.summary(userId)).balance).toBe(970);
  });
});
