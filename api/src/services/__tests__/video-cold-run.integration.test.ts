/**
 * 1a.7 gate: `cold: true` is a benchmark-only flag. Only the admin role or the
 * eval account (`users.isEvalUser`) may set it; it implies bypassCache and is
 * stamped on the new row as `coldMedia`, which tells the summarizer to skip its
 * S3 transcript cache and scene-frame manifest for that run.
 */
import { describe, it, expect, beforeAll, afterAll, beforeEach, vi } from 'vitest';
import { MongoMemoryServer } from 'mongodb-memory-server';
import { MongoClient, ObjectId } from 'mongodb';
import type { Db } from 'mongodb';
import type { FastifyBaseLogger } from 'fastify';
import { VideoRepository } from '../../repositories/video.repository.js';
import { UserRepository } from '../../repositories/user.repository.js';
import { IdempotencyRepository } from '../../repositories/idempotency.repository.js';
import { IdempotencyService } from '../idempotency.service.js';
import { noOpDispatchGuard } from '../dispatch-guard.service.js';
import { VideoService } from '../video.service.js';
import { ColdRunForbiddenError } from '../../utils/errors.js';
import { config } from '../../config.js';
import type { SummarizerClient } from '../summarizer-client.js';
import type { QueuePublisher } from '../queue-publisher.service.js';

const silentLogger = {
  info: vi.fn(),
  warn: vi.fn(),
  error: vi.fn(),
  debug: vi.fn(),
} as unknown as FastifyBaseLogger;

const YOUTUBE_ID = 'dQw4w9WgXcQ';
const URL = `https://www.youtube.com/watch?v=${YOUTUBE_ID}`;

describe('VideoService cold runs (integration)', () => {
  let mongod: MongoMemoryServer;
  let client: MongoClient;
  let db: Db;
  let service: VideoService;
  let evalUser: string;
  let admin: string;
  let alice: string;
  const publishVideoJob = vi.fn();
  const originalUseQueue = config.USE_QUEUE_PIPELINE;

  const cache = () => db.collection('videoSummaryCache');
  const submitCold = (userId: string) => service.createVideo(userId, URL, { tier: 'free', cold: true });

  async function rowOf(videoSummaryId: string) {
    return cache().findOne({ _id: new ObjectId(videoSummaryId) });
  }

  /** Alice's first submission — creates and completes the shared row. */
  async function seedSharedRow(): Promise<void> {
    const { video } = await service.createVideo(alice, URL, { tier: 'free' });
    await cache().updateOne({ _id: new ObjectId(video.videoSummaryId) }, { $set: { status: 'completed' } });
  }

  beforeAll(async () => {
    mongod = await MongoMemoryServer.create();
    client = new MongoClient(mongod.getUri());
    await client.connect();
    db = client.db('video-cold-run');
    await cache().createIndex(
      { dedupKey: 1 },
      { unique: true, partialFilterExpression: { dedupKey: { $exists: true } } },
    );
    const users = new UserRepository(db);
    const create = async (name: string) =>
      (await users.create({ email: `${name}@test.local`, passwordHash: 'h', name }))._id;
    const evalId = await create('eval');
    await db.collection('users').updateOne({ _id: evalId }, { $set: { isEvalUser: true } });
    const adminId = await create('admin');
    await db.collection('users').updateOne({ _id: adminId }, { $set: { role: 'admin' } });
    evalUser = evalId.toString();
    admin = adminId.toString();
    alice = (await create('alice')).toString();

    config.USE_QUEUE_PIPELINE = true;
    service = new VideoService(
      new VideoRepository(db),
      { triggerSummarization: vi.fn() } as unknown as SummarizerClient,
      { publishVideoJob } as unknown as QueuePublisher,
      new IdempotencyService(new IdempotencyRepository(db), silentLogger),
      noOpDispatchGuard,
      users,
      silentLogger,
    );
  });

  afterAll(async () => {
    config.USE_QUEUE_PIPELINE = originalUseQueue;
    await client.close();
    await mongod.stop();
  });

  beforeEach(async () => {
    publishVideoJob.mockReset().mockResolvedValue({ requestId: 'r' });
    await cache().deleteMany({});
    await db.collection('userVideos').deleteMany({});
  });

  describe('permission', () => {
    it('should reject a cold run with 403 when the user is neither admin nor eval', async () => {
      await expect(submitCold(alice)).rejects.toBeInstanceOf(ColdRunForbiddenError);
    });

    it('should create no row and publish no job when a cold run is rejected', async () => {
      await submitCold(alice).catch(() => undefined);

      expect(await cache().countDocuments({})).toBe(0);
      expect(publishVideoJob).not.toHaveBeenCalled();
    });
  });

  describe('accepted cold runs', () => {
    it('should persist coldMedia on the eval version row when the eval user submits cold', async () => {
      await seedSharedRow();

      const { video } = await submitCold(evalUser);

      const row = await rowOf(video.videoSummaryId);
      expect(row).toMatchObject({ coldMedia: true, forceRefresh: true, evalRun: true });
    });

    it('should persist coldMedia on a new version row when an admin submits cold', async () => {
      await seedSharedRow();

      const { video } = await submitCold(admin);

      expect(await rowOf(video.videoSummaryId)).toMatchObject({ coldMedia: true, forceRefresh: true });
    });

    it('should persist coldMedia on the first row when an admin submits cold for a new video', async () => {
      const { video } = await submitCold(admin);

      expect(await rowOf(video.videoSummaryId)).toMatchObject({ coldMedia: true, version: 1 });
    });

    it('should publish the job with bypassCache when the run is cold', async () => {
      await submitCold(evalUser);

      expect(publishVideoJob).toHaveBeenCalledWith(expect.objectContaining({ bypassCache: true }));
    });
  });

  describe('warm runs', () => {
    it('should not stamp coldMedia when the eval user submits without cold', async () => {
      await seedSharedRow();

      const { video } = await service.createVideo(evalUser, URL, { tier: 'free', bypassCache: true });

      expect((await rowOf(video.videoSummaryId))?.coldMedia).toBeUndefined();
    });
  });
});
