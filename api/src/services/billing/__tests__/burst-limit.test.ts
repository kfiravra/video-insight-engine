import { describe, it, expect, beforeEach, vi } from 'vitest';
import type { FastifyBaseLogger } from 'fastify';
import type { Redis } from 'ioredis';
import {
  SubmitBurstLimiter,
  noOpSubmitBurstLimiter,
  submitBurstKey,
} from '../burst-limit.js';
import { RateLimitedError } from '../../../utils/errors.js';

const USER_ID = '6a7b2f6abacdb32871507996';
const LIMIT = 20;

function makeLogger(): FastifyBaseLogger {
  return { info: vi.fn(), warn: vi.fn(), error: vi.fn(), debug: vi.fn() } as unknown as FastifyBaseLogger;
}

/** Stands in for the Lua script: a counter per key and a fixed seconds-left. */
function counterRedis(secondsLeft: number): { eval: ReturnType<typeof vi.fn> } {
  const counts = new Map<string, number>();
  return {
    eval: vi.fn(async (_script: string, _keys: number, key: string) => {
      const count = (counts.get(key) ?? 0) + 1;
      counts.set(key, count);
      return [count, secondsLeft];
    }),
  };
}

async function consumeTimes(limiter: SubmitBurstLimiter, times: number): Promise<void> {
  for (let i = 0; i < times; i += 1) {
    await limiter.consume(USER_ID);
  }
}

describe('SubmitBurstLimiter', () => {
  let logger: FastifyBaseLogger;

  beforeEach(() => {
    logger = makeLogger();
  });

  describe('consume', () => {
    it('should let the first 20 submissions through', async () => {
      const limiter = new SubmitBurstLimiter(counterRedis(3600) as unknown as Redis, LIMIT, logger);

      await expect(consumeTimes(limiter, LIMIT)).resolves.toBeUndefined();
    });

    it('should reject the 21st submission with RATE_LIMITED and 429', async () => {
      const limiter = new SubmitBurstLimiter(counterRedis(2700) as unknown as Redis, LIMIT, logger);
      await consumeTimes(limiter, LIMIT);

      await expect(limiter.consume(USER_ID)).rejects.toMatchObject({
        code: 'RATE_LIMITED',
        status: 429,
      });
    });

    it('should say how many minutes are left in the message', async () => {
      const limiter = new SubmitBurstLimiter(counterRedis(2700) as unknown as Redis, LIMIT, logger);
      await consumeTimes(limiter, LIMIT);

      await expect(limiter.consume(USER_ID)).rejects.toThrow(
        'Too many submissions in the last hour. Try again in 45 min.',
      );
    });

    it.each([
      [3600, 60],
      [61, 2],
      [60, 1],
      [1, 1],
      [0, 1],
    ])('should round %i seconds left up to %i minutes', async (secondsLeft, minutes) => {
      const limiter = new SubmitBurstLimiter(counterRedis(secondsLeft) as unknown as Redis, 1, logger);
      await limiter.consume(USER_ID);

      await expect(limiter.consume(USER_ID)).rejects.toThrow(`Try again in ${minutes} min.`);
    });

    it('should never mention a daily limit', async () => {
      const limiter = new SubmitBurstLimiter(counterRedis(1800) as unknown as Redis, 1, logger);
      await limiter.consume(USER_ID);

      const error = await limiter.consume(USER_ID).catch((err: unknown) => err);

      expect(error).toBeInstanceOf(RateLimitedError);
      expect((error as Error).message).not.toMatch(/daily|day|24/i);
    });

    it('should count each user in their own bucket', async () => {
      const limiter = new SubmitBurstLimiter(counterRedis(3600) as unknown as Redis, 1, logger);
      await limiter.consume(USER_ID);

      await expect(limiter.consume('6a7b2f6abacdb32871507997')).resolves.toBeUndefined();
    });

    it('should use the vie:api:submit-burst key with a one-hour window', async () => {
      const redis = counterRedis(3600);
      const limiter = new SubmitBurstLimiter(redis as unknown as Redis, LIMIT, logger);

      await limiter.consume(USER_ID);

      expect(redis.eval).toHaveBeenCalledWith(
        expect.any(String),
        1,
        `vie:api:submit-burst:${USER_ID}`,
        3600,
      );
      expect(submitBurstKey(USER_ID)).toBe(`vie:api:submit-burst:${USER_ID}`);
    });
  });

  describe('when Redis fails', () => {
    it('should let the submission through when the command rejects', async () => {
      const redis = { eval: vi.fn().mockRejectedValue(new Error('Connection is closed.')) };
      const limiter = new SubmitBurstLimiter(redis as unknown as Redis, 1, logger);

      await expect(consumeTimes(limiter, 3)).resolves.toBeUndefined();
    });

    it('should log a warning when the command rejects', async () => {
      const redis = { eval: vi.fn().mockRejectedValue(new Error('Connection is closed.')) };
      const limiter = new SubmitBurstLimiter(redis as unknown as Redis, 1, logger);

      await limiter.consume(USER_ID);

      expect(logger.warn).toHaveBeenCalledWith(
        expect.objectContaining({ userId: USER_ID }),
        'submit_burst_check_failed',
      );
    });

    it('should let the submission through when Redis replies with something unexpected', async () => {
      const redis = { eval: vi.fn().mockResolvedValue('OK') };
      const limiter = new SubmitBurstLimiter(redis as unknown as Redis, 1, logger);

      await expect(consumeTimes(limiter, 3)).resolves.toBeUndefined();
      expect(logger.warn).toHaveBeenCalledWith({ userId: USER_ID }, 'submit_burst_unexpected_reply');
    });
  });

  describe('when the limit is 0', () => {
    it('should not touch Redis', async () => {
      const redis = counterRedis(3600);
      const limiter = new SubmitBurstLimiter(redis as unknown as Redis, 0, logger);

      await consumeTimes(limiter, 50);

      expect(redis.eval).not.toHaveBeenCalled();
    });
  });
});

describe('noOpSubmitBurstLimiter', () => {
  it('should always let the submission through', async () => {
    await expect(noOpSubmitBurstLimiter.consume(USER_ID)).resolves.toBeUndefined();
  });
});
