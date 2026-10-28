/**
 * Memorize Feature Cleanup Script
 *
 * One-time migration to drop dead collections and clean up the folders collection.
 * Safe to run multiple times (idempotent).
 *
 * Usage: npx tsx scripts/cleanup-memorize.ts
 */

import { MongoClient } from 'mongodb';
import * as dotenv from 'dotenv';

dotenv.config();

const MONGODB_URI = process.env.MONGODB_URI || 'mongodb://localhost:27017/?directConnection=true';
const DATABASE_NAME = process.env.MONGODB_DATABASE || 'vie';

const COLLECTIONS_TO_DROP = [
  'memorizedItems',
  'userChats',
  'systemExpansionCache',
];

async function main() {
  console.log('Memorize Feature Cleanup');
  console.log('========================\n');

  const client = new MongoClient(MONGODB_URI);

  try {
    await client.connect();
    const db = client.db(DATABASE_NAME);

    // Drop dead collections
    for (const name of COLLECTIONS_TO_DROP) {
      try {
        await db.collection(name).drop();
        console.log(`Dropped: ${name}`);
      } catch {
        console.log(`Skipped (not found): ${name}`);
      }
    }

    // Remove type field from all folders
    const updateResult = await db.collection('folders').updateMany(
      {},
      { $unset: { type: '' } }
    );
    console.log(`\nRemoved 'type' field from ${updateResult.modifiedCount} folders`);

    // Rebuild folders index without type
    try {
      await db.collection('folders').dropIndex('userId_1_type_1_path_1');
      console.log('Dropped old folders index (userId_1_type_1_path_1)');
    } catch {
      console.log('Old folders index not found (already removed)');
    }

    await db.collection('folders').createIndex({ userId: 1, path: 1 });
    console.log('Created new folders index (userId_1_path_1)');

    console.log('\nDone.');
  } catch (error) {
    console.error('Error:', error);
    process.exit(1);
  } finally {
    await client.close();
  }
}

main();
