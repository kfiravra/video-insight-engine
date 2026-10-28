import { describe, it, expect, beforeAll, afterAll, beforeEach, vi } from 'vitest';
import { ObjectId } from 'mongodb';
import type { FastifyBaseLogger, FastifyInstance } from 'fastify';
import type { Redis } from 'ioredis';
import { buildTestApp, createMockContainer, type MockContainer } from '../test/helpers.js';
import { getTestDb, mockLogger } from '../test/setup.js';
import { createBillingIndexes } from '../plugins/mongodb.js';
import { BillingRepository } from '../repositories/billing.repository.js';
import { UserRepository } from '../repositories/user.repository.js';
import { BillingService } from '../services/billing/billing.service.js';
import { ChatFairUseService } from '../services/chat-fair-use.service.js';
import { insertUser, makeClock } from '../services/billing/__tests__/billing-fixtures.js';
import type { TestClock } from '../services/billing/__tests__/billing-fixtures.js';

const VIDEO_SUMMARY_ID = '507f1f77bcf86cd799439011';
const LIMIT = 50;
const DAY_MS = 24 * 60 * 60 * 1000;

type ChatMockContainer = MockContainer & {
  videoRepository: MockContainer['videoRepository'] & { getUserVideos: ReturnType<typeof vi.fn> };
  assistantClient: MockContainer['assistantClient'] & { libraryChat: ReturnType<typeof vi.fn> };
};

/** The Lua rule in memory (see chat-fair-use.service.test.ts); the script itself is checked against a real Redis. */
function rollingWindowRedis(): { eval: ReturnType<typeof vi.fn> } {
  const sets = new Map<string, number[]>();
  return {
    eval: vi.fn(async (_script: string, _keys: number, key: string, now: number, window: number, limit: number) => {
      const live = (sets.get(key) ?? []).filter((score) => score > now - window);
      if (live.length >= limit) {
        sets.set(key, live);
        return [0, live.length, Math.min(...live)];
      }
      live.push(now);
      sets.set(key, live);
      return [1, live.length, 0];
    }),
  };
}

function emptyStream(): ReadableStream<Uint8Array> {
  return new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode('data: [DONE]\n\n'));
      controller.close();
    },
  });
}

describe('free chat fair use on the chat routes', () => {
  let app: FastifyInstance;
  let mockContainer: ChatMockContainer;
  let clock: TestClock;
  let billing: BillingService;
  let redis: { eval: ReturnType<typeof vi.fn> };
  let guard: ChatFairUseService;
  let userId: string;

  // The routes read `chatFairUse` when they register, so the real service (and
  // the clock the tests move) must exist before the app.
  beforeAll(async () => {
    await createBillingIndexes(getTestDb());
    clock = makeClock('2026-11-01T10:00:00Z');
    billing = new BillingService(
      new BillingRepository(getTestDb()),
      new UserRepository(getTestDb()),
      mockLogger as unknown as FastifyBaseLogger,
      clock.now,
    );
    redis = rollingWindowRedis();
    const base = createMockContainer();
    mockContainer = {
      ...base,
      videoRepository: { ...base.videoRepository, getUserVideos: vi.fn().mockResolvedValue([]) },
      assistantClient: { ...base.assistantClient, libraryChat: vi.fn() },
    } as ChatMockContainer;
    guard = new ChatFairUseService(
      redis as unknown as Redis,
      billing,
      LIMIT,
      mockLogger as unknown as FastifyBaseLogger,
      () => clock.now().getTime(),
    );
    mockContainer.chatFairUse.consume.mockImplementation((id: string) => guard.consume(id));
    app = await buildTestApp(mockContainer);
    await app.ready();
  });

  afterAll(async () => {
    await app.close();
  });

  beforeEach(async () => {
    vi.clearAllMocks();
    clock.set('2026-11-01T10:00:00Z');
    mockContainer.chatFairUse.consume.mockImplementation((id: string) => guard.consume(id));
    mockContainer.videoRepository.userHasAccessToSummary.mockResolvedValue(true);
    mockContainer.assistantClient.chat.mockImplementation(async () => emptyStream());
    mockContainer.assistantClient.libraryChat.mockImplementation(async () => emptyStream());
    userId = await insertUser(getTestDb());
  });

  function authFor(id: string): Record<string, string> {
    const token = app.jwt.sign({ userId: id, email: 'chat@example.com', type: 'access' });
    return { authorization: `Bearer ${token}` };
  }

  function sendVideoChat(id = userId): ReturnType<FastifyInstance['inject']> {
    return app.inject({
      method: 'POST',
      url: `/api/videos/${VIDEO_SUMMARY_ID}/chat`,
      headers: authFor(id),
      payload: { message: 'What is this about?' },
    });
  }

  function sendLibraryChat(id = userId): ReturnType<FastifyInstance['inject']> {
    return app.inject({
      method: 'POST',
      url: '/api/assistant/library/chat',
      headers: authFor(id),
      payload: { message: 'Which video covers sorting?' },
    });
  }

  async function useUpAllowanceAcrossBothRoutes(id = userId): Promise<void> {
    for (let i = 0; i < LIMIT; i += 1) {
      const response = i % 2 === 0 ? await sendVideoChat(id) : await sendLibraryChat(id);
      expect(response.statusCode).toBe(200);
      clock.advance(1000);
    }
  }

  describe('a free account', () => {
    it('should answer 429 CHAT_LIMIT_REACHED to the 51st single-video message', async () => {
      await useUpAllowanceAcrossBothRoutes();

      const response = await sendVideoChat();

      expect(response.statusCode).toBe(429);
      expect(response.json().error).toBe('CHAT_LIMIT_REACHED');
    });

    it('should answer 429 CHAT_LIMIT_REACHED to the 51st library message', async () => {
      await useUpAllowanceAcrossBothRoutes();

      const response = await sendLibraryChat();

      expect(response.statusCode).toBe(429);
      expect(response.json().error).toBe('CHAT_LIMIT_REACHED');
    });

    it('should count both chat routes against one allowance', async () => {
      for (let i = 0; i < LIMIT - 1; i += 1) {
        await sendVideoChat();
      }
      const lastFree = await sendLibraryChat();

      const overLimit = await sendVideoChat();

      expect(lastFree.statusCode).toBe(200);
      expect(overLimit.statusCode).toBe(429);
    });

    it('should send nextAt, 24 hours after the first message, in the envelope', async () => {
      const firstMessageAt = clock.now().getTime();
      await useUpAllowanceAcrossBothRoutes();

      const response = await sendVideoChat();

      expect(response.json()).toEqual({
        error: 'CHAT_LIMIT_REACHED',
        message: 'Free accounts can send 50 chat messages in 24 hours. More open up as the last 24 hours pass.',
        statusCode: 429,
        nextAt: new Date(firstMessageAt + DAY_MS).toISOString(),
      });
    });

    it('should not call the assistant for a refused message', async () => {
      await useUpAllowanceAcrossBothRoutes();
      mockContainer.assistantClient.chat.mockClear();

      await sendVideoChat();

      expect(mockContainer.assistantClient.chat).not.toHaveBeenCalled();
    });

    it('should accept messages again once the oldest has aged out of 24 hours', async () => {
      await useUpAllowanceAcrossBothRoutes();
      clock.advance(DAY_MS);

      const response = await sendLibraryChat();

      expect(response.statusCode).toBe(200);
    });

    it('should leave another free account with its own allowance', async () => {
      await useUpAllowanceAcrossBothRoutes();
      const other = await insertUser(getTestDb());

      const response = await sendVideoChat(other);

      expect(response.statusCode).toBe(200);
    });

    it('should leave the minutes ledger and the account untouched', async () => {
      await useUpAllowanceAcrossBothRoutes();

      const ledger = await getTestDb().collection('ledgerEntries').countDocuments({ userId: new ObjectId(userId) });
      const detail = await billing.getAccountDetail(userId);

      expect(ledger).toBe(0);
      expect(detail.account.version).toBe(0);
      expect(detail.holds).toEqual([]);
    });
  });

  describe('a Pro account', () => {
    it('should send as many messages as it likes', async () => {
      await billing.setPlan(
        userId,
        { planId: 'pro' },
        { actorId: new ObjectId().toHexString(), reason: 'chat test', auditId: new ObjectId().toHexString() },
      );

      for (let i = 0; i < LIMIT * 2 + 1; i += 1) {
        const response = i % 2 === 0 ? await sendVideoChat() : await sendLibraryChat();
        expect(response.statusCode).toBe(200);
      }
    });
  });

  describe('when Redis is down', () => {
    it('should keep answering chat messages', async () => {
      for (let i = 0; i < 3; i += 1) {
        redis.eval.mockRejectedValueOnce(new Error('Connection is closed.'));
      }

      const responses = [await sendVideoChat(), await sendLibraryChat(), await sendVideoChat()];

      expect(responses.map((response) => response.statusCode)).toEqual([200, 200, 200]);
    });
  });

  describe('routes that are not chat messages', () => {
    it('should not count the assistant action route', async () => {
      mockContainer.assistantClient.action.mockResolvedValue({ status: 200, body: {} });

      await app.inject({
        method: 'POST',
        url: `/api/videos/${VIDEO_SUMMARY_ID}/action`,
        headers: authFor(userId),
        payload: { action: 'quiz_me' },
      });

      expect(mockContainer.chatFairUse.consume).not.toHaveBeenCalled();
    });
  });
});
