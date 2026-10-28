import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import type { FastifyBaseLogger } from 'fastify';
import type { Redis } from 'ioredis';
import { SummarizerClient, videoMetaCacheKey, videoMetaMissKey, type VideoMetadata } from '../summarizer-client.js';
import { MetadataUnavailableError } from '../../utils/errors.js';
import { config } from '../../config.js';

const logger = {
  info: vi.fn(),
  warn: vi.fn(),
  error: vi.fn(),
  debug: vi.fn(),
} as unknown as FastifyBaseLogger;

const YOUTUBE_ID = 'dQw4w9WgXcQ';
const METADATA: VideoMetadata = {
  youtubeId: YOUTUBE_ID,
  title: 'Never Gonna Give You Up',
  channel: 'Rick Astley',
  duration: 212,
  thumbnailUrl: 'https://i.ytimg.com/vi/dQw4w9WgXcQ/hq.jpg',
};

function fakeResponse(status: number, body: unknown): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as unknown as Response;
}

function aborted(): Error {
  return Object.assign(new Error('This operation was aborted'), { name: 'AbortError' });
}

interface RedisStub {
  get: ReturnType<typeof vi.fn>;
  set: ReturnType<typeof vi.fn>;
}

describe('SummarizerClient.getVideoMetadata', () => {
  const fetchMock = vi.fn();
  let redis: RedisStub;
  let client: SummarizerClient;

  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal('fetch', fetchMock);
    vi.mocked(logger.warn).mockClear();
    redis = { get: vi.fn().mockResolvedValue(null), set: vi.fn().mockResolvedValue('OK') };
    client = new SummarizerClient(logger, redis as unknown as Redis);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  describe('cache', () => {
    it('should return the cached metadata without calling the summarizer when the key exists', async () => {
      redis.get.mockResolvedValue(JSON.stringify(METADATA));

      const result = await client.getVideoMetadata(YOUTUBE_ID);

      expect(result).toEqual(METADATA);
      expect(fetchMock).not.toHaveBeenCalled();
    });

    it('should read the vie:api:video-meta key of the video', async () => {
      redis.get.mockResolvedValue(JSON.stringify(METADATA));

      await client.getVideoMetadata(YOUTUBE_ID);

      expect(redis.get).toHaveBeenCalledWith(`vie:api:video-meta:${YOUTUBE_ID}`);
      expect(videoMetaCacheKey(YOUTUBE_ID)).toBe(`vie:api:video-meta:${YOUTUBE_ID}`);
    });

    it('should fetch and cache the metadata for 24 hours when the key is missing', async () => {
      fetchMock.mockResolvedValue(fakeResponse(200, METADATA));

      const result = await client.getVideoMetadata(YOUTUBE_ID);

      expect(result).toEqual(METADATA);
      expect(redis.set).toHaveBeenCalledWith(
        `vie:api:video-meta:${YOUTUBE_ID}`,
        JSON.stringify(METADATA),
        'EX',
        86400,
      );
    });

    it('should fetch when Redis fails on read', async () => {
      redis.get.mockRejectedValue(new Error('READONLY You can\'t write against a read only replica'));
      fetchMock.mockResolvedValue(fakeResponse(200, METADATA));

      const result = await client.getVideoMetadata(YOUTUBE_ID);

      expect(result).toEqual(METADATA);
    });

    it('should still return the metadata when Redis fails on write', async () => {
      fetchMock.mockResolvedValue(fakeResponse(200, METADATA));
      redis.set.mockRejectedValue(new Error('Connection is closed.'));

      const result = await client.getVideoMetadata(YOUTUBE_ID);

      expect(result).toEqual(METADATA);
    });

    it.each([
      ['not JSON', '{oops'],
      ['the wrong shape', JSON.stringify({ title: 'only a title' })],
    ])('should fetch when the cached entry is %s', async (_label, raw) => {
      redis.get.mockImplementation(async (key: string) => (key === videoMetaCacheKey(YOUTUBE_ID) ? raw : null));
      fetchMock.mockResolvedValue(fakeResponse(200, METADATA));

      const result = await client.getVideoMetadata(YOUTUBE_ID);

      expect(result).toEqual(METADATA);
    });

    it('should fetch every time when no Redis client is configured', async () => {
      fetchMock.mockResolvedValue(fakeResponse(200, METADATA));

      await new SummarizerClient(logger).getVideoMetadata(YOUTUBE_ID);
      await new SummarizerClient(logger).getVideoMetadata(YOUTUBE_ID);

      expect(fetchMock).toHaveBeenCalledTimes(2);
    });
  });

  describe('negative cache for an unknown video', () => {
    it('should remember a 404 for 10 minutes under its own key', async () => {
      fetchMock.mockResolvedValue(fakeResponse(404, { detail: 'gone' }));

      await client.getVideoMetadata(YOUTUBE_ID).catch(() => undefined);

      expect(redis.set).toHaveBeenCalledWith(`vie:api:video-meta-miss:${YOUTUBE_ID}`, '1', 'EX', 600);
      expect(videoMetaMissKey(YOUTUBE_ID)).toBe(`vie:api:video-meta-miss:${YOUTUBE_ID}`);
    });

    it('should not write the metadata key for a 404', async () => {
      fetchMock.mockResolvedValue(fakeResponse(404, { detail: 'gone' }));

      await client.getVideoMetadata(YOUTUBE_ID).catch(() => undefined);

      expect(redis.set).not.toHaveBeenCalledWith(videoMetaCacheKey(YOUTUBE_ID), expect.anything(), 'EX', expect.anything());
    });

    it('should answer not_found without asking the summarizer while the marker is there', async () => {
      redis.get.mockImplementation(async (key: string) => (key === videoMetaMissKey(YOUTUBE_ID) ? '1' : null));

      await expect(client.getVideoMetadata(YOUTUBE_ID)).rejects.toMatchObject({ code: 'METADATA_UNAVAILABLE', status: 404, reason: 'not_found' });
      expect(fetchMock).not.toHaveBeenCalled();
    });

    it('should ask the summarizer again once the marker has expired', async () => {
      fetchMock.mockResolvedValue(fakeResponse(200, METADATA));

      const result = await client.getVideoMetadata(YOUTUBE_ID);

      expect(result).toEqual(METADATA);
      expect(fetchMock).toHaveBeenCalledTimes(1);
    });

    it('should prefer cached metadata over a marker', async () => {
      redis.get.mockImplementation(async (key: string) => (key === videoMetaCacheKey(YOUTUBE_ID) ? JSON.stringify(METADATA) : '1'));

      await expect(client.getVideoMetadata(YOUTUBE_ID)).resolves.toEqual(METADATA);
    });

    it('should ask the summarizer when Redis fails reading the marker', async () => {
      redis.get.mockImplementation(async (key: string) => {
        if (key === videoMetaMissKey(YOUTUBE_ID)) throw new Error('Connection is closed.');
        return null;
      });
      fetchMock.mockResolvedValue(fakeResponse(200, METADATA));

      await expect(client.getVideoMetadata(YOUTUBE_ID)).resolves.toEqual(METADATA);
    });

    it('should still answer not_found when Redis fails writing the marker', async () => {
      fetchMock.mockResolvedValue(fakeResponse(404, { detail: 'gone' }));
      redis.set.mockRejectedValue(new Error('Connection is closed.'));

      await expect(client.getVideoMetadata(YOUTUBE_ID)).rejects.toMatchObject({ reason: 'not_found' });
    });

    it('should not read or write a marker when no Redis client is configured', async () => {
      fetchMock.mockResolvedValue(fakeResponse(404, { detail: 'gone' }));

      await expect(new SummarizerClient(logger).getVideoMetadata(YOUTUBE_ID)).rejects.toMatchObject({ reason: 'not_found' });
      expect(redis.get).not.toHaveBeenCalled();
    });
  });

  describe('request', () => {
    it('should GET /video/metadata with the id and the internal secret', async () => {
      fetchMock.mockResolvedValue(fakeResponse(200, METADATA));

      await client.getVideoMetadata(YOUTUBE_ID);

      const [url, init] = fetchMock.mock.calls[0];
      expect(url).toBe(`${config.SUMMARIZER_URL}/video/metadata?youtubeId=${YOUTUBE_ID}`);
      expect(init.method).toBe('GET');
      expect(init.headers['X-Internal-Secret']).toBe(config.INTERNAL_SECRET);
    });

    it('should return a null thumbnail when the summarizer sends none', async () => {
      fetchMock.mockResolvedValue(fakeResponse(200, { ...METADATA, thumbnailUrl: null }));

      const result = await client.getVideoMetadata(YOUTUBE_ID);

      expect(result.thumbnailUrl).toBeNull();
    });
  });

  describe('failures', () => {
    it('should throw METADATA_UNAVAILABLE with reason not_found when the summarizer answers 404', async () => {
      fetchMock.mockResolvedValue(fakeResponse(404, { detail: 'Video not found or unavailable' }));

      await expect(client.getVideoMetadata(YOUTUBE_ID)).rejects.toMatchObject({
        code: 'METADATA_UNAVAILABLE',
        status: 404,
        reason: 'not_found',
      });
    });

    it('should throw METADATA_UNAVAILABLE with reason unsupported and status 422 for a live stream or premiere', async () => {
      fetchMock.mockResolvedValue(fakeResponse(422, { detail: 'Live streams are not supported' }));

      await expect(client.getVideoMetadata(YOUTUBE_ID)).rejects.toMatchObject({
        code: 'METADATA_UNAVAILABLE',
        status: 422,
        reason: 'unsupported',
      });
    });

    it('should say live streams and premieres in the unsupported message', async () => {
      fetchMock.mockResolvedValue(fakeResponse(422, { detail: 'Video has no duration yet' }));

      await expect(client.getVideoMetadata(YOUTUBE_ID)).rejects.toThrow(/live streams and premieres/i);
    });

    it.each([500, 502, 503])('should throw METADATA_UNAVAILABLE with reason upstream on a %i', async (status) => {
      fetchMock.mockResolvedValue(fakeResponse(status, { detail: 'down' }));

      await expect(client.getVideoMetadata(YOUTUBE_ID)).rejects.toMatchObject({
        code: 'METADATA_UNAVAILABLE',
        status: 503,
        reason: 'upstream',
      });
    });

    it('should throw METADATA_UNAVAILABLE when the summarizer is unreachable', async () => {
      fetchMock.mockRejectedValue(new Error('connect ECONNREFUSED 127.0.0.1:8000'));

      await expect(client.getVideoMetadata(YOUTUBE_ID)).rejects.toBeInstanceOf(MetadataUnavailableError);
    });

    it('should throw METADATA_UNAVAILABLE when a 200 body is not JSON', async () => {
      fetchMock.mockResolvedValue({
        ok: true, status: 200, json: async () => { throw new SyntaxError('Unexpected token <'); },
      } as unknown as Response);

      await expect(client.getVideoMetadata(YOUTUBE_ID)).rejects.toBeInstanceOf(MetadataUnavailableError);
    });

    it('should throw METADATA_UNAVAILABLE when the duration is not positive', async () => {
      fetchMock.mockResolvedValue(fakeResponse(200, { ...METADATA, duration: 0 }));

      await expect(client.getVideoMetadata(YOUTUBE_ID)).rejects.toBeInstanceOf(MetadataUnavailableError);
    });

    it.each([
      ['an upstream error', () => fetchMock.mockResolvedValue(fakeResponse(503, { detail: 'down' }))],
      ['an unsupported video', () => fetchMock.mockResolvedValue(fakeResponse(422, { detail: 'live' }))],
      ['an unreachable summarizer', () => fetchMock.mockRejectedValue(new Error('ECONNREFUSED'))],
    ])('should remember nothing after %s', async (_label, arrange) => {
      arrange();

      await client.getVideoMetadata(YOUTUBE_ID).catch(() => undefined);

      expect(redis.set).not.toHaveBeenCalled();
    });

    it('should give up with reason timeout after 25 seconds, so a slow rotation that still answers is not thrown away', async () => {
      vi.useFakeTimers();
      fetchMock.mockImplementation(
        (_url: string, init: RequestInit) =>
          new Promise((_resolve, reject) => {
            init.signal?.addEventListener('abort', () => reject(aborted()));
          }),
      );
      let settled = false;
      const outcome = client.getVideoMetadata(YOUTUBE_ID).then(
        () => 'resolved',
        (err: unknown) => {
          settled = true;
          return err;
        },
      );

      await vi.advanceTimersByTimeAsync(24999);
      const settledBeforeDeadline = settled;
      await vi.advanceTimersByTimeAsync(1);

      expect(settledBeforeDeadline).toBe(false);
      expect(await outcome).toMatchObject({ code: 'METADATA_UNAVAILABLE', status: 503, reason: 'timeout' });
    });
  });
});
