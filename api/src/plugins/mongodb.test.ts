import { describe, it, expect, vi, beforeAll, afterAll, beforeEach } from 'vitest';
import Fastify, { FastifyInstance } from 'fastify';
import { MongoMemoryServer } from 'mongodb-memory-server';
import { MongoClient } from 'mongodb';

// Note: The mongodb plugin uses process.env.MONGODB_URI which is set in test/env.ts
// For these tests, we use an in-memory MongoDB server to test the actual plugin behavior

describe('MongoDB plugin', () => {
  let mongod: MongoMemoryServer;
  let originalMongoUri: string | undefined;

  beforeAll(async () => {
    // Save original URI
    originalMongoUri = process.env.MONGODB_URI;

    // Start in-memory MongoDB
    mongod = await MongoMemoryServer.create();
    const uri = mongod.getUri();

    // Override env for tests
    process.env.MONGODB_URI = uri;
  });

  afterAll(async () => {
    // Restore original URI
    if (originalMongoUri) {
      process.env.MONGODB_URI = originalMongoUri;
    }

    if (mongod) {
      await mongod.stop();
    }
  });

  describe('connection handling', () => {
    let app: FastifyInstance;

    beforeAll(async () => {
      // Re-import the plugin to use the new MONGODB_URI
      const { mongodbPlugin } = await import('./mongodb.js');

      app = Fastify({ logger: false });
      await app.register(mongodbPlugin);
      await app.ready();
    });

    afterAll(async () => {
      await app.close();
    });

    it('should decorate fastify with mongo object', () => {
      expect(app.mongo).toBeDefined();
    });

    it('should have client property', () => {
      expect(app.mongo.client).toBeDefined();
      expect(app.mongo.client).toBeInstanceOf(MongoClient);
    });

    it('should have db property', () => {
      expect(app.mongo.db).toBeDefined();
    });

    it('should be able to perform database operations', async () => {
      const collection = app.mongo.db.collection('test');

      // Insert a document
      const result = await collection.insertOne({ test: 'value' });
      expect(result.acknowledged).toBe(true);

      // Read it back
      const doc = await collection.findOne({ test: 'value' });
      expect(doc).toBeDefined();
      expect(doc?.test).toBe('value');

      // Clean up
      await collection.deleteMany({});
    });
  });

  describe('index creation', () => {
    let app: FastifyInstance;

    beforeAll(async () => {
      const { mongodbPlugin } = await import('./mongodb.js');

      app = Fastify({ logger: false });
      await app.register(mongodbPlugin);
      await app.ready();
    });

    afterAll(async () => {
      await app.close();
    });

    it('should create indexes for videoSummaryCache collection', async () => {
      const indexes = await app.mongo.db.collection('videoSummaryCache').indexes();

      // Should have _id index plus our custom indexes
      expect(indexes.length).toBeGreaterThan(1);

      // Check for specific index
      const hasYoutubeIdIndex = indexes.some(idx =>
        idx.key && 'youtubeId' in idx.key
      );
      expect(hasYoutubeIdIndex).toBe(true);
    });

    it('should create indexes for userVideos collection', async () => {
      const indexes = await app.mongo.db.collection('userVideos').indexes();

      expect(indexes.length).toBeGreaterThan(1);

      const hasUserIdIndex = indexes.some(idx =>
        idx.key && 'userId' in idx.key
      );
      expect(hasUserIdIndex).toBe(true);
    });

    it('should create indexes for folders collection', async () => {
      const indexes = await app.mongo.db.collection('folders').indexes();

      expect(indexes.length).toBeGreaterThan(1);
    });

    it('should create unique index for users email', async () => {
      const indexes = await app.mongo.db.collection('users').indexes();

      const emailIndex = indexes.find(idx =>
        idx.key && 'email' in idx.key
      );
      expect(emailIndex).toBeDefined();
      expect(emailIndex?.unique).toBe(true);
    });

    it('should create a sparse videoSummaryId index on idempotencyKeys so pending placeholders are excluded', async () => {
      const indexes = await app.mongo.db.collection('idempotencyKeys').indexes();

      const videoSummaryIndex = indexes.find(idx =>
        idx.key && 'videoSummaryId' in idx.key
      );
      expect(videoSummaryIndex).toBeDefined();
      expect(videoSummaryIndex?.sparse).toBe(true);
    });

    it('should create a partial unique index on videoSummaryCache.dedupKey (Step 1c)', async () => {
      // Partial-on-$exists so legacy rows (no dedupKey) don't trip the
      // constraint. Without unique:true the cross-user dedup at the upsert
      // layer is unenforced.
      const indexes = await app.mongo.db.collection('videoSummaryCache').indexes();

      const dedupIndex = indexes.find(
        (idx) => idx.key && 'dedupKey' in idx.key && Object.keys(idx.key).length === 1,
      );
      expect(dedupIndex).toBeDefined();
      expect(dedupIndex?.unique).toBe(true);
      expect(dedupIndex?.partialFilterExpression).toEqual({ dedupKey: { $exists: true } });
    });

  });

  describe('llm_usage legacy TTL index migration', () => {
    it('should decide drop-vs-keep from the TTL option, not the index name', async () => {
      const { hasLegacyLlmUsageTtlIndex } = await import('./mongodb.js');

      // Legacy 90-day TTL variant → must be dropped.
      expect(hasLegacyLlmUsageTtlIndex([
        { name: 'createdAt_1', expireAfterSeconds: 90 * 24 * 60 * 60 },
      ])).toBe(true);

      // Current plain variant → dropping it would rebuild the ledger index
      // on EVERY boot (regression: the drop used to be unconditional).
      expect(hasLegacyLlmUsageTtlIndex([{ name: 'createdAt_1' }])).toBe(false);

      // Unrelated indexes / fresh collection → nothing to drop.
      expect(hasLegacyLlmUsageTtlIndex([{ name: '_id_' }])).toBe(false);
      expect(hasLegacyLlmUsageTtlIndex([])).toBe(false);
    });

    it('should replace a legacy TTL createdAt index with a plain (keep-forever) one', async () => {
      const { mongodbPlugin } = await import('./mongodb.js');

      // Pre-seed the pre-2026-07 state: createdAt_1 WITH a 90-day TTL.
      const raw = new MongoClient(process.env.MONGODB_URI as string);
      await raw.connect();
      const ledger = raw.db().collection('llm_usage');
      await ledger.dropIndex('createdAt_1').catch(() => undefined);
      await ledger.createIndex({ createdAt: 1 }, { expireAfterSeconds: 90 * 24 * 60 * 60 });

      const app = Fastify({ logger: false });
      await app.register(mongodbPlugin);
      await app.ready();

      const indexes = await app.mongo.db.collection('llm_usage').indexes();
      const createdAtIndex = indexes.find((idx) => idx.name === 'createdAt_1');
      expect(createdAtIndex).toBeDefined();
      // The financial ledger is kept forever — no TTL may survive startup.
      expect(createdAtIndex?.expireAfterSeconds).toBeUndefined();

      await app.close();
      await raw.close();
    });
  });

  describe('videoSummaryCache retired expiry TTL index', () => {
    it('should flag only a TTL expiresAt index when deciding what to drop', async () => {
      const { hasVideoExpiryTtlIndex } = await import('./mongodb.js');

      // Pre-2026-09-17 deploys: TTL index that hard-deleted shared rows.
      expect(hasVideoExpiryTtlIndex([{ name: 'expiresAt_1', expireAfterSeconds: 0 }])).toBe(true);

      // A plain index of the same name must not be rebuilt on every boot.
      expect(hasVideoExpiryTtlIndex([{ name: 'expiresAt_1' }])).toBe(false);

      // Unrelated indexes / fresh collection → nothing to drop.
      expect(hasVideoExpiryTtlIndex([{ name: '_id_' }])).toBe(false);
      expect(hasVideoExpiryTtlIndex([])).toBe(false);
    });

    it('should drop the retired expiresAt TTL index on startup and not recreate it', async () => {
      const { mongodbPlugin } = await import('./mongodb.js');

      // Pre-seed the pre-2026-09-17 state: a TTL index on expiresAt.
      const raw = new MongoClient(process.env.MONGODB_URI as string);
      await raw.connect();
      const cache = raw.db().collection('videoSummaryCache');
      await cache.dropIndex('expiresAt_1').catch(() => undefined);
      await cache.createIndex({ expiresAt: 1 }, { expireAfterSeconds: 0 });

      const app = Fastify({ logger: false });
      await app.register(mongodbPlugin);
      await app.ready();

      const indexes = await app.mongo.db.collection('videoSummaryCache').indexes();
      // Summaries never expire: no TTL index may survive startup.
      expect(indexes.find((idx) => idx.name === 'expiresAt_1')).toBeUndefined();

      await app.close();
      await raw.close();
    });
  });

  describe('dedupKey backfill (Step 1a)', () => {
    let app: FastifyInstance;

    beforeAll(async () => {
      const { mongodbPlugin } = await import('./mongodb.js');
      app = Fastify({ logger: false });
      await app.register(mongodbPlugin);
    });

    afterAll(async () => {
      await app.close();
    });

    beforeEach(async () => {
      // Drop the dedupKey index between scenarios so we can pre-seed legacy
      // rows AND ensure the plugin's startup hook recreates it deterministically.
      await app.mongo.db.collection('videoSummaryCache').deleteMany({});
      try {
        await app.mongo.db.collection('videoSummaryCache').dropIndex('dedupKey_1');
      } catch {
        /* index may not exist yet */
      }
    });

    it('should populate dedupKey on legacy rows missing the field', async () => {
      const { computeContentKey } = await import('../services/idempotency.service.js');
      const { runDedupKeyBackfill } = await import('./mongodb.js');

      // Pre-seed two legacy rows (no dedupKey field).
      await app.mongo.db.collection('videoSummaryCache').insertMany([
        { youtubeId: 'legacy-001', version: 1, status: 'completed', isLatest: true, retryCount: 0 },
        { youtubeId: 'legacy-002', version: 1, status: 'completed', isLatest: true, retryCount: 0 },
      ]);

      await runDedupKeyBackfill(app.mongo.db, app.log);

      const rows = await app.mongo.db
        .collection('videoSummaryCache')
        .find({})
        .toArray();

      const byYoutubeId = Object.fromEntries(rows.map((r) => [r.youtubeId, r]));
      expect(byYoutubeId['legacy-001'].dedupKey).toBe(
        computeContentKey({ youtubeId: 'legacy-001', providers: undefined, version: 1 }),
      );
      expect(byYoutubeId['legacy-002'].dedupKey).toBe(
        computeContentKey({ youtubeId: 'legacy-002', providers: undefined, version: 1 }),
      );
    });

    it('should be idempotent — running twice is a no-op', async () => {
      const { runDedupKeyBackfill } = await import('./mongodb.js');

      await app.mongo.db.collection('videoSummaryCache').insertOne({
        youtubeId: 'legacy-idem',
        version: 1,
        status: 'completed',
        isLatest: true,
        retryCount: 0,
      });

      await runDedupKeyBackfill(app.mongo.db, app.log);
      const afterFirst = await app.mongo.db
        .collection('videoSummaryCache')
        .findOne({ youtubeId: 'legacy-idem' });

      await runDedupKeyBackfill(app.mongo.db, app.log);
      const afterSecond = await app.mongo.db
        .collection('videoSummaryCache')
        .findOne({ youtubeId: 'legacy-idem' });

      expect(afterSecond?.dedupKey).toBe(afterFirst?.dedupKey);
    });

    it('should skip rows that already have dedupKey (predicate scoped)', async () => {
      const { runDedupKeyBackfill } = await import('./mongodb.js');

      await app.mongo.db.collection('videoSummaryCache').insertOne({
        youtubeId: 'already-keyed',
        version: 1,
        status: 'completed',
        isLatest: true,
        retryCount: 0,
        dedupKey: 'pre-existing-key-do-not-touch',
      });

      await runDedupKeyBackfill(app.mongo.db, app.log);

      const row = await app.mongo.db
        .collection('videoSummaryCache')
        .findOne({ youtubeId: 'already-keyed' });
      expect(row?.dedupKey).toBe('pre-existing-key-do-not-touch');
    });

    it('should default missing version to 1 (legacy rows from v1.0 with no version field)', async () => {
      const { computeContentKey } = await import('../services/idempotency.service.js');
      const { runDedupKeyBackfill } = await import('./mongodb.js');

      // Some very-legacy rows pre-date the version field entirely.
      await app.mongo.db.collection('videoSummaryCache').insertOne({
        youtubeId: 'no-version',
        status: 'completed',
        isLatest: true,
        retryCount: 0,
      });

      await runDedupKeyBackfill(app.mongo.db, app.log);

      const row = await app.mongo.db
        .collection('videoSummaryCache')
        .findOne({ youtubeId: 'no-version' });
      expect(row?.dedupKey).toBe(
        computeContentKey({ youtubeId: 'no-version', providers: undefined, version: 1 }),
      );
    });
  });

  describe('connection cleanup', () => {
    it('should close connection on app close', async () => {
      const { mongodbPlugin } = await import('./mongodb.js');

      const cleanupApp = Fastify({ logger: false });
      await cleanupApp.register(mongodbPlugin);
      await cleanupApp.ready();

      const client = cleanupApp.mongo.client;

      // App is ready, connection should work
      const pingResult = await client.db().admin().ping();
      expect(pingResult.ok).toBe(1);

      // Close the app
      await cleanupApp.close();

      // After close, operations should fail
      await expect(client.db().admin().ping()).rejects.toThrow();
    });
  });

  describe('database reference', () => {
    let app: FastifyInstance;

    beforeAll(async () => {
      const { mongodbPlugin } = await import('./mongodb.js');

      app = Fastify({ logger: false });
      await app.register(mongodbPlugin);
      await app.ready();
    });

    afterAll(async () => {
      await app.close();
    });

    it('should use the database from connection string', () => {
      // The MongoMemoryServer creates a random DB name
      // Just verify db is accessible
      expect(app.mongo.db.databaseName).toBeDefined();
      expect(typeof app.mongo.db.databaseName).toBe('string');
    });

    it('should allow creating collections', async () => {
      await app.mongo.db.createCollection('test_collection');

      const collections = await app.mongo.db.listCollections({ name: 'test_collection' }).toArray();
      expect(collections.length).toBe(1);

      // Clean up
      await app.mongo.db.dropCollection('test_collection');
    });
  });
});

describe('MongoDB plugin error handling', () => {
  // TODO: Fix plugin timeout issue - plugin initialization times out when
  // creating a fresh MongoMemoryServer. The unique constraint test itself works.
  it.skip('should handle index creation errors gracefully', async () => {
    // Start a fresh in-memory server
    const mongod = await MongoMemoryServer.create();
    const uri = mongod.getUri();

    // Save and override
    const originalUri = process.env.MONGODB_URI;
    process.env.MONGODB_URI = uri;

    try {
      const { mongodbPlugin } = await import('./mongodb.js');

      const app = Fastify({ logger: false });
      await app.register(mongodbPlugin);

      // Create a document that would conflict with unique index
      await app.ready();

      // Insert two documents with same email to test unique constraint
      const usersCollection = app.mongo.db.collection('users');
      await usersCollection.insertOne({ email: 'test@example.com' });

      // Second insert should fail due to unique constraint
      await expect(
        usersCollection.insertOne({ email: 'test@example.com' })
      ).rejects.toThrow();

      await app.close();
    } finally {
      process.env.MONGODB_URI = originalUri;
      await mongod.stop();
    }
  });
});
