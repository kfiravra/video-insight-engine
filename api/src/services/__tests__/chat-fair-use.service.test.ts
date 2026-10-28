import { describe, it, expect, beforeEach, vi } from 'vitest';
import type { FastifyBaseLogger } from 'fastify';
import type { Redis } from 'ioredis';
import {
  ChatFairUseService,
  chatFairUseKey,
  noOpChatFairUse,
  type PlanLookup,
} from '../chat-fair-use.service.js';
import { ChatLimitReachedError } from '../../utils/errors.js';

const USER_ID = '6a7b2f6abacdb32871507996';
const LIMIT = 50;
const MINUTE = 60 * 1000;
const DAY_MS = 24 * 60 * MINUTE;
const START = Date.parse('2026-11-01T10:00:00Z');

function makeLogger(): FastifyBaseLogger {
  return { info: vi.fn(), warn: vi.fn(), error: vi.fn(), debug: vi.fn() } as unknown as FastifyBaseLogger;
}

/**
 * The same rule as the Lua script, kept in memory: one member per admitted
 * message scored by time; entries older than the window drop; a message is
 * admitted while fewer than `limit` remain; the oldest score decides `nextAt`.
 * The script itself is exercised against a real Redis outside the suite.
 */
function rollingWindowRedis(): { eval: ReturnType<typeof vi.fn>; sets: Map<string, number[]> } {
  const sets = new Map<string, number[]>();
  const evalFn = vi.fn(
    async (_script: string, _keys: number, key: string, now: number, window: number, limit: number) => {
      const live = (sets.get(key) ?? []).filter((score) => score > now - window);
      if (live.length >= limit) {
        sets.set(key, live);
        return [0, live.length, Math.min(...live)];
      }
      live.push(now);
      sets.set(key, live);
      return [1, live.length, 0];
    },
  );
  return { eval: evalFn, sets };
}

function plans(planId: 'free' | 'pro'): PlanLookup & { summary: ReturnType<typeof vi.fn> } {
  return { summary: vi.fn().mockResolvedValue({ plan: { id: planId, periodEnd: null } }) };
}

describe('ChatFairUseService', () => {
  let logger: FastifyBaseLogger;
  let now: number;
  const clock = () => now;

  beforeEach(() => {
    logger = makeLogger();
    now = START;
  });

  function freeService(redis = rollingWindowRedis(), limit = LIMIT): ChatFairUseService {
    return new ChatFairUseService(redis as unknown as Redis, plans('free'), limit, logger, clock);
  }

  async function sendMessages(service: ChatFairUseService, count: number, userId = USER_ID): Promise<void> {
    for (let i = 0; i < count; i += 1) {
      await service.consume(userId);
      now += 1000;
    }
  }

  describe('on a free account', () => {
    it('should admit the first 50 messages', async () => {
      await expect(sendMessages(freeService(), LIMIT)).resolves.toBeUndefined();
    });

    it('should reject the 51st message in 24 hours with CHAT_LIMIT_REACHED and 429', async () => {
      const service = freeService();
      await sendMessages(service, LIMIT);

      await expect(service.consume(USER_ID)).rejects.toMatchObject({
        code: 'CHAT_LIMIT_REACHED',
        status: 429,
      });
    });

    it('should reject with a ChatLimitReachedError', async () => {
      const service = freeService();
      await sendMessages(service, LIMIT);

      await expect(service.consume(USER_ID)).rejects.toBeInstanceOf(ChatLimitReachedError);
    });

    it('should set nextAt to 24 hours after the oldest counted message', async () => {
      const service = freeService();
      await sendMessages(service, LIMIT);

      const error = await service.consume(USER_ID).catch((err: unknown) => err as ChatLimitReachedError);

      expect(error.nextAt).toBe(new Date(START + DAY_MS).toISOString());
    });

    it('should not move nextAt when a rejected message is retried', async () => {
      const service = freeService();
      await sendMessages(service, LIMIT);
      const first = await service.consume(USER_ID).catch((err: unknown) => err as ChatLimitReachedError);
      now += 3 * 60 * MINUTE;

      const retry = await service.consume(USER_ID).catch((err: unknown) => err as ChatLimitReachedError);

      expect(retry.nextAt).toBe(first.nextAt);
    });

    it('should keep refusing until the oldest message is 24 hours old', async () => {
      const service = freeService();
      await sendMessages(service, LIMIT);
      now = START + DAY_MS - 1;

      await expect(service.consume(USER_ID)).rejects.toBeInstanceOf(ChatLimitReachedError);
    });

    it('should admit a message again once the oldest one leaves the 24 hour window', async () => {
      const service = freeService();
      await sendMessages(service, LIMIT);
      now = START + DAY_MS + 1;

      await expect(service.consume(USER_ID)).resolves.toBeUndefined();
    });

    it('should free one slot at a time as the window rolls, not all at once', async () => {
      const service = freeService();
      await sendMessages(service, LIMIT);
      now = START + DAY_MS + 1;
      await service.consume(USER_ID);

      await expect(service.consume(USER_ID)).rejects.toBeInstanceOf(ChatLimitReachedError);
    });

    it('should count each user in their own window', async () => {
      const service = freeService();
      await sendMessages(service, LIMIT);

      await expect(service.consume('6a7b2f6abacdb32871507997')).resolves.toBeUndefined();
    });

    it('should keep the window under vie:api:chat-fair-use:<userId>', async () => {
      const redis = rollingWindowRedis();
      await freeService(redis).consume(USER_ID);

      expect(redis.eval.mock.calls[0][2]).toBe(`vie:api:chat-fair-use:${USER_ID}`);
      expect(chatFairUseKey(USER_ID)).toBe(`vie:api:chat-fair-use:${USER_ID}`);
    });

    it('should name the configured limit in the message', async () => {
      const service = freeService(rollingWindowRedis(), 3);
      await sendMessages(service, 3);

      await expect(service.consume(USER_ID)).rejects.toThrow('Free accounts can send 3 chat messages in 24 hours.');
    });
  });

  describe('on a paid plan', () => {
    it('should never limit a Pro account', async () => {
      const redis = rollingWindowRedis();
      const service = new ChatFairUseService(redis as unknown as Redis, plans('pro'), LIMIT, logger, clock);

      await expect(sendMessages(service, LIMIT * 4)).resolves.toBeUndefined();
    });

    it('should not touch Redis for a Pro account', async () => {
      const redis = rollingWindowRedis();
      const service = new ChatFairUseService(redis as unknown as Redis, plans('pro'), LIMIT, logger, clock);

      await service.consume(USER_ID);

      expect(redis.eval).not.toHaveBeenCalled();
    });
  });

  describe('when something fails', () => {
    it('should let the message through when Redis rejects', async () => {
      const redis = { eval: vi.fn().mockRejectedValue(new Error('Connection is closed.')) };
      const service = new ChatFairUseService(redis as unknown as Redis, plans('free'), 1, logger, clock);

      await expect(sendMessages(service, 5)).resolves.toBeUndefined();
    });

    it('should warn when Redis rejects', async () => {
      const redis = { eval: vi.fn().mockRejectedValue(new Error('Connection is closed.')) };
      const service = new ChatFairUseService(redis as unknown as Redis, plans('free'), 1, logger, clock);

      await service.consume(USER_ID);

      expect(logger.warn).toHaveBeenCalledWith(
        expect.objectContaining({ userId: USER_ID }),
        'chat_fair_use_check_failed',
      );
    });

    it('should let the message through when Redis replies with something unexpected', async () => {
      const redis = { eval: vi.fn().mockResolvedValue('OK') };
      const service = new ChatFairUseService(redis as unknown as Redis, plans('free'), 1, logger, clock);

      await expect(sendMessages(service, 3)).resolves.toBeUndefined();
      expect(logger.warn).toHaveBeenCalledWith({ userId: USER_ID }, 'chat_fair_use_unexpected_reply');
    });

    it('should let the message through when the plan cannot be read', async () => {
      const redis = rollingWindowRedis();
      const broken = { summary: vi.fn().mockRejectedValue(new Error('mongo down')) };
      const service = new ChatFairUseService(redis as unknown as Redis, broken, 1, logger, clock);

      await expect(sendMessages(service, 3)).resolves.toBeUndefined();
      expect(redis.eval).not.toHaveBeenCalled();
    });

    it('should warn when the plan cannot be read', async () => {
      const broken = { summary: vi.fn().mockRejectedValue(new Error('mongo down')) };
      const service = new ChatFairUseService(rollingWindowRedis() as unknown as Redis, broken, 1, logger, clock);

      await service.consume(USER_ID);

      expect(logger.warn).toHaveBeenCalledWith(
        expect.objectContaining({ userId: USER_ID }),
        'chat_fair_use_plan_lookup_failed',
      );
    });
  });

  describe('when the limit is 0', () => {
    it('should read neither the plan nor Redis', async () => {
      const redis = rollingWindowRedis();
      const lookup = plans('free');
      const service = new ChatFairUseService(redis as unknown as Redis, lookup, 0, logger, clock);

      await sendMessages(service, 100);

      expect(lookup.summary).not.toHaveBeenCalled();
      expect(redis.eval).not.toHaveBeenCalled();
    });
  });
});

describe('noOpChatFairUse', () => {
  it('should always admit the message', async () => {
    await expect(noOpChatFairUse.consume(USER_ID)).resolves.toBeUndefined();
  });
});
