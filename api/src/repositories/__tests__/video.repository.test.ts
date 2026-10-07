import { describe, it, expect, beforeAll, afterAll, beforeEach } from 'vitest';
import { MongoMemoryServer } from 'mongodb-memory-server';
import { MongoClient, Db, ObjectId } from 'mongodb';
import {
  VideoRepository,
  type CreateVideoSummaryData,
} from '../video.repository.js';

describe('VideoRepository', () => {
  let mongod: MongoMemoryServer;
  let client: MongoClient;
  let db: Db;
  let repo: VideoRepository;

  const makeCacheData = (
    overrides: Partial<CreateVideoSummaryData> = {},
  ): CreateVideoSummaryData => ({
    youtubeId: 'dQw4w9WgXcQ',
    url: 'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
    status: 'pending',
    version: 1,
    isLatest: true,
    retryCount: 0,
    ...overrides,
  });

  beforeAll(async () => {
    mongod = await MongoMemoryServer.create();
    client = new MongoClient(mongod.getUri());
    await client.connect();
    db = client.db('test');
    repo = new VideoRepository(db);

    // Mirror the production index spec — partial unique on dedupKey so legacy
    // rows without the field don't trip the constraint. This is asserted in
    // src/plugins/mongodb.ts at runtime; we reproduce it here so the repo
    // tests catch any divergence between spec and behaviour.
    await db.collection('videoSummaryCache').createIndex(
      { dedupKey: 1 },
      { unique: true, partialFilterExpression: { dedupKey: { $exists: true } } },
    );
  });

  afterAll(async () => {
    await client.close();
    await mongod.stop();
  });

  beforeEach(async () => {
    await db.collection('videoSummaryCache').deleteMany({});
  });

  describe('upsertCacheByDedupKey', () => {
    it('should insert a fresh row when the dedupKey has never been seen', async () => {
      const data = makeCacheData();
      const result = await repo.upsertCacheByDedupKey({ ...data, dedupKey: 'key-1' });

      expect(result.wasInsert).toBe(true);
      expect(result.doc.dedupKey).toBe('key-1');
      expect(result.doc.youtubeId).toBe(data.youtubeId);
      expect(result.doc.status).toBe('pending');
      expect(result.doc.createdAt).toBeInstanceOf(Date);
      expect(result.doc.updatedAt).toBeInstanceOf(Date);
    });

    it('should return the existing row when the dedupKey already exists', async () => {
      const first = await repo.upsertCacheByDedupKey({
        ...makeCacheData(),
        dedupKey: 'key-shared',
      });

      // Same dedupKey, different user-supplied URL — must NOT overwrite the
      // existing row. $setOnInsert is the load-bearing operator here.
      const second = await repo.upsertCacheByDedupKey({
        ...makeCacheData({ url: 'https://example.com/different' }),
        dedupKey: 'key-shared',
      });

      expect(first.wasInsert).toBe(true);
      expect(second.wasInsert).toBe(false);
      expect(second.doc._id.toString()).toBe(first.doc._id.toString());
      expect(second.doc.url).toBe(first.doc.url); // original URL preserved
    });

    it('should serialize concurrent upserts on the same dedupKey — exactly one insert', async () => {
      // The cross-user dedup property: 5 simultaneous submits for the same
      // (youtubeId, providers, version) collapse onto one cache row.
      const results = await Promise.all(
        Array.from({ length: 5 }, () =>
          repo.upsertCacheByDedupKey({
            ...makeCacheData(),
            dedupKey: 'concurrent-key',
          }),
        ),
      );

      const inserters = results.filter((r) => r.wasInsert);
      expect(inserters).toHaveLength(1);

      const uniqueIds = new Set(results.map((r) => r.doc._id.toString()));
      expect(uniqueIds.size).toBe(1);

      const count = await db
        .collection('videoSummaryCache')
        .countDocuments({ dedupKey: 'concurrent-key' });
      expect(count).toBe(1);
    });

    it('should allow distinct dedupKeys to coexist for the same youtubeId', async () => {
      // bypassCache version-bump produces a new dedupKey (different `v${n}`)
      // and must insert a NEW row, not attach to the existing one.
      await repo.upsertCacheByDedupKey({
        ...makeCacheData({ version: 1, isLatest: false }),
        dedupKey: 'key-v1',
      });
      const v2 = await repo.upsertCacheByDedupKey({
        ...makeCacheData({ version: 2, isLatest: true }),
        dedupKey: 'key-v2',
      });

      expect(v2.wasInsert).toBe(true);
      expect(v2.doc.version).toBe(2);

      const count = await db
        .collection('videoSummaryCache')
        .countDocuments({ youtubeId: 'dQw4w9WgXcQ' });
      expect(count).toBe(2);
    });

    it('should not refresh updatedAt on attach (the existing row is untouched)', async () => {
      const first = await repo.upsertCacheByDedupKey({
        ...makeCacheData(),
        dedupKey: 'untouched',
      });
      const originalUpdatedAt = first.doc.updatedAt;

      await new Promise((r) => setTimeout(r, 5));
      const second = await repo.upsertCacheByDedupKey({
        ...makeCacheData(),
        dedupKey: 'untouched',
      });

      expect(second.wasInsert).toBe(false);
      expect(second.doc.updatedAt.getTime()).toBe(originalUpdatedAt.getTime());
    });
  });

  describe('tryClaimDispatchRelease', () => {
    it('should return true on the first FAILED event for a failed row', async () => {
      // Seed a row mimicking the state immediately after the summarizer
      // wrote status=failed: status='failed' AND no `dispatchGuardReleasedAt`.
      const { doc } = await repo.upsertCacheByDedupKey({
        ...makeCacheData(),
        dedupKey: 'claim-first',
      });
      await repo.updateCacheEntry(doc._id.toString(), { status: 'failed' });

      const claimed = await repo.tryClaimDispatchRelease(doc._id.toString());

      expect(claimed).toBe(true);
    });

    it('should return false on the second concurrent FAILED event (duplicate)', async () => {
      // Reproduces the audited race: summarizer status callback fires twice
      // (HTTP retry on transient failure). The second event must NOT release
      // the Redis lock — that's what previously wiped a freshly-acquired
      // dispatch lock from a user-driven retry.
      const { doc } = await repo.upsertCacheByDedupKey({
        ...makeCacheData(),
        dedupKey: 'claim-dup',
      });
      await repo.updateCacheEntry(doc._id.toString(), { status: 'failed' });

      const first = await repo.tryClaimDispatchRelease(doc._id.toString());
      const second = await repo.tryClaimDispatchRelease(doc._id.toString());

      expect(first).toBe(true);
      expect(second).toBe(false);
    });

    it('should return false when the row is no longer in failed status', async () => {
      // Retry-between-events scenario: user retried between Pipeline A failing
      // and the FAILED event landing. The retry flipped status back to
      // 'pending', so the stale FAILED event must be a no-op — otherwise it
      // would race to release the fresh dispatch's Redis lock.
      const { doc } = await repo.upsertCacheByDedupKey({
        ...makeCacheData(),
        dedupKey: 'claim-stale',
      });
      // Note: incrementRetryCount sets status='pending' (the retry transition).
      await repo.incrementRetryCount(doc._id.toString());

      const claimed = await repo.tryClaimDispatchRelease(doc._id.toString());

      expect(claimed).toBe(false);
    });

    it('should allow a fresh claim after retryCount bumps the row (incrementRetryCount clears the marker)', async () => {
      // After the retry runs the pipeline again and that run also fails, the
      // next FAILED event must once again be claimable — the marker reset on
      // retry is what enables this. Without the $unset in incrementRetryCount,
      // every subsequent failure would skip release and the user would be
      // stuck behind the 900s TTL.
      const { doc } = await repo.upsertCacheByDedupKey({
        ...makeCacheData(),
        dedupKey: 'claim-after-retry',
      });
      await repo.updateCacheEntry(doc._id.toString(), { status: 'failed' });
      expect(await repo.tryClaimDispatchRelease(doc._id.toString())).toBe(true);

      // Simulate user-driven retry: status → pending, marker unset.
      await repo.incrementRetryCount(doc._id.toString());
      // Simulate pipeline failing again.
      await repo.updateCacheEntry(doc._id.toString(), { status: 'failed' });

      expect(await repo.tryClaimDispatchRelease(doc._id.toString())).toBe(true);
    });

    it('should return false when the row does not exist', async () => {
      const claimed = await repo.tryClaimDispatchRelease('507f1f77bcf86cd799439011');
      expect(claimed).toBe(false);
    });
  });

  describe('mergeSynthesis', () => {
    // memory-done emission (hero fields only) and the synthesis-done superset.
    const early = { tldr: 'Memory tldr', keyTakeaways: ['Memory point'] };
    const late = {
      tldr: 'Final tldr',
      keyTakeaways: ['Final point 1', 'Final point 2'],
      masterSummary: 'The full master summary.',
      seoDescription: 'SEO description.',
    };

    async function seedRow(dedupKey: string): Promise<string> {
      const { doc } = await repo.upsertCacheByDedupKey({ ...makeCacheData(), dedupKey });
      return doc._id.toString();
    }

    async function storedSynthesis(id: string): Promise<unknown> {
      const doc = await db.collection('videoSummaryCache').findOne({ _id: new ObjectId(id) });
      return doc?.synthesis;
    }

    it('should persist the hero fields when only the early emission arrives', async () => {
      const id = await seedRow('synth-early');

      await repo.mergeSynthesis(id, early);

      expect(await storedSynthesis(id)).toEqual(early);
    });

    it('should persist every field when only the late emission arrives', async () => {
      const id = await seedRow('synth-late');

      await repo.mergeSynthesis(id, late);

      expect(await storedSynthesis(id)).toEqual(late);
    });

    it('should let the superset overwrite the early fields when early lands before late', async () => {
      const id = await seedRow('synth-early-late');

      await repo.mergeSynthesis(id, early);
      await repo.mergeSynthesis(id, late);

      expect(await storedSynthesis(id)).toEqual(late);
    });

    it('should keep the full synthesis when a replayed early emission lands after late', async () => {
      const id = await seedRow('synth-late-early');

      await repo.mergeSynthesis(id, late);
      await repo.mergeSynthesis(id, early);

      expect(await storedSynthesis(id)).toEqual(late);
    });

    it('should be idempotent when the whole sequence is replayed', async () => {
      const id = await seedRow('synth-replay');

      for (const emission of [early, late, early, late]) {
        await repo.mergeSynthesis(id, emission);
      }

      expect(await storedSynthesis(id)).toEqual(late);
    });

    it('should keep the early hero fields when the late emission leaves them empty', async () => {
      const id = await seedRow('synth-late-empty-hero');

      await repo.mergeSynthesis(id, early);
      await repo.mergeSynthesis(id, { ...late, tldr: '', keyTakeaways: [] });

      expect(await storedSynthesis(id)).toEqual({
        ...early,
        masterSummary: late.masterSummary,
        seoDescription: late.seoDescription,
      });
    });

    it('should not touch the row when the emission carries no content', async () => {
      const id = await seedRow('synth-all-empty');
      await repo.mergeSynthesis(id, early);

      await repo.mergeSynthesis(id, { tldr: '', keyTakeaways: [], masterSummary: '', seoDescription: '' });

      expect(await storedSynthesis(id)).toEqual(early);
    });
  });
});
