import { describe, it, expect, beforeAll, afterAll, beforeEach, vi } from 'vitest';
import type { FastifyInstance } from 'fastify';
import type { FastifyBaseLogger } from 'fastify';
import type { Redis } from 'ioredis';
import { buildTestApp, createMockContainer, type MockContainer } from '../test/helpers.js';
import { ESTIMATE_LIMIT_PER_HOUR, SubmitBurstLimiter, estimateBurstKey } from '../services/billing/burst-limit.js';
import { config } from '../config.js';

const USER_ID = 'aaaaaaaaaaaaaaaaaaaaaaaa';
const OTHER_USER_ID = 'bbbbbbbbbbbbbbbbbbbbbbbb';
const YOUTUBE_URL = 'https://www.youtube.com/watch?v=dQw4w9WgXcQ';
const PLAYLIST_URL = 'https://www.youtube.com/playlist?list=PLtest123';
const LIMIT = 20;
const SECONDS_LEFT = 2700;

const silentLogger = {
  info: vi.fn(),
  warn: vi.fn(),
  error: vi.fn(),
  debug: vi.fn(),
} as unknown as FastifyBaseLogger;

/** Counter per key with a fixed time left, standing in for the Lua script. */
function counterRedis(): Redis {
  const counts = new Map<string, number>();
  const stub = {
    eval: async (_script: string, _keys: number, key: string): Promise<[number, number]> => {
      const count = (counts.get(key) ?? 0) + 1;
      counts.set(key, count);
      return [count, SECONDS_LEFT];
    },
  };
  return stub as unknown as Redis;
}

describe('submit burst limit on the submit routes', () => {
  let app: FastifyInstance;
  let mockContainer: MockContainer;
  let webHeaders: Record<string, string>;

  beforeAll(async () => {
    mockContainer = createMockContainer();
    app = await buildTestApp(mockContainer);
    await app.ready();
    const token = app.jwt.sign({ userId: USER_ID, email: 'burst@example.com', type: 'access' });
    webHeaders = { authorization: `Bearer ${token}`, 'content-type': 'application/json' };
  });

  afterAll(async () => {
    await app.close();
  });

  beforeEach(() => {
    vi.clearAllMocks();
    // One Redis behind both buckets, wired as container.ts does: only the keys keep them apart.
    const redis = counterRedis();
    const limiter = new SubmitBurstLimiter(redis, LIMIT, silentLogger);
    const estimates = new SubmitBurstLimiter(redis, ESTIMATE_LIMIT_PER_HOUR, silentLogger, { key: estimateBurstKey, what: 'estimates' });
    mockContainer.submitBurstLimiter.consume.mockImplementation((userId: string) =>
      limiter.consume(userId),
    );
    mockContainer.estimateBurstLimiter.consume.mockImplementation((userId: string) => estimates.consume(userId));
    mockContainer.videoService.createVideo.mockResolvedValue({
      video: { id: 'v1', youtubeId: 'dQw4w9WgXcQ', status: 'pending' },
      cached: false,
    });
    mockContainer.userRepository.findById.mockResolvedValue({});
    mockContainer.playlistService.import.mockResolvedValue({ playlistId: 'PLtest123', importedCount: 50 });
  });

  function submitFromWeb(): ReturnType<FastifyInstance['inject']> {
    return app.inject({
      method: 'POST',
      url: '/api/videos',
      headers: webHeaders,
      payload: { url: YOUTUBE_URL },
    });
  }

  function submitFromAssistant(userId = USER_ID): ReturnType<FastifyInstance['inject']> {
    return app.inject({
      method: 'POST',
      url: '/internal/assistant/generate',
      headers: {
        'content-type': 'application/json',
        'x-internal-secret': config.INTERNAL_SECRET,
        'x-user-id': userId,
      },
      payload: { url: YOUTUBE_URL },
    });
  }

  async function fillBucketAcrossBothRoutes(): Promise<void> {
    for (let i = 0; i < LIMIT; i += 1) {
      const response = i % 2 === 0 ? await submitFromWeb() : await submitFromAssistant();
      expect(response.statusCode).toBeLessThan(300);
    }
  }

  describe('one bucket for POST /api/videos and the assistant generate route', () => {
    it('should answer 429 to the web app once the two routes together made 20 submissions', async () => {
      await fillBucketAcrossBothRoutes();

      const response = await submitFromWeb();

      expect(response.statusCode).toBe(429);
    });

    it('should answer 429 to the assistant once the two routes together made 20 submissions', async () => {
      await fillBucketAcrossBothRoutes();

      const response = await submitFromAssistant();

      expect(response.statusCode).toBe(429);
    });

    it('should not start a video for the 21st submission', async () => {
      await fillBucketAcrossBothRoutes();
      mockContainer.videoService.createVideo.mockClear();

      await submitFromWeb();

      expect(mockContainer.videoService.createVideo).not.toHaveBeenCalled();
    });

    it('should leave another user with a full bucket of their own', async () => {
      await fillBucketAcrossBothRoutes();

      const response = await submitFromAssistant(OTHER_USER_ID);

      expect(response.statusCode).toBe(200);
    });
  });

  describe('the 429 answer', () => {
    it('should use the RATE_LIMITED envelope with the minutes left', async () => {
      await fillBucketAcrossBothRoutes();

      const response = await submitFromWeb();

      expect(response.json()).toEqual({
        error: 'RATE_LIMITED',
        message: 'Too many submissions in the last hour. Try again in 45 min.',
        statusCode: 429,
        retryAfterSec: 2700,
      });
    });

    it('should never mention a daily limit', async () => {
      await fillBucketAcrossBothRoutes();

      const response = await submitFromAssistant();

      expect(response.json().message).not.toMatch(/daily/i);
    });
  });

  describe('POST /api/playlists/import', () => {
    it('should count a 50-video playlist as one submission', async () => {
      await app.inject({
        method: 'POST',
        url: '/api/playlists/import',
        headers: webHeaders,
        payload: { url: PLAYLIST_URL },
      });

      expect(mockContainer.submitBurstLimiter.consume).toHaveBeenCalledTimes(1);
    });

    it('should share the bucket with video submissions', async () => {
      for (let i = 0; i < LIMIT - 1; i += 1) {
        await submitFromWeb();
      }
      const lastFree = await app.inject({
        method: 'POST',
        url: '/api/playlists/import',
        headers: webHeaders,
        payload: { url: PLAYLIST_URL },
      });

      const overLimit = await submitFromWeb();

      expect(lastFree.statusCode).toBe(201);
      expect(overLimit.statusCode).toBe(429);
    });
  });
  describe('GET /api/videos/estimate — its own bucket (A2)', () => {
    const estimate = () => app.inject({
      method: 'GET',
      url: `/api/videos/estimate?url=${encodeURIComponent(YOUTUBE_URL)}`,
      headers: { authorization: webHeaders.authorization },
    });
    const estimateTimes = async (times: number): Promise<void> => {
      for (let i = 0; i < times; i += 1) {
        expect((await estimate()).statusCode).toBe(200);
      }
    };

    it('should answer 429 to the 61st estimate in an hour, naming what was counted', async () => {
      await estimateTimes(60);

      const response = await estimate();

      expect(response.json()).toEqual({
        error: 'RATE_LIMITED',
        message: 'Too many estimates in the last hour. Try again in 45 min.',
        statusCode: 429,
        retryAfterSec: 2700,
      });
    });

    it('should not consume the submit bucket: a submit still goes through after 60 estimates', async () => {
      await estimateTimes(60);

      expect((await submitFromWeb()).statusCode).toBe(201);
      expect(mockContainer.submitBurstLimiter.consume).toHaveBeenCalledTimes(1);
    });

    it('should still estimate once the submit bucket is full', async () => {
      await fillBucketAcrossBothRoutes();

      expect((await estimate()).statusCode).toBe(200);
    });
  });
});
