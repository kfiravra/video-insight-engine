/**
 * Migration Script: Add versioning support to videoSummaryCache
 *
 * Run with: mongosh "mongodb://localhost:27017/video-insight-engine?directConnection=true" scripts/migrate-versioning.js
 *
 * This migration:
 * 1. Adds `version: 1` and `isLatest: true` to existing documents
 * 2. Drops old unique index on youtubeId only
 * 3. Creates new compound unique index on (youtubeId, version)
 *
 * Safe to run multiple times (idempotent).
 */

print('=== Video Insight Engine: Versioning Migration ===\n');

// Step 1: Update existing documents to have version and isLatest fields
print('Step 1: Updating existing documents...');
const updateResult = db.videoSummaryCache.updateMany(
  { version: { $exists: false } },
  { $set: { version: 1, isLatest: true } }
);
print(`  - Updated ${updateResult.modifiedCount} documents with version: 1, isLatest: true`);

// Step 2: Check and drop old index if it exists
print('\nStep 2: Checking for old index...');
const existingIndexes = db.videoSummaryCache.getIndexes();
const oldIndexExists = existingIndexes.some(idx =>
  JSON.stringify(idx.key) === JSON.stringify({ youtubeId: 1 }) && idx.unique === true
);

if (oldIndexExists) {
  print('  - Found old unique index on { youtubeId: 1 }, dropping...');
  try {
    db.videoSummaryCache.dropIndex({ youtubeId: 1 });
    print('  - Old index dropped successfully');
  } catch (e) {
    print(`  - Warning: Could not drop old index: ${e.message}`);
  }
} else {
  print('  - Old index not found (already migrated or never existed)');
}

// Step 3: Create new indexes (idempotent - MongoDB ignores if already exists)
print('\nStep 3: Creating new indexes...');

try {
  db.videoSummaryCache.createIndex(
    { youtubeId: 1, version: 1 },
    { unique: true, name: 'youtubeId_version_unique' }
  );
  print('  - Created unique index: { youtubeId: 1, version: 1 }');
} catch (e) {
  if (e.code === 85) {
    print('  - Index { youtubeId: 1, version: 1 } already exists');
  } else {
    throw e;
  }
}

try {
  db.videoSummaryCache.createIndex(
    { youtubeId: 1, isLatest: 1 },
    { name: 'youtubeId_isLatest_lookup' }
  );
  print('  - Created lookup index: { youtubeId: 1, isLatest: 1 }');
} catch (e) {
  if (e.code === 85) {
    print('  - Index { youtubeId: 1, isLatest: 1 } already exists');
  } else {
    throw e;
  }
}

// Step 4: Verify migration
print('\nStep 4: Verifying migration...');
const docsWithoutVersion = db.videoSummaryCache.countDocuments({ version: { $exists: false } });
const docsWithoutIsLatest = db.videoSummaryCache.countDocuments({ isLatest: { $exists: false } });

if (docsWithoutVersion === 0 && docsWithoutIsLatest === 0) {
  print('  - All documents have version and isLatest fields');
} else {
  print(`  - WARNING: ${docsWithoutVersion} docs without version, ${docsWithoutIsLatest} docs without isLatest`);
}

// Final index listing
print('\nFinal indexes on videoSummaryCache:');
db.videoSummaryCache.getIndexes().forEach(idx => {
  print(`  - ${idx.name}: ${JSON.stringify(idx.key)}${idx.unique ? ' (unique)' : ''}`);
});

print('\n=== Migration Complete ===');
