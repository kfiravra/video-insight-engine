import { describe, it, expect, beforeAll, afterAll, beforeEach, vi } from 'vitest';
import type { FastifyInstance } from 'fastify';
import type { FastifyBaseLogger } from 'fastify';
import type { Redis } from 'ioredis';
import { buildTestApp, createMockContainer, type MockContainer } from '../test/helpers.js';
import { SubmitBurstLimiter } from '../services/billing/burst-limit.js';
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
    const limiter = new SubmitBurstLimiter(counterRedis(), LIMIT, silentLogger);
    mockContainer.submitBurstLimiter.consume.mockImplementation((userId: string) =>
      limiter.consume(userId),
    );
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
});
