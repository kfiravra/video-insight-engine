/**
 * B2: every dispatch stamps the dispatcher's `userId` / `requestId` on the
 * shared `videoSummaryCache` row before either branch runs. The summarizer
 * attributes `llm_usage` from that row whenever an SSE request drives the
 * pipeline — the HTTP fallback, or an SSE request that won the per-video lock
 * on a queued video before the worker — so the stamp must already be there
 * when the job is published or the summarizer is triggered, must belong to
 * THIS dispatch, and must match the GDPR cascade's string filter.
 */
import { describe, it, expect, beforeAll, afterAll, beforeEach, afterEach, vi } from 'vitest';
import { MongoMemoryServer } from 'mongodb-memory-server';
import { MongoClient, ObjectId } from 'mongodb';
import type { Db, Document } from 'mongodb';
import type { FastifyBaseLogger } from 'fastify';
import { VideoRepository } from '../../repositories/video.repository.js';
import { IdempotencyRepository } from '../../repositories/idempotency.repository.js';
import { IdempotencyService } from '../idempotency.service.js';
import { VideoService } from '../video.service.js';
import { QueuePublishError } from '../../utils/errors.js';
import { config } from '../../config.js';
import type { SummarizerClient, SummarizeRequest } from '../summarizer-client.js';
import type { QueuePublisher, PublishVideoJobInput } from '../queue-publisher.service.js';

const silentLogger = {
  info: vi.fn(),
  warn: vi.fn(),
  error: vi.fn(),
  debug: vi.fn(),
} as unknown as FastifyBaseLogger;

const YOUTUBE_ID = 'dQw4w9WgXcQ';
const URL = `https://www.youtube.com/watch?v=${YOUTUBE_ID}`;
const ALICE = new ObjectId().toHexString();
const BOB = new ObjectId().toHexString();

describe('VideoService dispatch context (integration)', () => {
  let mongod: MongoMemoryServer;
  let client: MongoClient;
  let db: Db;
  let service: VideoService;
  const originalUseQueue = config.USE_QUEUE_PIPELINE;
  const acquire = vi.fn();
  const publishVideoJob = vi.fn();
  const triggerSummarization = vi.fn();
  /** The row as the producer would read it at publish / trigger time. */
  let rowsAtDispatch: Promise<Document | null>[];

  const cache = () => db.collection('videoSummaryCache');
  const rowOf = (videoSummaryId: string) => cache().findOne({ _id: new ObjectId(videoSummaryId) });
  const submit = (userId: string, requestId?: string) =>
    service.createVideo(userId, URL, { queuePlan: 'free', requestId });

  /** Alice's dispatch, then the run fails — the next submitter retries the same row. */
  async function seedFailedRowDispatchedByAlice(): Promise<string> {
    const { video } = await submit(ALICE, 'req-alice-1');
    await cache().updateOne({ _id: new ObjectId(video.videoSummaryId) }, { $set: { status: 'failed' } });
    rowsAtDispatch = [];
    return video.videoSummaryId;
  }

  beforeAll(async () => {
    mongod = await MongoMemoryServer.create();
    client = new MongoClient(mongod.getUri());
    await client.connect();
    db = client.db('video-dispatch-context');
    await cache().createIndex(
      { dedupKey: 1 },
      { unique: true, partialFilterExpression: { dedupKey: { $exists: true } } },
    );
    service = new VideoService(
      new VideoRepository(db),
      { triggerSummarization } as unknown as SummarizerClient,
      { publishVideoJob } as unknown as QueuePublisher,
      new IdempotencyService(new IdempotencyRepository(db), silentLogger),
      { acquire, release: vi.fn().mockResolvedValue(undefined) },
      { isEvalUser: async () => false, mayRunCold: async () => false },
      silentLogger,
    );
  });

  afterAll(async () => {
    config.USE_QUEUE_PIPELINE = originalUseQueue;
    await client.close();
    await mongod.stop();
  });

  beforeEach(async () => {
    config.USE_QUEUE_PIPELINE = true;
    rowsAtDispatch = [];
    acquire.mockReset().mockResolvedValue({ acquired: true, token: 'guard-token' });
    publishVideoJob.mockReset().mockImplementation(async (job: PublishVideoJobInput) => {
      rowsAtDispatch.push(rowOf(job.videoSummaryId));
      return { requestId: job.requestId ?? 'generated' };
    });
    triggerSummarization.mockReset().mockImplementation((req: SummarizeRequest) => {
      rowsAtDispatch.push(rowOf(req.videoSummaryId));
    });
    await cache().deleteMany({});
    await db.collection('userVideos').deleteMany({});
  });

  afterEach(() => {
    config.USE_QUEUE_PIPELINE = originalUseQueue;
  });

  describe('queue path', () => {
    it('should stamp the dispatcher on the row before the job is published', async () => {
      await submit(ALICE, 'req-alice-1');

      const [row] = await Promise.all(rowsAtDispatch);
      expect(row).toMatchObject({ userId: ALICE, requestId: 'req-alice-1' });
    });

    it('should store the userId as the string the GDPR cascade matches on', async () => {
      await submit(ALICE, 'req-alice-1');

      expect(await cache().countDocuments({ userId: ALICE })).toBe(1);
    });
  });

  describe('HTTP fallback', () => {
    it('should stamp the dispatcher on the row before the summarizer is triggered', async () => {
      config.USE_QUEUE_PIPELINE = false;

      await submit(ALICE, 'req-alice-1');

      const [row] = await Promise.all(rowsAtDispatch);
      expect(row).toMatchObject({ userId: ALICE, requestId: 'req-alice-1' });
    });

    it('should keep the stamp when a failed queue publish falls back to HTTP', async () => {
      publishVideoJob.mockRejectedValue(new QueuePublishError('broker down'));

      await submit(ALICE, 'req-alice-1');

      const [row] = await Promise.all(rowsAtDispatch);
      expect(row).toMatchObject({ userId: ALICE, requestId: 'req-alice-1' });
    });
  });

  describe('re-dispatch of a shared row', () => {
    it("should replace the earlier dispatcher's ids when another user retries the row", async () => {
      const videoSummaryId = await seedFailedRowDispatchedByAlice();

      await submit(BOB, 'req-bob-1');

      expect(await rowOf(videoSummaryId)).toMatchObject({ userId: BOB, requestId: 'req-bob-1' });
    });

    it('should unset the earlier requestId when the new dispatch carries none', async () => {
      const videoSummaryId = await seedFailedRowDispatchedByAlice();

      await submit(BOB);

      expect(await rowOf(videoSummaryId)).not.toHaveProperty('requestId');
    });

    it('should leave the row untouched when the dispatch guard rejects the dispatch', async () => {
      const videoSummaryId = await seedFailedRowDispatchedByAlice();
      acquire.mockResolvedValue({ acquired: false, token: null });

      await submit(BOB, 'req-bob-1');

      expect(await rowOf(videoSummaryId)).toMatchObject({ userId: ALICE, requestId: 'req-alice-1' });
    });
  });
});
