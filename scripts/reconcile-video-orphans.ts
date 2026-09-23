/**
 * Reconcile video orphans across Mongo, Qdrant and S3.
 *
 * A video "exists" when it has a `videoSummaryCache` row. Anything else that
 * carries its youtube id is an orphan: Qdrant `transcript_chunks` points,
 * S3 `videos/<id>/` prefixes, and `userVideos` rows whose summary is gone.
 * Orphans came from the retired free-tier TTL (summaries deleted with no
 * cascade) and from data wipes that skipped Qdrant.
 *
 * Dry-run by default: prints the orphan table. `--apply` purges each orphan
 * through vie-api's admin endpoint — the same cascade the admin panel uses —
 * so Qdrant/S3/Redis/Mongo cleanup stays in one code path.
 *
 * Usage (from the repo root; the api workspace has the drivers):
 *   pnpm --filter @vie/api exec tsx ../scripts/reconcile-video-orphans.ts           # dry run
 *   pnpm --filter @vie/api exec tsx ../scripts/reconcile-video-orphans.ts --apply   # purge
 *
 * Env (from .env): MONGODB_URI, MONGODB_DATABASE, QDRANT_URL (default
 * http://localhost:6333), S3_BUCKET, AWS_REGION, AWS_ENDPOINT_URL,
 * AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY (optional), API_URL (default
 * http://localhost:3000), ADMIN_API_KEY.
 */

import { MongoClient } from 'mongodb';
import type { Db } from 'mongodb';
import { ListObjectsV2Command, S3Client } from '@aws-sdk/client-s3';
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
const QDRANT_URL = (process.env.QDRANT_URL || 'http://localhost:6333').replace(/\/$/, '');
const API_URL = (process.env.API_URL || 'http://localhost:3000').replace(/\/$/, '');
const ADMIN_API_KEY = process.env.ADMIN_API_KEY ?? '';
// The orphan set comes from MONGODB_URI/QDRANT_URL/S3_BUCKET, but the purge runs on
// whatever stack API_URL points at. Only a loopback API is assumed to be the same stack.
const REMOTE_API_ACKNOWLEDGED = process.argv.includes('--remote-api');
const LOOPBACK_API = /^https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/;
const S3_BUCKET = process.env.S3_BUCKET || '';
const APPLY = process.argv.includes('--apply');
const QDRANT_COLLECTION = 'transcript_chunks';

interface OrphanRow {
  youtubeId: string;
  qdrant: boolean;
  s3: boolean;
  libraryRows: boolean;
}

interface QdrantScrollPage {
  result?: { points?: Array<{ payload?: { video_id?: string } }>; next_page_offset?: unknown };
}

function databaseFromUri(uri: string): string | undefined {
  const match = uri.match(/^mongodb(?:\+srv)?:\/\/[^/]+\/([^?]+)/);
  return match?.[1] || undefined;
}

async function listKnownVideos(db: Db): Promise<Set<string>> {
  const ids = await db.collection('videoSummaryCache').distinct('youtubeId');
  return new Set(ids.filter((id): id is string => typeof id === 'string'));
}

async function listLibraryVideos(db: Db): Promise<Set<string>> {
  const ids = await db.collection('userVideos').distinct('youtubeId');
  return new Set(ids.filter((id): id is string => typeof id === 'string'));
}

async function listQdrantVideos(): Promise<Set<string>> {
  const ids = new Set<string>();
  let offset: unknown = null;
  for (;;) {
    const response = await fetch(`${QDRANT_URL}/collections/${QDRANT_COLLECTION}/points/scroll`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ limit: 1000, with_payload: ['video_id'], with_vector: false, offset }),
    });
    if (!response.ok) throw new Error(`Qdrant scroll failed: ${response.status}`);
    const page = (await response.json()) as QdrantScrollPage;
    for (const point of page.result?.points ?? []) {
      if (point.payload?.video_id) ids.add(point.payload.video_id);
    }
    offset = page.result?.next_page_offset ?? null;
    if (offset === null || offset === undefined) return ids;
  }
}

function s3Client(): S3Client {
  const options: ConstructorParameters<typeof S3Client>[0] = { region: process.env.AWS_REGION };
  if (process.env.AWS_ACCESS_KEY_ID && process.env.AWS_SECRET_ACCESS_KEY) {
    options.credentials = {
      accessKeyId: process.env.AWS_ACCESS_KEY_ID,
      secretAccessKey: process.env.AWS_SECRET_ACCESS_KEY,
    };
  }
  if (process.env.AWS_ENDPOINT_URL) {
    options.endpoint = process.env.AWS_ENDPOINT_URL;
    options.forcePathStyle = true;
  }
  return new S3Client(options);
}

async function listS3Videos(): Promise<Set<string>> {
  const ids = new Set<string>();
  if (!S3_BUCKET) return ids;
  const client = s3Client();
  let token: string | undefined;
  do {
    const page = await client.send(new ListObjectsV2Command({
      Bucket: S3_BUCKET, Prefix: 'videos/', Delimiter: '/', ContinuationToken: token,
    }));
    for (const prefix of page.CommonPrefixes ?? []) {
      const id = prefix.Prefix?.replace(/^videos\//, '').replace(/\/$/, '');
      if (id) ids.add(id);
    }
    token = page.IsTruncated ? page.NextContinuationToken : undefined;
  } while (token);
  return ids;
}

async function listSafely(name: string, list: () => Promise<Set<string>>, warnings: string[]): Promise<Set<string>> {
  try {
    return await list();
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : String(error);
    warnings.push(`${name}: not scanned (${message})`);
    return new Set();
  }
}

function findOrphans(known: Set<string>, qdrant: Set<string>, s3: Set<string>, library: Set<string>): OrphanRow[] {
  const candidates = new Set([...qdrant, ...s3, ...library]);
  return [...candidates]
    .filter((id) => !known.has(id))
    .sort()
    .map((youtubeId) => ({
      youtubeId,
      qdrant: qdrant.has(youtubeId),
      s3: s3.has(youtubeId),
      libraryRows: library.has(youtubeId),
    }));
}

const MAX_RATE_LIMIT_RETRIES = 5;
const DEFAULT_RETRY_AFTER_SECONDS = 30;

function sleep(ms: number): Promise<void> {
  return new Promise((resolveSleep) => setTimeout(resolveSleep, ms));
}

async function purgeOnce(youtubeId: string): Promise<Response> {
  return fetch(`${API_URL}/api/admin/videos/${encodeURIComponent(youtubeId)}`, {
    method: 'DELETE',
    headers: {
      'x-admin-key': ADMIN_API_KEY,
      'x-admin-id': 'reconcile-script',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ reason: 'reconcile-video-orphans' }),
  });
}

/** vie-api rate-limits per IP; a bulk run waits out each 429 instead of failing. */
async function purge(youtubeId: string): Promise<boolean> {
  for (let attempt = 1; attempt <= MAX_RATE_LIMIT_RETRIES; attempt++) {
    const response = await purgeOnce(youtubeId);
    const body = await response.text();
    if (response.status === 429 && attempt < MAX_RATE_LIMIT_RETRIES) {
      const retryAfter = Number(response.headers.get('retry-after')) || DEFAULT_RETRY_AFTER_SECONDS;
      console.log(`   ⏳ ${youtubeId}: rate limited, waiting ${retryAfter}s (attempt ${attempt})`);
      await sleep(retryAfter * 1000);
      continue;
    }
    if (!response.ok) {
      console.log(`   ❌ ${youtubeId}: ${response.status} ${body.slice(0, 200)}`);
      return false;
    }
    const partial = body.includes('"partial":true');
    console.log(`   ${partial ? '⚠️ partial' : '✅'} ${youtubeId}: ${body.slice(0, 200)}`);
    return true;
  }
  return false;
}

function assertApplyIsSafe(): void {
  if (!APPLY) return;
  if (!ADMIN_API_KEY) throw new Error('ADMIN_API_KEY is required for --apply');
  if (!REMOTE_API_ACKNOWLEDGED && !LOOPBACK_API.test(API_URL)) {
    throw new Error(
      `refusing --apply against ${API_URL}; pass --remote-api only if MONGODB_URI, QDRANT_URL and S3_BUCKET belong to that stack`,
    );
  }
}

async function main(): Promise<void> {
  assertApplyIsSafe();
  console.log(`\n🧹 Reconcile video orphans${APPLY ? ' (APPLY)' : ' (DRY RUN)'}`);
  console.log(`   Database: ${DATABASE_NAME}  Qdrant: ${QDRANT_URL}  Bucket: ${S3_BUCKET || '(none)'}\n`);
  const warnings: string[] = [];
  const client = new MongoClient(MONGODB_URI);
  try {
    await client.connect();
    const db = client.db(DATABASE_NAME);
    const known = await listKnownVideos(db);
    const [library, qdrant, s3] = await Promise.all([
      listSafely('mongo userVideos', () => listLibraryVideos(db), warnings),
      listSafely('qdrant', listQdrantVideos, warnings),
      listSafely('s3', listS3Videos, warnings),
    ]);
    const orphans = findOrphans(known, qdrant, s3, library);

    console.log(`   Known videos: ${known.size}  Qdrant: ${qdrant.size}  S3: ${s3.size}  Library: ${library.size}`);
    for (const warning of warnings) console.log(`   ⚠️  ${warning}`);
    if (warnings.length > 0) {
      // A store that was not scanned makes the orphan set incomplete: never purge on it.
      process.exitCode = 1;
      if (APPLY) {
        console.log('   refusing --apply: a store was not scanned');
        return;
      }
    }
    console.log(`\n📋 Orphans: ${orphans.length}`);
    for (const row of orphans) {
      const where = [row.qdrant && 'qdrant', row.s3 && 's3', row.libraryRows && 'library'].filter(Boolean).join(', ');
      console.log(`   ${row.youtubeId}  [${where}]`);
    }
    if (!APPLY || orphans.length === 0) return;

    console.log('\n🚮 Purging through vie-api');
    let failed = 0;
    for (const row of orphans) {
      if (!(await purge(row.youtubeId))) failed += 1;
    }
    console.log(`\n   Purged ${orphans.length - failed}/${orphans.length}`);
    if (failed > 0) process.exitCode = 1;
  } finally {
    await client.close();
  }
}

main().catch((error: unknown) => {
  console.error('❌ Reconcile failed:', error);
  process.exit(1);
});
