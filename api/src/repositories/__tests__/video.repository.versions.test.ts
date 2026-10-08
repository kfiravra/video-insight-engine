import { describe, it, expect, beforeAll, afterAll, beforeEach } from 'vitest';
import { MongoMemoryServer } from 'mongodb-memory-server';
import { MongoClient, Db } from 'mongodb';
import {
  VideoRepository,
  type CreateVideoSummaryData,
} from '../video.repository.js';

/**
 * D25 version pools: eval-user rows (`evalRun: true`) and every other row are
 * listed and pruned as separate pools — an eval run can never delete a user's
 * version, and other users never see eval versions.
 */
describe('VideoRepository version pools', () => {
  let mongod: MongoMemoryServer;
  let client: MongoClient;
  let db: Db;
  let repo: VideoRepository;

  const YOUTUBE_ID = 'dQw4w9WgXcQ';

  const makeCacheData = (
    overrides: Partial<CreateVideoSummaryData> = {},
  ): CreateVideoSummaryData => ({
    youtubeId: YOUTUBE_ID,
    url: `https://www.youtube.com/watch?v=${YOUTUBE_ID}`,
    status: 'completed',
    version: 1,
    isLatest: false,
    retryCount: 0,
    ...overrides,
  });

  async function seed(userVersions: number[], evalVersions: number[]): Promise<void> {
    for (const version of userVersions) {
      await repo.createCacheEntry(makeCacheData({ version }));
    }
    for (const version of evalVersions) {
      await repo.createCacheEntry(makeCacheData({ version, evalRun: true }));
    }
  }

  async function versionsOf(evalRun: boolean): Promise<number[]> {
    const filter = evalRun ? { evalRun: true } : { evalRun: { $ne: true } };
    const rows = await db.collection('videoSummaryCache').find(filter).sort({ version: 1 }).toArray();
    return rows.map(r => r.version as number);
  }

  beforeAll(async () => {
    mongod = await MongoMemoryServer.create();
    client = new MongoClient(mongod.getUri());
    await client.connect();
    db = client.db('test');
    repo = new VideoRepository(db);
  });

  afterAll(async () => {
    await client.close();
    await mongod.stop();
  });

  beforeEach(async () => {
    await db.collection('videoSummaryCache').deleteMany({});
  });

  describe('pruneVersions', () => {
    it('should keep every user version when the eval pool is pruned', async () => {
      await seed([1, 2, 3], [4, 5, 6, 7, 8, 9, 10]);

      await repo.pruneVersions(YOUTUBE_ID, { evalRun: true, keep: 5 });

      expect(await versionsOf(false)).toEqual([1, 2, 3]);
    });

    it('should keep only the newest eval versions when the eval pool is pruned', async () => {
      await seed([1, 2, 3], [4, 5, 6, 7, 8, 9, 10]);

      const deleted = await repo.pruneVersions(YOUTUBE_ID, { evalRun: true, keep: 5 });

      expect({ deleted, kept: await versionsOf(true) }).toEqual({ deleted: 2, kept: [6, 7, 8, 9, 10] });
    });

    it('should keep every eval version when the user pool is pruned', async () => {
      await seed([1, 2, 3, 4, 5, 6, 7], [8, 9]);

      await repo.pruneVersions(YOUTUBE_ID, { evalRun: false, keep: 5 });

      expect(await versionsOf(true)).toEqual([8, 9]);
    });

    it('should rank user versions past gaps left by eval versions', async () => {
      // Arithmetic on the newest number (v12 - 5) would delete v1..v7 here and
      // leave the user with 1 version; ranking keeps the 5 newest user rows.
      await seed([1, 2, 3, 4, 5, 12], [6, 7, 8, 9, 10, 11]);

      await repo.pruneVersions(YOUTUBE_ID, { evalRun: false, keep: 5 });

      expect(await versionsOf(false)).toEqual([2, 3, 4, 5, 12]);
    });

    it('should delete nothing when the pool is within the limit', async () => {
      await seed([1, 2], [3]);

      const deleted = await repo.pruneVersions(YOUTUBE_ID, { evalRun: false, keep: 5 });

      expect(deleted).toBe(0);
    });

    it('should leave other videos untouched', async () => {
      await seed([1, 2, 3], []);
      await repo.createCacheEntry(makeCacheData({ youtubeId: 'otherVideo1', version: 1 }));

      await repo.pruneVersions(YOUTUBE_ID, { evalRun: false, keep: 1 });

      expect(await db.collection('videoSummaryCache').countDocuments({ youtubeId: 'otherVideo1' })).toBe(1);
    });
  });

  describe('getVersions', () => {
    it('should hide eval versions when includeEval is false', async () => {
      await seed([1, 2], [3, 4]);

      const rows = await repo.getVersions(YOUTUBE_ID, 10, { includeEval: false });

      expect(rows.map(r => r.version)).toEqual([2, 1]);
    });

    it('should list eval versions when includeEval is true', async () => {
      await seed([1, 2], [3, 4]);

      const rows = await repo.getVersions(YOUTUBE_ID, 10, { includeEval: true });

      expect(rows.map(r => r.version)).toEqual([4, 3, 2, 1]);
    });
  });

  describe('findHighestVersion', () => {
    it('should count eval versions when no pool is given', async () => {
      await seed([1, 2], [3]);

      const row = await repo.findHighestVersion(YOUTUBE_ID);

      expect(row?.version).toBe(3);
    });

    it('should ignore eval versions when asked for the user pool', async () => {
      await seed([1, 2], [3]);

      const row = await repo.findHighestVersion(YOUTUBE_ID, false);

      expect(row?.version).toBe(2);
    });

    it('should return null when the user pool is empty', async () => {
      await seed([], [2, 3]);

      expect(await repo.findHighestVersion(YOUTUBE_ID, false)).toBeNull();
    });
  });
});
