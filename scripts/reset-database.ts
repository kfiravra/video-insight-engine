/**
 * Database Reset Script
 *
 * Resets video-related collections while preserving user data.
 * Run after the section → chapter refactoring to ensure clean data.
 *
 * Usage: npx tsx scripts/reset-database.ts
 *
 * Collections dropped:
 * - videoSummaryCache: Video summaries (will be regenerated)
 * - userVideos: User video associations
 *
 * Collections preserved:
 * - users: User accounts and settings
 * - folders: User folder organization
 */

import { MongoClient } from 'mongodb';
import * as dotenv from 'dotenv';

// Load environment variables
dotenv.config();

const MONGODB_URI = process.env.MONGODB_URI || 'mongodb://localhost:27017/?directConnection=true';
const DATABASE_NAME = process.env.MONGODB_DATABASE || 'vie';

// Collections to drop (video/content related)
const COLLECTIONS_TO_DROP = [
  'videoSummaryCache',
  'userVideos',
];

// Collections to preserve
const COLLECTIONS_TO_PRESERVE = ['users', 'folders'];

async function main() {
  console.log('🔄 Video Insight Engine - Database Reset');
  console.log('=========================================\n');

  console.log(`📌 MongoDB URI: ${MONGODB_URI.replace(/\/\/[^:]+:[^@]+@/, '//<credentials>@')}`);
  console.log(`📌 Database: ${DATABASE_NAME}\n`);

  const client = new MongoClient(MONGODB_URI);

  try {
    await client.connect();
    console.log('✅ Connected to MongoDB\n');

    const db = client.db(DATABASE_NAME);

    // Show current collection stats
    console.log('📊 Current collection counts:');
    const collections = await db.listCollections().toArray();
    for (const col of collections) {
      const count = await db.collection(col.name).countDocuments();
      const status = COLLECTIONS_TO_DROP.includes(col.name)
        ? '🗑️  (will be dropped)'
        : COLLECTIONS_TO_PRESERVE.includes(col.name)
          ? '✅ (preserved)'
          : '❓ (unknown)';
      console.log(`   ${col.name}: ${count} documents ${status}`);
    }

    console.log('\n⚠️  This will drop the following collections:');
    for (const name of COLLECTIONS_TO_DROP) {
      console.log(`   - ${name}`);
    }
    console.log(`\n✅ The following collections will be preserved:`);
    for (const name of COLLECTIONS_TO_PRESERVE) {
      console.log(`   - ${name}`);
    }

    // Prompt for confirmation (if running interactively)
    if (process.stdin.isTTY) {
      console.log('\n❓ Press Enter to continue or Ctrl+C to abort...');
      await new Promise<void>((resolve) => {
        process.stdin.once('data', () => resolve());
      });
    }

    // Drop collections
    console.log('\n🔄 Dropping collections...');
    for (const name of COLLECTIONS_TO_DROP) {
      try {
        const exists = collections.some((c) => c.name === name);
        if (exists) {
          await db.collection(name).drop();
          console.log(`   ✅ Dropped: ${name}`);
        } else {
          console.log(`   ⏭️  Skipped (not found): ${name}`);
        }
      } catch (error) {
        console.error(`   ❌ Error dropping ${name}:`, error);
      }
    }

    // Show final state
    console.log('\n📊 Final collection counts:');
    const finalCollections = await db.listCollections().toArray();
    for (const col of finalCollections) {
      const count = await db.collection(col.name).countDocuments();
      console.log(`   ${col.name}: ${count} documents`);
    }

    console.log('\n✅ Database reset complete!');
    console.log('\n📝 Next steps:');
    console.log('   1. Restart your services: docker-compose restart');
    console.log('   2. Re-summarize videos to populate new chapter-based data');

  } catch (error) {
    console.error('\n❌ Error:', error);
    process.exit(1);
  } finally {
    await client.close();
    console.log('\n🔒 Connection closed');
  }
}

main();
