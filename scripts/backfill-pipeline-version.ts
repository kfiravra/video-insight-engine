/**
 * Backfill script: adds pipelineVersion: "1.0" to all existing videoSummaryCache documents.
 * Run with: npx tsx scripts/backfill-pipeline-version.ts
 */

import { MongoClient } from 'mongodb';

const MONGODB_URI = process.env.MONGODB_URI || 'mongodb://localhost:27017/video-insight-engine?directConnection=true';
const BATCH_SIZE = 500;

async function main() {
  const client = new MongoClient(MONGODB_URI);

  try {
    await client.connect();
    console.log('Connected to MongoDB');

    const db = client.db();
    const collection = db.collection('videoSummaryCache');

    // Count documents without pipelineVersion
    const count = await collection.countDocuments({ pipelineVersion: { $exists: false } });
    console.log(`Found ${count} documents without pipelineVersion`);

    if (count === 0) {
      console.log('Nothing to backfill');
      return;
    }

    // Batch update
    const result = await collection.updateMany(
      { pipelineVersion: { $exists: false } },
      { $set: { pipelineVersion: '1.0' } },
    );

    console.log(`Updated ${result.modifiedCount} documents with pipelineVersion: "1.0"`);

    // Create indexes for v2 queries
    console.log('Creating indexes...');
    await collection.createIndex({ pipelineVersion: 1 });
    await collection.createIndex(
      { youtubeId: 1, pipelineVersion: 1, isLatest: 1 },
      { name: 'youtubeId_pipelineVersion_isLatest' },
    );
    console.log('Indexes created');

    // Verify
    const remaining = await collection.countDocuments({ pipelineVersion: { $exists: false } });
    console.log(`Remaining without pipelineVersion: ${remaining}`);
    console.log('Backfill complete');
  } finally {
    await client.close();
  }
}

main().catch(console.error);
