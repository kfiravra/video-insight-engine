/**
 * D25 gate: results produced by the eval user (`users.isEvalUser`) are saved
 * as eval-flagged versions that
 *  - never become the row other users are served (the shared version-1 row
 *    keeps `isLatest`, new submitters still attach to it, version lists hide
 *    eval rows from them);
 *  - never prune user versions (each pool is pruned among itself);
 *  - still come back to the eval user through its own library entry, which is
 *    how scripts/_eval_api.py reads a run (POST → stream → GET /api/videos/:id).
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

describe('VideoService eval-user versions (integration)', () => {
  let mongod: MongoMemoryServer;
  let client: MongoClient;
  let db: Db;
  let service: VideoService;
  let evalUser: string;
  let alice: string;
  let bob: string;
  const publishVideoJob = vi.fn();
  const originalUseQueue = config.USE_QUEUE_PIPELINE;

  const cache = () => db.collection('videoSummaryCache');
  const submit = (userId: string, bypassCache = false) =>
    service.createVideo(userId, URL, { tier: 'free', bypassCache });

  async function markCompleted(videoSummaryId: string): Promise<void> {
    await cache().updateOne({ _id: new ObjectId(videoSummaryId) }, { $set: { status: 'completed' } });
  }

  /** Alice's first submission — creates and completes the shared row. */
  async function seedSharedRow(): Promise<string> {
    const { video } = await submit(alice);
    await markCompleted(video.videoSummaryId);
    return video.videoSummaryId;
  }

  async function versionsInPool(evalRun: boolean): Promise<number[]> {
    const filter = evalRun ? { evalRun: true } : { evalRun: { $ne: true } };
    const rows = await cache().find({ youtubeId: YOUTUBE_ID, ...filter }).sort({ version: 1 }).toArray();
    return rows.map(r => r.version as number);
  }

  beforeAll(async () => {
    mongod = await MongoMemoryServer.create();
    client = new MongoClient(mongod.getUri());
    await client.connect();
    db = client.db('video-eval-versions');
    // Production index spec (src/plugins/mongodb.ts): versioned dedupKeys collide here.
    await cache().createIndex(
      { dedupKey: 1 },
      { unique: true, partialFilterExpression: { dedupKey: { $exists: true } } },
    );
    const users = new UserRepository(db);
    const create = async (name: string) =>
      (await users.create({ email: `${name}@test.local`, passwordHash: 'h', name }))._id;
    const evalId = await create('eval');
    await db.collection('users').updateOne({ _id: evalId }, { $set: { isEvalUser: true } });
    evalUser = evalId.toString();
    alice = (await create('alice')).toString();
    bob = (await create('bob')).toString();

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

  describe('serving other users', () => {
    it('should keep the shared row latest when the eval user bypasses the cache', async () => {
      const sharedId = await seedSharedRow();

      await submit(evalUser, true);

      const shared = await cache().findOne({ _id: new ObjectId(sharedId) });
      expect(shared?.isLatest).toBe(true);
    });

    it('should save the eval run as an eval-flagged version that is never latest', async () => {
      await seedSharedRow();

      const { video } = await submit(evalUser, true);

      const row = await cache().findOne({ _id: new ObjectId(video.videoSummaryId) });
      expect({ evalRun: row?.evalRun, isLatest: row?.isLatest }).toEqual({ evalRun: true, isLatest: false });
    });

    it('should serve a new user the shared row, not the eval version', async () => {
      const sharedId = await seedSharedRow();
      await submit(evalUser, true);

      const result = await submit(bob);

      expect({ id: result.video.videoSummaryId, cached: result.cached }).toEqual({ id: sharedId, cached: true });
    });

    it('should not create the shared row when the eval user runs a video nobody has', async () => {
      const evalRun = await submit(evalUser, true);

      const result = await submit(bob);

      const served = await cache().findOne({ _id: new ObjectId(result.video.videoSummaryId) });
      expect({
        separateRow: result.video.videoSummaryId !== evalRun.video.videoSummaryId,
        servedVersion: served?.version,
        servedEval: served?.evalRun,
      }).toEqual({ separateRow: true, servedVersion: 1, servedEval: undefined });
    });

    it('should create an eval version instead of attaching when the eval user does not bypass', async () => {
      const sharedId = await seedSharedRow();

      const { video } = await submit(evalUser);

      const row = await cache().findOne({ _id: new ObjectId(video.videoSummaryId) });
      expect({ attached: video.videoSummaryId === sharedId, evalRun: row?.evalRun }).toEqual({
        attached: false,
        evalRun: true,
      });
    });

    it('should let the summarizer answer from its response cache when the eval user does not bypass', async () => {
      await seedSharedRow();

      const { video } = await submit(evalUser);

      const row = await cache().findOne({ _id: new ObjectId(video.videoSummaryId) });
      expect({ forceRefresh: row?.forceRefresh, published: publishVideoJob.mock.calls.at(-1)?.[0].bypassCache })
        .toEqual({ forceRefresh: undefined, published: false });
    });

    it('should number a user bypass past existing eval versions instead of colliding', async () => {
      await seedSharedRow();
      await submit(evalUser, true);

      const result = await submit(alice, true);

      expect('version' in result.video ? result.video.version : undefined).toBe(3);
    });
  });

  describe('pruning', () => {
    it('should keep every user version when eval runs exceed the version cap', async () => {
      await seedSharedRow();
      await submit(alice, true);

      for (let run = 0; run < 7; run += 1) {
        await submit(evalUser, true);
      }

      await vi.waitFor(async () => expect(await versionsInPool(true)).toHaveLength(5));
      expect(await versionsInPool(false)).toEqual([1, 2]);
    });

    it('should keep the newest eval versions when the eval pool is pruned', async () => {
      await seedSharedRow();

      for (let run = 0; run < 7; run += 1) {
        await submit(evalUser, true);
      }

      await vi.waitFor(async () => expect(await versionsInPool(true)).toEqual([4, 5, 6, 7, 8]));
    });
  });

  describe('eval readback', () => {
    it('should return the eval version to the eval user through its library entry', async () => {
      await seedSharedRow();
      const { video } = await submit(evalUser, true);

      const doc = await service.getVideo(evalUser, video.id);

      expect(doc.videoSummaryId).toBe(video.videoSummaryId);
    });

    it('should list only the newest eval run in the eval user library', async () => {
      await seedSharedRow();
      await submit(evalUser, true);
      const latest = await submit(evalUser, true);

      const { videos } = await service.getVideos(evalUser);

      expect(videos.map(v => v.videoSummaryId)).toEqual([latest.video.videoSummaryId]);
    });
  });

  describe('getVersions', () => {
    it('should hide eval versions from a non-eval user', async () => {
      await seedSharedRow();
      await submit(evalUser, true);

      const versions = await service.getVersions(alice, YOUTUBE_ID);

      expect(versions.map(v => v.version)).toEqual([1]);
    });

    it('should list eval versions to the eval user', async () => {
      await seedSharedRow();
      await submit(evalUser, true);

      const versions = await service.getVersions(evalUser, YOUTUBE_ID);

      expect(versions.map(v => v.version)).toEqual([2, 1]);
    });
  });
});
