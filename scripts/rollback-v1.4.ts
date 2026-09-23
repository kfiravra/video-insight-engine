/**
 * V1.4 Rollback Script
 *
 * Removes indexes added by backfill-v1.4.ts.
 * Does NOT delete data — only drops indexes.
 *
 * Indexes removed:
 *   - videoSummaryCache: shareSlug unique sparse index
 *
 * Usage:
 *   npx tsx scripts/rollback-v1.4.ts              # Run for real
 *   npx tsx scripts/rollback-v1.4.ts --dry-run    # Preview only
 *
 * Idempotent — safe to re-run. Checks if index exists before dropping.
 */

import { MongoClient } from 'mongodb';
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
const DATABASE_NAME = process.env.MONGODB_DATABASE || 'vie';
const DRY_RUN = process.argv.includes('--dry-run');

const INDEXES_TO_DROP = [
  { collection: 'videoSummaryCache', name: 'shareSlug_1' },
];

async function indexExists(
  db: ReturnType<MongoClient['db']>,
  collectionName: string,
  indexName: string,
): Promise<boolean> {
  const indexes = await db.collection(collectionName).indexes();
  return indexes.some((idx) => idx.name === indexName);
}

async function main() {
  console.log('🔄 VIE v1.4 — Rollback (Index Removal)');
  console.log('='.repeat(45));
  if (DRY_RUN) {
    console.log('⚠️  DRY RUN — no changes will be made\n');
  }

  console.log(`📌 MongoDB URI: ${MONGODB_URI.replace(/\/\/[^:]+:[^@]+@/, '//<credentials>@')}`);
  console.log(`📌 Database: ${DATABASE_NAME}\n`);

  const client = new MongoClient(MONGODB_URI);

  try {
    await client.connect();
    console.log('✅ Connected to MongoDB\n');

    const db = client.db(DATABASE_NAME);

    for (const { collection, name } of INDEXES_TO_DROP) {
      const exists = await indexExists(db, collection, name);

      if (!exists) {
        console.log(`⏭️  ${collection}.${name} — index does not exist, skipping`);
        continue;
      }

      if (DRY_RUN) {
        console.log(`🔍 Would drop: ${collection}.${name}`);
      } else {
        await db.collection(collection).dropIndex(name);
        console.log(`✅ Dropped: ${collection}.${name}`);
      }
    }

    console.log('\n' + '='.repeat(45));
    console.log(DRY_RUN ? '\n✅ Dry run complete — no changes made' : '\n✅ Rollback complete!');
  } catch (error) {
    console.error('\n❌ Error:', error);
    process.exit(1);
  } finally {
    await client.close();
    console.log('🔒 Connection closed');
  }
}

main();
