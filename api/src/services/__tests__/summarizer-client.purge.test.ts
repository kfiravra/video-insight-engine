import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import type { FastifyBaseLogger } from 'fastify';
import { SummarizerClient } from '../summarizer-client.js';
import { SummarizerPurgeError } from '../../utils/errors.js';
import { config } from '../../config.js';

const logger = {
  info: vi.fn(),
  warn: vi.fn(),
  error: vi.fn(),
  debug: vi.fn(),
} as unknown as FastifyBaseLogger;

function fakeResponse(status: number, body: unknown): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as unknown as Response;
}

describe('SummarizerClient.purgeVideo', () => {
  const fetchMock = vi.fn();
  const request = { youtubeId: 'dQw4w9WgXcQ', videoSummaryIds: ['6a7b2f6abacdb32871507996'] };

  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('should post the summary ids with the internal secret and return the counts', async () => {
    fetchMock.mockResolvedValue(
      fakeResponse(200, { qdrantPoints: 3, s3Objects: 9, redisKeys: 2, warnings: [] }),
    );

    const result = await new SummarizerClient(logger).purgeVideo(request);

    expect(result).toEqual({ qdrantPoints: 3, s3Objects: 9, redisKeys: 2, warnings: [] });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`${config.SUMMARIZER_URL}/internal/videos/dQw4w9WgXcQ/purge`);
    expect(init.method).toBe('POST');
    expect(init.headers['X-Internal-Secret']).toBe(config.INTERNAL_SECRET);
    expect(JSON.parse(init.body)).toEqual({ videoSummaryIds: request.videoSummaryIds });
  });

  it('should throw SummarizerPurgeError when the summarizer answers non-2xx', async () => {
    fetchMock.mockResolvedValue(fakeResponse(503, { detail: 'down' }));

    await expect(new SummarizerClient(logger).purgeVideo(request)).rejects.toBeInstanceOf(
      SummarizerPurgeError,
    );
  });

  it('should throw SummarizerPurgeError when the summarizer is unreachable', async () => {
    fetchMock.mockRejectedValue(new Error('ECONNREFUSED'));

    await expect(new SummarizerClient(logger).purgeVideo(request)).rejects.toMatchObject({
      code: 'SUMMARIZER_PURGE_FAILED',
      status: 502,
    });
  });

  it('should throw SummarizerPurgeError when a 2xx body is not JSON', async () => {
    fetchMock.mockResolvedValue({
      ok: true, status: 200, json: async () => { throw new SyntaxError('Unexpected token <'); },
    } as unknown as Response);

    await expect(new SummarizerClient(logger).purgeVideo(request)).rejects.toBeInstanceOf(
      SummarizerPurgeError,
    );
  });

  it('should throw SummarizerPurgeError when the payload has the wrong shape', async () => {
    fetchMock.mockResolvedValue(fakeResponse(200, { deleted: true }));

    await expect(new SummarizerClient(logger).purgeVideo(request)).rejects.toBeInstanceOf(
      SummarizerPurgeError,
    );
  });
});
