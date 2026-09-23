/**
 * Remove the retired free-tier summary expiry.
 *
 * Summaries never expire (decision 2026-09-17). MongoDB's TTL monitor used to
 * hard-delete shared `videoSummaryCache` rows with no cascade, orphaning
 * library rows and Qdrant vectors. The API no longer writes `expiresAt` and
 * drops the TTL index at boot; this script cleans an existing database in
 * one pass.
 *
 * Operations (in this order, so the TTL cannot fire mid-run):
 *   1. Drop the `expiresAt_1` TTL index on videoSummaryCache (if present)
 *   2. $unset `expiresAt` on every videoSummaryCache document that has it
 *
 * Usage (from the repo root; the api workspace has the mongodb driver):
 *   pnpm --filter @vie/api exec tsx ../scripts/migrate-remove-video-expiry.ts            # Run for real
 *   pnpm --filter @vie/api exec tsx ../scripts/migrate-remove-video-expiry.ts --dry-run  # Preview only
 *
 * Idempotent — safe to re-run.
 */

import { MongoClient, MongoServerError } from 'mongodb';
import type { Collection, Document } from 'mongodb';
import { readFileSync, existsSync } from 'node:fs';
import { resolve } from 'node:path';

// Load .env manually (no dotenv dependency needed)
const envPath = resolve(import.meta.dirname ?? '.', '..', '.env');
if (existsSync(envPath)) {
  for (const line of readFileSync(envPath, 'utf-8').split('\n')) {
    const match = line.match(/^\s*([^#=]+?)\s*=\s*(.*?)\s*$/);
    if (match && !process.env[match[1]]) {
      process.env[match[1]] = match[2].replace(/^['"]|['"]$/g, '');
    }
  }
}

const MONGODB_URI = process.env.MONGODB_URI || 'mongodb://localhost:27017';
const DATABASE_NAME = process.env.MONGODB_DATABASE || databaseFromUri(MONGODB_URI) || 'video-insight-engine';
const DRY_RUN = process.argv.includes('--dry-run');
const INDEX_NAME = 'expiresAt_1';

/** The database named in the URI path, if any (`mongodb://host/db?opts`). */
function databaseFromUri(uri: string): string | undefined {
  const match = uri.match(/^mongodb(?:\+srv)?:\/\/[^/]+\/([^?]+)/);
  return match?.[1] || undefined;
}

async function dropExpiryIndex(cache: Collection<Document>): Promise<boolean> {
  const indexes = await cache.indexes().catch((err: unknown) => {
    // NamespaceNotFound: the collection does not exist yet on a fresh database.
    if (err instanceof MongoServerError && err.code === 26) return [];
    throw err;
  });
  const ttl = indexes.find((idx) => idx.name === INDEX_NAME);
  if (!ttl) {
    console.log(`   ⏭️  ${INDEX_NAME} not present`);
    return false;
  }
  if (DRY_RUN) {
    console.log(`   🔍 Would drop ${INDEX_NAME} (expireAfterSeconds: ${String(ttl.expireAfterSeconds)})`);
    return true;
  }
  await cache.dropIndex(INDEX_NAME);
  console.log(`   ✅ Dropped ${INDEX_NAME}`);
  return true;
}

async function unsetExpiresAt(cache: Collection<Document>): Promise<number> {
  const filter = { expiresAt: { $exists: true } };
  const pending = await cache.countDocuments(filter);
  if (pending === 0) {
    console.log('   ⏭️  No documents carry expiresAt');
    return 0;
  }
  if (DRY_RUN) {
    console.log(`   🔍 Would unset expiresAt on ${pending} document(s)`);
    return pending;
  }
  const result = await cache.updateMany(filter, { $unset: { expiresAt: '' } });
  console.log(`   ✅ Unset expiresAt on ${result.modifiedCount} document(s)`);
  return result.modifiedCount;
}

async function main(): Promise<void> {
  console.log(`\n🧹 Remove video expiry${DRY_RUN ? ' (DRY RUN)' : ''}`);
  console.log(`   Database: ${DATABASE_NAME}\n`);

  const client = new MongoClient(MONGODB_URI);
  try {
    await client.connect();
    const cache = client.db(DATABASE_NAME).collection('videoSummaryCache');

    console.log('📊 TTL index');
    const indexDropped = await dropExpiryIndex(cache);

    console.log('\n📊 Documents');
    const docsUnset = await unsetExpiresAt(cache);

    console.log('\n' + '='.repeat(45));
    console.log(`   Index dropped:   ${indexDropped}`);
    console.log(`   Documents unset: ${docsUnset}`);
    console.log('='.repeat(45) + '\n');
  } finally {
    await client.close();
  }
}

main().catch((error: unknown) => {
  console.error('❌ Migration failed:', error);
  process.exit(1);
});
