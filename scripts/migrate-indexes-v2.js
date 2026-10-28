// MongoDB Index Migration Script v2
// Run with: mongosh "mongodb://localhost:27017/video-insight-engine?directConnection=true" scripts/migrate-indexes-v2.js
//
// This script safely migrates from the old index structure to the new versioning structure.
// It handles:
// 1. Adding version field to existing videoSummaryCache documents
// 2. Dropping old indexes
// 3. Creating new versioned indexes
//
// Safe to run multiple times (idempotent)

print('=== MongoDB Index Migration v2 ===');
print('');

// Step 1: Migrate existing videoSummaryCache documents to have version field
print('Step 1: Migrating existing videoSummaryCache documents...');

const existingWithoutVersion = db.videoSummaryCache.countDocuments({ version: { $exists: false } });
print(`  Found ${existingWithoutVersion} documents without version field`);

if (existingWithoutVersion > 0) {
  const updateResult = db.videoSummaryCache.updateMany(
    { version: { $exists: false } },
    { $set: { version: 1, isLatest: true } }
  );
  print(`  Updated ${updateResult.modifiedCount} documents with version: 1, isLatest: true`);
} else {
  print('  All documents already have version field');
}

// Step 2: Drop old indexes if they exist
print('');
print('Step 2: Dropping old indexes...');

const existingIndexes = db.videoSummaryCache.getIndexes();
const oldYoutubeIdIndex = existingIndexes.find(
  idx => idx.name === 'youtubeId_1' && !idx.key.version
);

if (oldYoutubeIdIndex) {
  try {
    db.videoSummaryCache.dropIndex('youtubeId_1');
    print('  Dropped old youtubeId_1 index');
  } catch (e) {
    print(`  Warning: Could not drop youtubeId_1: ${e.message}`);
  }
} else {
  print('  Old youtubeId_1 index not found (already migrated or never existed)');
}

// Check for old userVideos index
const userVideoIndexes = db.userVideos.getIndexes();
const oldUserVideoIndex = userVideoIndexes.find(
  idx => idx.name === 'userId_1_videoSummaryId_1'
);

if (oldUserVideoIndex) {
  try {
    db.userVideos.dropIndex('userId_1_videoSummaryId_1');
    print('  Dropped old userId_1_videoSummaryId_1 index');
  } catch (e) {
    print(`  Warning: Could not drop userId_1_videoSummaryId_1: ${e.message}`);
  }
} else {
  print('  Old userId_1_videoSummaryId_1 index not found');
}

// Step 3: Create new indexes
print('');
print('Step 3: Creating new indexes...');

// videoSummaryCache indexes
try {
  db.videoSummaryCache.createIndex({ youtubeId: 1, version: 1 }, { unique: true });
  print('  Created: videoSummaryCache.youtubeId_version (unique)');
} catch (e) {
  if (e.code === 85 || e.codeName === 'IndexOptionsConflict') {
    print('  Index videoSummaryCache.youtubeId_version already exists');
  } else {
    print(`  Error creating youtubeId_version index: ${e.message}`);
  }
}

try {
  db.videoSummaryCache.createIndex({ youtubeId: 1, isLatest: 1 });
  print('  Created: videoSummaryCache.youtubeId_isLatest');
} catch (e) {
  if (e.code === 85 || e.codeName === 'IndexOptionsConflict') {
    print('  Index videoSummaryCache.youtubeId_isLatest already exists');
  } else {
    print(`  Error creating youtubeId_isLatest index: ${e.message}`);
  }
}

try {
  db.videoSummaryCache.createIndex({ status: 1 });
  print('  Created: videoSummaryCache.status');
} catch (e) {
  if (e.code === 85 || e.codeName === 'IndexOptionsConflict') {
    print('  Index videoSummaryCache.status already exists');
  } else {
    print(`  Error creating status index: ${e.message}`);
  }
}

// userVideos indexes
try {
  db.userVideos.createIndex({ userId: 1, youtubeId: 1, folderId: 1 }, { unique: true });
  print('  Created: userVideos.userId_youtubeId_folderId (unique)');
} catch (e) {
  if (e.code === 85 || e.codeName === 'IndexOptionsConflict') {
    print('  Index userVideos.userId_youtubeId_folderId already exists');
  } else {
    print(`  Error creating userId_youtubeId_folderId index: ${e.message}`);
  }
}

try {
  db.userVideos.createIndex({ userId: 1, folderId: 1 });
  print('  Created: userVideos.userId_folderId');
} catch (e) {
  if (e.code === 85 || e.codeName === 'IndexOptionsConflict') {
    print('  Index userVideos.userId_folderId already exists');
  } else {
    print(`  Error: ${e.message}`);
  }
}

try {
  db.userVideos.createIndex({ userId: 1, createdAt: -1 });
  print('  Created: userVideos.userId_createdAt');
} catch (e) {
  if (e.code === 85 || e.codeName === 'IndexOptionsConflict') {
    print('  Index userVideos.userId_createdAt already exists');
  } else {
    print(`  Error: ${e.message}`);
  }
}

// Step 4: Verify migration
print('');
print('Step 4: Verifying migration...');

const finalIndexes = db.videoSummaryCache.getIndexes();
print('  videoSummaryCache indexes:');
finalIndexes.forEach(idx => {
  print(`    - ${idx.name}: ${JSON.stringify(idx.key)}`);
});

const finalUserVideoIndexes = db.userVideos.getIndexes();
print('  userVideos indexes:');
finalUserVideoIndexes.forEach(idx => {
  print(`    - ${idx.name}: ${JSON.stringify(idx.key)}`);
});

// Check for any documents still missing version
const stillMissingVersion = db.videoSummaryCache.countDocuments({ version: { $exists: false } });
if (stillMissingVersion > 0) {
  print(`  WARNING: ${stillMissingVersion} documents still missing version field!`);
} else {
  print('  All videoSummaryCache documents have version field');
}

print('');
print('=== Migration Complete ===');
