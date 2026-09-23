import { FastifyInstance } from 'fastify';
import type { FastifyBaseLogger } from 'fastify';
import fp from 'fastify-plugin';
import { MongoClient, Db, ObjectId } from 'mongodb';
import type { Collection, Document } from 'mongodb';
import { config } from '../config.js';
import { computeContentKey } from '../services/idempotency.service.js';

interface CacheRowForBackfill {
  _id: ObjectId;
  youtubeId: string;
  version?: number;
}

/** Minimal shape of a `listIndexes()` row we inspect at startup. */
export interface IndexInfoForMigration {
  name?: string;
  expireAfterSeconds?: number;
}

/**
 * True when the legacy 90-day-TTL variant of the `llm_usage` `createdAt_1`
 * index is still present (pre-2026-07-06 deploys). The plain (non-TTL)
 * `createdAt_1` index that replaces it must NOT match — dropping it on every
 * boot would force a full index rebuild each startup. Exported so the
 * decision logic is regression-testable without a live cluster.
 */
export function hasLegacyLlmUsageTtlIndex(indexes: IndexInfoForMigration[]): boolean {
  return indexes.some(
    (idx) => idx.name === 'createdAt_1' && idx.expireAfterSeconds !== undefined,
  );
}

/**
 * True when the retired free-tier expiry TTL index (`expiresAt_1`) is still
 * present on `videoSummaryCache` (deploys before 2026-09-17). MongoDB's TTL
 * monitor hard-deleted shared cache rows with no cascade, orphaning library
 * rows and vectors. Summaries never expire now, so the index must go — but
 * only when it is really a TTL index, so a boot never rebuilds anything.
 */
export function hasVideoExpiryTtlIndex(indexes: IndexInfoForMigration[]): boolean {
  return indexes.some(
    (idx) => idx.name === 'expiresAt_1' && idx.expireAfterSeconds !== undefined,
  );
}

/**
 * Drop a retired TTL index, but only while it is still a TTL index (an
 * unconditional drop would rebuild the index on every boot). A failure is
 * logged at error level and never disguised as success: a surviving TTL
 * keeps deleting rows silently.
 */
async function dropRetiredTtlIndex(
  collection: Collection<Document>,
  indexName: string,
  isRetired: (indexes: IndexInfoForMigration[]) => boolean,
  log: FastifyBaseLogger,
): Promise<void> {
  // The collection may not exist yet on a fresh database.
  const indexes = await collection.listIndexes().toArray().catch(() => []);
  if (!isRetired(indexes)) return;
  try {
    await collection.dropIndex(indexName);
    log.info({ collection: collection.collectionName, indexName }, 'retired_ttl_index_dropped');
  } catch (err) {
    log.error(
      { err, collection: collection.collectionName, indexName },
      'retired_ttl_index_drop_failed',
    );
  }
}

/**
 * Idempotent one-shot backfill: populate `dedupKey` on legacy
 * `videoSummaryCache` rows that pre-date the content-addressed dedup scheme.
 *
 * After the first successful run the predicate matches zero docs, so the
 * cursor scan on subsequent startups is cheap. At very large collection sizes
 * this should be moved to `scripts/backfill-dedup-keys.ts` and run manually
 * before deploy; current scale (dev / small prod) is fine inline.
 *
 * Legacy rows had no provider override, so `providers: undefined` is the
 * correct canonicalization to match what fresh submits without an override
 * compute today. Per-row E11000 (rare: two pre-existing rows for the same
 * youtubeId+version) is logged-via-counter and skipped rather than aborted —
 * the older row keeps the key, the newer one stays unkeyed and is excluded
 * from the partial unique index.
 *
 * Exported (not just used internally) so tests can drive it deterministically
 * without spinning up a full Fastify app and racing against startup hooks.
 */
export async function runDedupKeyBackfill(db: Db, log: FastifyInstance['log']): Promise<void> {
  const collection = db.collection<CacheRowForBackfill>('videoSummaryCache');
  const cursor = collection.find(
    { dedupKey: { $exists: false } },
    { projection: { _id: 1, youtubeId: 1, version: 1 } },
  );

  const BATCH_SIZE = 500;
  let batch: { updateOne: { filter: { _id: ObjectId }; update: { $set: { dedupKey: string } } } }[] = [];
  let processed = 0;
  let skipped = 0;

  const flush = async () => {
    if (batch.length === 0) return;
    try {
      await collection.bulkWrite(batch, { ordered: false });
      processed += batch.length;
    } catch (err) {
      // BulkWriteError on partial failure — successful writes still commit.
      // Count duplicates and continue; anything else re-throws.
      const bwe = err as { code?: number; writeErrors?: { code: number }[] };
      const dupes = bwe.writeErrors?.filter((e) => e.code === 11000) ?? [];
      skipped += dupes.length;
      processed += batch.length - dupes.length;
      if (dupes.length !== bwe.writeErrors?.length) throw err;
    }
    batch = [];
  };

  for await (const doc of cursor) {
    const dedupKey = computeContentKey({
      youtubeId: doc.youtubeId,
      providers: undefined,
      version: doc.version ?? 1,
    });
    batch.push({
      updateOne: { filter: { _id: doc._id }, update: { $set: { dedupKey } } },
    });
    if (batch.length >= BATCH_SIZE) await flush();
  }
  await flush();

  if (processed > 0 || skipped > 0) {
    log.info({ processed, skipped }, 'videoSummaryCache.dedupKey backfill complete');
  }
}

declare module 'fastify' {
  interface FastifyInstance {
    mongo: {
      client: MongoClient;
      db: Db;
    };
  }
}

async function mongodb(fastify: FastifyInstance) {
  const client = new MongoClient(config.MONGODB_URI);
  await client.connect();

  const db = client.db();

  fastify.decorate('mongo', { client, db });

  // Create indexes on app ready
  fastify.addHook('onReady', async () => {
    try {
      // Retired free-tier expiry: drop the TTL index if it survives from an
      // older deploy (conditional, so a boot never triggers an index rebuild).
      const videoSummaryCache = db.collection('videoSummaryCache');
      await dropRetiredTtlIndex(videoSummaryCache, 'expiresAt_1', hasVideoExpiryTtlIndex, fastify.log);

      // videoSummaryCache indexes
      await videoSummaryCache.createIndexes([
        { key: { youtubeId: 1, isLatest: 1 } },
        { key: { youtubeId: 1, version: -1 } },
        { key: { status: 1 } },
        { key: { outputType: 1 } },
        { key: { shareSlug: 1 }, unique: true, sparse: true },
        { key: { language: 1 }, sparse: true },
        // Content-addressed dedup key — partial-on-$exists so legacy rows
        // without the field don't trip the unique constraint. The cross-user
        // single-flight in video.service.ts.createVideo relies on this index
        // for atomic upsert serialization.
        {
          key: { dedupKey: 1 },
          unique: true,
          partialFilterExpression: { dedupKey: { $exists: true } },
        },
      ]);

      // userVideos indexes
      await db.collection('userVideos').createIndexes([
        { key: { userId: 1, videoSummaryId: 1 } },
        { key: { userId: 1, folderId: 1 } },
        { key: { userId: 1, youtubeId: 1, folderId: 1 } },
        { key: { userId: 1, createdAt: -1 } },
        { key: { userId: 1, 'playlistInfo.playlistId': 1 } },
        // Global video purge deletes every user's rows for one video
        { key: { youtubeId: 1 } },
      ]);

      // folders indexes
      await db.collection('folders').createIndexes([
        { key: { userId: 1, path: 1 } },
        { key: { userId: 1, parentId: 1 } },
      ]);

      // users indexes
      await db.collection('users').createIndexes([
        { key: { email: 1 }, unique: true },
        { key: { tier: 1 } },
        // GDPR scheduler scans for `hardDeleteAt <= now` among soft-deleted
        // accounts. Sparse on hardDeleteAt keeps the index tiny.
        { key: { hardDeleteAt: 1 }, sparse: true },
      ]);

      // videoDeletions audit — one row per global video purge
      await db.collection('videoDeletions').createIndexes([
        { key: { youtubeId: 1 } },
        { key: { completedAt: -1 } },
      ]);

      // userDeletions audit — read by email-hash lookup and by original user
      // ID. Retained indefinitely (no TTL).
      await db.collection('userDeletions').createIndexes([
        { key: { originalUserId: 1 } },
        { key: { emailHash: 1 } },
        { key: { completedAt: -1 } },
      ]);

      // shareLikes indexes
      await db.collection('shareLikes').createIndexes([
        { key: { shareSlug: 1, ipHash: 1 }, unique: true },
      ]);

      // llm_usage is the financial ledger — rows are kept forever (decision
      // 2026-07-06, docs/llm-cost-model.md). A 90-day TTL used to silently
      // erase billing history; drop it if it survives from an older deploy.
      // Drop ONLY when the TTL option is actually present — an unconditional
      // drop would rebuild the ledger index on every boot (expensive as the
      // collection grows, and cost queries scan unindexed in the gap).
      const llmUsage = db.collection('llm_usage');
      await dropRetiredTtlIndex(llmUsage, 'createdAt_1', hasLegacyLlmUsageTtlIndex, fastify.log);
      await llmUsage.createIndexes([
        { key: { createdAt: 1 } },
      ]);

      // userCosts indexes — per-user daily cost aggregates
      await db.collection('userCosts').createIndexes([
        { key: { userId: 1, date: 1 }, unique: true },
        { key: { date: 1 } },
      ]);

      // userCostAdjustments — admin grant-credit audit log
      await db.collection('userCostAdjustments').createIndexes([
        { key: { userId: 1, createdAt: -1 } },
        { key: { adminId: 1, createdAt: -1 } },
      ]);

      // idempotencyKeys — request-level dedup for POST /api/videos.
      // The TTL index (expireAfterSeconds: 0) treats `expiresAt` as the
      // absolute eviction deadline; Mongo reaps lazily (~60s precision) which
      // is fine for dedup semantics. The videoSummaryId index is sparse
      // because pending placeholders carry no videoSummaryId until the
      // pipeline completes.
      await db.collection('idempotencyKeys').createIndexes([
        { key: { hash: 1 }, unique: true },
        { key: { expiresAt: 1 }, expireAfterSeconds: 0 },
        { key: { videoSummaryId: 1 }, sparse: true },
        // User-scope delete invalidates by library row; global purge by video
        { key: { userVideoId: 1 }, sparse: true },
        { key: { youtubeId: 1 } },
        { key: { userId: 1, createdAt: -1 } },
      ]);

      fastify.log.info('MongoDB indexes created');
    } catch (err) {
      // Most index-creation errors are benign re-runs ("already exists with
      // matching options"), so we keep the catch broad to avoid noisy startup
      // failures. The dedupKey partial-unique index is correctness-critical
      // for cross-user single-flight (video.service.ts.createVideo relies on
      // it) — a build failure there means duplicate cache rows could appear.
      // We surface that case at ERROR level and, in production, propagate the
      // failure so an orchestrator restart can be triggered.
      const message = err instanceof Error ? err.message : String(err);
      const isDedupKeyFailure = /dedupKey/i.test(message);
      if (isDedupKeyFailure) {
        fastify.log.error(
          { err },
          'dedupKey partial-unique index failed to build — cross-user dedup may not be enforced',
        );
        if (config.NODE_ENV === 'production') throw err;
      } else {
        fastify.log.warn({ err }, 'Some indexes may already exist or failed to create');
      }
    }

    // Run the one-shot dedupKey backfill OFF the ready path. Kicking it via
    // setImmediate means: (1) Fastify's `ready` resolves quickly — health
    // checks stay green during what could be a multi-minute scan; (2) the
    // partial-unique index is already in place, so the bulkWrite's existing
    // per-row E11000 swallow handles any legitimate legacy duplicate pairs
    // (older row keeps the key, newer one stays unkeyed and excluded by the
    // partial filter); (3) the backfill failing is non-fatal — the next
    // submit for an unkeyed row will still write a key on insert.
    if (config.NODE_ENV !== 'test' && config.RUN_DEDUP_BACKFILL) {
      setImmediate(() => {
        runDedupKeyBackfill(db, fastify.log).catch((err) => {
          fastify.log.error({ err }, 'dedupKey backfill failed (non-fatal — readiness unaffected)');
        });
      });
    }
  });

  fastify.addHook('onClose', async () => {
    await client.close();
  });

  fastify.log.info('MongoDB connected');
}

export const mongodbPlugin = fp(mongodb);
