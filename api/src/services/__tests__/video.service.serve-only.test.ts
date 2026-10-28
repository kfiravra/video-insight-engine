/**
 * P3.1 / C1: `createVideo({ serveOnly })` — the ledger refused to pay for a
 * run, so createVideo may only serve what costs nothing (Q6) and must throw
 * RunNotAllowedError, before any write, wherever it would dispatch. Real
 * VideoRepository on the test replica set; nothing may reach the pipeline.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { ObjectId } from 'mongodb';
import type { FastifyBaseLogger } from 'fastify';
import { getTestDb, mockLogger } from '../../test/setup.js';
import { config } from '../../config.js';
import { VideoRepository } from '../../repositories/video.repository.js';
import { RunNotAllowedError } from '../../utils/errors.js';
import { computeContentKey } from '../idempotency.service.js';
import { VideoService } from '../video.service.js';
import { noOpDispatchGuard } from '../dispatch-guard.service.js';
import type { IdempotencyService } from '../idempotency.service.js';
import type { QueuePublisher } from '../queue-publisher.service.js';
import type { SummarizerClient } from '../summarizer-client.js';

const YOUTUBE_ID = 'dQw4w9WgXcQ';
const URL = `https://www.youtube.com/watch?v=${YOUTUBE_ID}`;
const DEDUP_KEY = computeContentKey({ youtubeId: YOUTUBE_ID, providers: undefined, version: 1 });

describe('VideoService.createVideo — serveOnly', () => {
  let service: VideoService;
  let triggerSummarization: ReturnType<typeof vi.fn>;
  let publishVideoJob: ReturnType<typeof vi.fn>;
  const userId = new ObjectId().toHexString();

  const cache = () => getTestDb().collection('videoSummaryCache');
  const seedRow = (fields: Record<string, unknown>) =>
    cache().insertOne({
      youtubeId: YOUTUBE_ID, url: URL, version: 1, isLatest: true, retryCount: 0, dedupKey: DEDUP_KEY,
      createdAt: new Date(), updatedAt: new Date(), ...fields,
    });
  const serveOnly = (extra: Record<string, unknown> = {}) =>
    service.createVideo(userId, URL, { tier: 'free', serveOnly: true, ...extra });
  const userVideos = () => getTestDb().collection('userVideos').countDocuments();

  beforeEach(() => {
    triggerSummarization = vi.fn();
    publishVideoJob = vi.fn();
    service = new VideoService(
      new VideoRepository(getTestDb()),
      { triggerSummarization } as unknown as SummarizerClient,
      { publishVideoJob } as unknown as QueuePublisher,
      { computeContentKey } as unknown as IdempotencyService,
      noOpDispatchGuard,
      { isEvalUser: vi.fn().mockResolvedValue(false), mayRunCold: vi.fn().mockResolvedValue(false) },
      mockLogger as unknown as FastifyBaseLogger,
    );
  });

  it('should serve a completed, current summary as cached', async () => {
    await seedRow({ status: 'completed', pipelineVersion: config.PIPELINE_VERSION });

    const result = await serveOnly();

    expect(result.cached).toBe(true);
    expect(await userVideos()).toBe(1);
  });

  it('should attach to a live in-flight run', async () => {
    await seedRow({ status: 'processing' });

    const result = await serveOnly();

    expect(result).toMatchObject({ cached: false, attached: true });
  });

  it('should serve the user\'s own copy', async () => {
    const { insertedId } = await seedRow({ status: 'completed', pipelineVersion: config.PIPELINE_VERSION });
    await getTestDb().collection('userVideos').insertOne({
      userId: new ObjectId(userId), videoSummaryId: insertedId, youtubeId: YOUTUBE_ID, folderId: null, status: 'completed',
    });

    expect(await serveOnly()).toMatchObject({ cached: true, alreadyExists: true });
  });

  it.each([
    ['nobody has the video', null],
    ['the run failed', { status: 'failed' }],
    ['the in-flight run stalled', { status: 'pending', updatedAt: new Date(Date.now() - 31 * 60 * 1000) }],
    ['the summary is from an older pipeline', { status: 'completed', pipelineVersion: 'v0-old' }],
  ])('should refuse before any write when %s', async (_label, row) => {
    if (row) await seedRow(row);
    const rowsBefore = await cache().countDocuments();

    await expect(serveOnly()).rejects.toBeInstanceOf(RunNotAllowedError);

    expect([await cache().countDocuments(), await userVideos()]).toEqual([rowsBefore, 0]);
    expect([triggerSummarization, publishVideoJob].every((fn) => fn.mock.calls.length === 0)).toBe(true);
  });

  it('should refuse a bypassCache re-run', async () => {
    await seedRow({ status: 'completed', pipelineVersion: config.PIPELINE_VERSION });

    await expect(serveOnly({ bypassCache: true })).rejects.toBeInstanceOf(RunNotAllowedError);
  });
});
