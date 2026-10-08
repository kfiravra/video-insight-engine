/**
 * 1d.8 gate: a failed or incomplete re-run never replaces a completed served
 * version. A bypassCache re-run is inserted unserved (`isLatest: false`) and
 * takes over only when its run completes (the summarizer's `completed` status
 * relay → promoteCompletedVersion); a failed re-run hands the requester's
 * library entry back to the served version (restoreServedVersion). Eval rows
 * (D25) are never promoted and never re-pointed. Also pins the eval
 * renumbering on a version-number race (G23) and the synthesis reset on a
 * re-dispatch (G10).
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

describe('VideoService served version (integration)', () => {
  let mongod: MongoMemoryServer;
  let client: MongoClient;
  let db: Db;
  let repo: VideoRepository;
  let service: VideoService;
  let evalUser: string;
  let alice: string;
  let bob: string;
  const publishVideoJob = vi.fn();
  const originalUseQueue = config.USE_QUEUE_PIPELINE;

  const cache = () => db.collection('videoSummaryCache');
  const submit = (userId: string, bypassCache = false) =>
    service.createVideo(userId, URL, { tier: 'free', bypassCache });

  async function setStatus(videoSummaryId: string, status: 'completed' | 'failed'): Promise<void> {
    await cache().updateOne({ _id: new ObjectId(videoSummaryId) }, { $set: { status } });
  }

  /** What the `completed` status relay does once the summarizer saved the run. */
  async function complete(videoSummaryId: string): Promise<void> {
    await setStatus(videoSummaryId, 'completed');
    await repo.promoteCompletedVersion(videoSummaryId);
  }

  /** What the `failed` status relay does once the summarizer marked the run failed. */
  async function fail(videoSummaryId: string): Promise<void> {
    await setStatus(videoSummaryId, 'failed');
    await repo.restoreServedVersion(videoSummaryId);
  }

  async function servedId(): Promise<string | undefined> {
    const rows = await cache().find({ youtubeId: YOUTUBE_ID, isLatest: true }).toArray();
    return rows.map(r => r._id.toString()).join(',') || undefined;
  }

  async function libraryEntry(userId: string): Promise<string | undefined> {
    const row = await db.collection('userVideos').findOne({ userId: new ObjectId(userId), youtubeId: YOUTUBE_ID });
    return row?.videoSummaryId.toString();
  }

  /** Alice's first submission — creates and completes the shared row. */
  async function seedSharedRow(): Promise<string> {
    const { video } = await submit(alice);
    await setStatus(video.videoSummaryId, 'completed');
    return video.videoSummaryId;
  }

  beforeAll(async () => {
    mongod = await MongoMemoryServer.create();
    client = new MongoClient(mongod.getUri());
    await client.connect();
    db = client.db('video-served-version');
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
    repo = new VideoRepository(db);
    service = new VideoService(
      repo,
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
    vi.restoreAllMocks();
    publishVideoJob.mockReset().mockResolvedValue({ requestId: 'r' });
    await cache().deleteMany({});
    await db.collection('userVideos').deleteMany({});
  });

  describe('bypassCache re-run', () => {
    it('should keep serving the completed version while the re-run is in progress', async () => {
      const sharedId = await seedSharedRow();

      await submit(alice, true);

      expect(await servedId()).toBe(sharedId);
    });

    it('should keep serving the completed version when the re-run fails', async () => {
      const sharedId = await seedSharedRow();
      const rerun = await submit(alice, true);

      await fail(rerun.video.videoSummaryId);

      expect(await servedId()).toBe(sharedId);
    });

    it('should hand the requester library entry back to the served version when the re-run fails', async () => {
      const sharedId = await seedSharedRow();
      const rerun = await submit(alice, true);

      await fail(rerun.video.videoSummaryId);

      expect(await libraryEntry(alice)).toBe(sharedId);
    });

    it('should serve the re-run once it completes', async () => {
      await seedSharedRow();
      const rerun = await submit(alice, true);

      await complete(rerun.video.videoSummaryId);

      expect(await servedId()).toBe(rerun.video.videoSummaryId);
    });

    it('should not let an older re-run that completes late displace a newer served one', async () => {
      await seedSharedRow();
      const older = await submit(alice, true);
      const newer = await submit(alice, true);
      await complete(newer.video.videoSummaryId);

      await complete(older.video.videoSummaryId);

      expect(await servedId()).toBe(newer.video.videoSummaryId);
    });

    it('should keep the served version when failed re-runs outnumber the version cap', async () => {
      const sharedId = await seedSharedRow();
      for (let i = 0; i < 6; i++) {
        const rerun = await submit(alice, true);
        await fail(rerun.video.videoSummaryId);
      }

      await vi.waitFor(async () => expect(await cache().countDocuments({ youtubeId: YOUTUBE_ID })).toBe(6));
      expect(await servedId()).toBe(sharedId);
    });
  });

  describe('eval rows', () => {
    it('should never serve a completed eval run', async () => {
      const sharedId = await seedSharedRow();
      const evalRun = await submit(evalUser, true);

      await complete(evalRun.video.videoSummaryId);

      expect(await servedId()).toBe(sharedId);
    });

    it('should leave the eval user library on a failed eval run', async () => {
      await seedSharedRow();
      const evalRun = await submit(evalUser, true);

      await fail(evalRun.video.videoSummaryId);

      expect(await libraryEntry(evalUser)).toBe(evalRun.video.videoSummaryId);
    });

    it('should renumber an eval run whose version a concurrent run took', async () => {
      await seedSharedRow();
      await submit(alice, true);
      // Stale read: the eval run computes version 2, which alice's re-run holds.
      vi.spyOn(repo, 'findHighestVersion').mockResolvedValueOnce(null);

      const evalRun = await submit(evalUser, true);

      expect('version' in evalRun.video ? evalRun.video.version : undefined).toBe(3);
    });
  });

  describe('re-dispatch', () => {
    it('should drop the previous run synthesis when a failed row is retried', async () => {
      const sharedId = await seedSharedRow();
      await cache().updateOne(
        { _id: new ObjectId(sharedId) },
        { $set: { status: 'failed', synthesis: { masterSummary: 'Old run summary.' } } },
      );

      await submit(bob);

      expect((await cache().findOne({ _id: new ObjectId(sharedId) }))?.synthesis).toBeUndefined();
    });
  });
});
