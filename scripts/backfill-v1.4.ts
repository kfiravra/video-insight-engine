/**
 * V1.4 Backfill Migration Script
 *
 * Adds default values to existing documents and creates new indexes
 * for the V1.4 contracts (OutputType, sharing, tiers).
 *
 * Operations:
 *   - videoSummaryCache: set outputType='summary', viewsCount=0, likesCount=0, totalTokens=0
 *   - users: set tier='free'
 *   - Create new indexes (outputType, shareSlug unique sparse)
 *
 * Usage:
 *   npx tsx scripts/backfill-v1.4.ts              # Run for real
 *   npx tsx scripts/backfill-v1.4.ts --dry-run    # Preview only
 *
 * Idempotent — safe to re-run. Uses $exists:false guards so already-set values are untouched.
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

async function main() {
  console.log('🔄 VIE v1.4 — Backfill Migration');
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

    // ── Phase 1: Backfill videoSummaryCache ─────────────────────

    const cacheCollection = db.collection('videoSummaryCache');
    const totalCache = await cacheCollection.countDocuments();
    const needsOutputType = await cacheCollection.countDocuments({ outputType: { $exists: false } });

    console.log(`📊 videoSummaryCache: ${totalCache} total, ${needsOutputType} need backfill`);

    if (!DRY_RUN && needsOutputType > 0) {
      const cacheResult = await cacheCollection.updateMany(
        { outputType: { $exists: false } },
        {
          $set: {
            outputType: 'summary',
            viewsCount: 0,
            likesCount: 0,
            totalTokens: 0,
          },
        },
      );
      console.log(`   ✅ Updated ${cacheResult.modifiedCount} documents`);
    } else if (needsOutputType === 0) {
      console.log('   ⏭️  All documents already have outputType');
    } else {
      console.log(`   🔍 Would update ${needsOutputType} documents`);
    }

    // ── Phase 2: Backfill users ─────────────────────────────────

    const usersCollection = db.collection('users');
    const totalUsers = await usersCollection.countDocuments();
    const needsTier = await usersCollection.countDocuments({ tier: { $exists: false } });

    console.log(`\n📊 users: ${totalUsers} total, ${needsTier} need tier backfill`);

    if (!DRY_RUN && needsTier > 0) {
      const userResult = await usersCollection.updateMany(
        { tier: { $exists: false } },
        { $set: { tier: 'free' } },
      );
      console.log(`   ✅ Updated ${userResult.modifiedCount} documents`);
    } else if (needsTier === 0) {
      console.log('   ⏭️  All users already have tier');
    } else {
      console.log(`   🔍 Would update ${needsTier} documents`);
    }

    // ── Phase 3: Create indexes ─────────────────────────────────

    console.log('\n📊 Creating indexes...');

    if (!DRY_RUN) {
      await cacheCollection.createIndexes([
        { key: { outputType: 1 } },
        { key: { shareSlug: 1 }, unique: true, sparse: true },
      ]);
      console.log('   ✅ videoSummaryCache indexes created (outputType, shareSlug)');
    } else {
      console.log('   🔍 Would create: outputType_1, shareSlug_1 (unique sparse)');
    }

    // ── Summary ─────────────────────────────────────────────────

    console.log('\n' + '='.repeat(45));

    if (!DRY_RUN) {
      const indexes = await cacheCollection.indexes();
      const indexNames = indexes.map((idx) => idx.name).join(', ');
      console.log(`📋 videoSummaryCache indexes: ${indexNames}`);
    }

    console.log(DRY_RUN ? '\n✅ Dry run complete — no changes made' : '\n✅ Migration complete!');
  } catch (error) {
    console.error('\n❌ Error:', error);
    process.exit(1);
  } finally {
    await client.close();
    console.log('🔒 Connection closed');
  }
}

main();
