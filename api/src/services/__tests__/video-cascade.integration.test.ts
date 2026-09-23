/**
 * Compliance-style gate for video deletion, mirroring gdpr-cascade:
 *  - a GLOBAL delete removes every row of the target video in every Mongo
 *    collection and calls the summarizer purge, leaving a witness video and
 *    its users untouched;
 *  - a USER delete removes only that user's row, key and notes and never
 *    touches the shared summary, other users, or the summarizer.
 */
import { describe, it, expect, beforeAll, afterAll, beforeEach, vi } from 'vitest';
import { MongoMemoryServer } from 'mongodb-memory-server';
import { MongoClient, ObjectId } from 'mongodb';
import type { Db } from 'mongodb';
import type { FastifyBaseLogger } from 'fastify';
import { VideoRepository } from '../../repositories/video.repository.js';
import { VideoDeletionRepository } from '../../repositories/video-deletion.repository.js';
import { IdempotencyRepository } from '../../repositories/idempotency.repository.js';
import { IdempotencyService } from '../idempotency.service.js';
import { noOpDispatchGuard } from '../dispatch-guard.service.js';
import { VideoCascadeService } from '../video-cascade.service.js';
import type { SummarizerClient } from '../summarizer-client.js';

const silentLogger = {
  info: vi.fn(),
  warn: vi.fn(),
  error: vi.fn(),
  debug: vi.fn(),
} as unknown as FastifyBaseLogger;

const TARGET = 'targetVid01';
const WITNESS = 'witnessVid1';

interface Seeded {
  latestId: ObjectId;
  rowIds: ObjectId[];
}

async function seedVideo(db: Db, youtubeId: string, users: ObjectId[]): Promise<Seeded> {
  const v1 = new ObjectId();
  const v2 = new ObjectId();
  const slug = `slug-${youtubeId}`;
  await db.collection('videoSummaryCache').insertMany([
    { _id: v1, youtubeId, version: 1, isLatest: false, status: 'completed', shareSlug: slug },
    { _id: v2, youtubeId, version: 2, isLatest: true, status: 'completed' },
  ]);
  const rowIds = users.map(() => new ObjectId());
  await db.collection('userVideos').insertMany([
    ...users.map((userId, i) => ({ _id: rowIds[i], userId, videoSummaryId: v2, youtubeId, status: 'completed' })),
    // A third user's row already orphaned by an earlier summary deletion — still caught by youtubeId.
    { _id: new ObjectId(), userId: new ObjectId(), videoSummaryId: new ObjectId(), youtubeId, status: 'completed' },
  ]);
  await db.collection('shareLikes').insertOne({ shareSlug: slug, ipHash: 'h' });
  await db.collection('shareViews').insertOne({ shareSlug: slug, viewedAt: new Date() });
  await db.collection('agentNotes').insertMany([
    { userId: users[0].toString(), videoId: v2.toString(), text: 'by summary id' },
    { userId: users[1].toString(), videoId: youtubeId, text: 'by youtube id' },
  ]);
  await db.collection('idempotencyKeys').insertMany([
    { hash: `h-${youtubeId}-a`, userId: users[0], userVideoId: rowIds[0], videoSummaryId: v2, youtubeId, status: 'completed' },
    { hash: `h-${youtubeId}-b`, userId: users[1], userVideoId: rowIds[1], videoSummaryId: v2, youtubeId, status: 'completed' },
  ]);
  return { latestId: v2, rowIds };
}

async function count(db: Db, collection: string, filter: Record<string, unknown>): Promise<number> {
  return db.collection(collection).countDocuments(filter);
}

describe('video deletion cascade (integration)', () => {
  let mongod: MongoMemoryServer;
  let client: MongoClient;
  let db: Db;
  let service: VideoCascadeService;
  const purgeVideo = vi.fn();
  const userA = new ObjectId();
  const userB = new ObjectId();

  beforeAll(async () => {
    mongod = await MongoMemoryServer.create();
    client = new MongoClient(mongod.getUri());
    await client.connect();
    db = client.db('video-cascade-e2e');
    const summarizerClient = { purgeVideo } as unknown as SummarizerClient;
    service = new VideoCascadeService(
      db,
      new VideoRepository(db),
      new IdempotencyService(new IdempotencyRepository(db), silentLogger),
      noOpDispatchGuard,
      summarizerClient,
      new VideoDeletionRepository(db),
      silentLogger,
    );
  });

  afterAll(async () => {
    await client.close();
    await mongod.stop();
  });

  beforeEach(async () => {
    purgeVideo.mockReset();
    purgeVideo.mockResolvedValue({ qdrantPoints: 5, s3Objects: 7, redisKeys: 3, warnings: ['s3: partial'] });
    const collections = await db.listCollections().toArray();
    for (const c of collections) await db.collection(c.name).deleteMany({});
  });

  it('removes every row of the target video everywhere and leaves the witness alone', async () => {
    const target = await seedVideo(db, TARGET, [userA, userB]);
    await seedVideo(db, WITNESS, [userA, userB]);

    const result = await service.deleteVideo({
      scope: 'global',
      youtubeId: TARGET,
      actor: { initiatedBy: 'admin', adminId: 'kfir', reason: 'integration' },
    });

    // ─── Target: gone from every collection ───────────────────────────────
    expect(await count(db, 'videoSummaryCache', { youtubeId: TARGET })).toBe(0);
    expect(await count(db, 'userVideos', { youtubeId: TARGET })).toBe(0);
    expect(await count(db, 'shareLikes', { shareSlug: `slug-${TARGET}` })).toBe(0);
    expect(await count(db, 'shareViews', { shareSlug: `slug-${TARGET}` })).toBe(0);
    expect(await count(db, 'agentNotes', { videoId: { $in: [target.latestId.toString(), TARGET] } })).toBe(0);
    expect(await count(db, 'idempotencyKeys', { youtubeId: TARGET })).toBe(0);

    // ─── Witness: untouched ───────────────────────────────────────────────
    expect(await count(db, 'videoSummaryCache', { youtubeId: WITNESS })).toBe(2);
    expect(await count(db, 'userVideos', { youtubeId: WITNESS })).toBe(3);
    expect(await count(db, 'shareLikes', { shareSlug: `slug-${WITNESS}` })).toBe(1);
    expect(await count(db, 'agentNotes', { videoId: WITNESS })).toBe(1);
    expect(await count(db, 'idempotencyKeys', { youtubeId: WITNESS })).toBe(2);

    // ─── Summarizer + audit ───────────────────────────────────────────────
    expect(purgeVideo).toHaveBeenCalledTimes(1);
    expect(purgeVideo.mock.calls[0][0].videoSummaryIds).toHaveLength(2);
    expect(result.counts).toMatchObject({
      videoSummaryCache: 2, userVideos: 3, shareLikes: 1, shareViews: 1, agentNotes: 2,
      idempotencyKeys: 2, dispatchGuards: 2, qdrantPoints: 5, s3Objects: 7, redisKeys: 3,
    });
    expect(result.warnings).toEqual(['summarizer: s3: partial']);
    const audit = await db.collection('videoDeletions').findOne({ youtubeId: TARGET });
    expect(audit).toMatchObject({ initiatedBy: 'admin', adminId: 'kfir', counts: result.counts });
  });

  it('is a no-op on a second run for the same video', async () => {
    await seedVideo(db, TARGET, [userA, userB]);
    await service.deleteVideo({ scope: 'global', youtubeId: TARGET, actor: { initiatedBy: 'admin', adminId: null } });
    purgeVideo.mockResolvedValue({ qdrantPoints: 0, s3Objects: 0, redisKeys: 0, warnings: [] });

    const again = await service.deleteVideo({ scope: 'global', youtubeId: TARGET, actor: { initiatedBy: 'script', adminId: null } });

    expect(again.summaryIds).toEqual([]);
    expect(again.counts).toMatchObject({ videoSummaryCache: 0, userVideos: 0, agentNotes: 0, idempotencyKeys: 0 });
  });

  it('user delete keeps the shared summary and every other user intact', async () => {
    const target = await seedVideo(db, TARGET, [userA, userB]);

    const result = await service.deleteVideo({
      scope: 'user',
      userId: userA.toString(),
      userVideoId: target.rowIds[0].toString(),
    });

    expect(purgeVideo).not.toHaveBeenCalled();
    expect(await count(db, 'videoSummaryCache', { youtubeId: TARGET })).toBe(2);
    expect(await count(db, 'userVideos', { _id: target.rowIds[0] })).toBe(0);
    expect(await count(db, 'userVideos', { _id: target.rowIds[1] })).toBe(1);
    expect(await count(db, 'idempotencyKeys', { userVideoId: target.rowIds[0] })).toBe(0);
    expect(await count(db, 'idempotencyKeys', { userVideoId: target.rowIds[1] })).toBe(1);
    expect(await count(db, 'agentNotes', { userId: userA.toString() })).toBe(0);
    expect(await count(db, 'agentNotes', { userId: userB.toString() })).toBe(1);
    expect(await count(db, 'shareLikes', { shareSlug: `slug-${TARGET}` })).toBe(1);
    expect(result.counts).toMatchObject({ userVideos: 1, idempotencyKeys: 1, agentNotes: 1, videoSummaryCache: 0 });
    expect(await count(db, 'videoDeletions', {})).toBe(0);
  });
});
