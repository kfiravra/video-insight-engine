import { Db, ObjectId, Collection, type Filter, type UpdateFilter } from 'mongodb';
import { DatabaseError } from '../utils/errors.js';
import { buildSynthesisMerge } from '../utils/synthesis-merge.js';
import type { SynthesisFields } from '../schemas/synthesis-event.schema.js';

/**
 * Transcript-phase provenance, written ONCE per run by the summarizer's
 * pipeline runner right after the transcript+frames phase — for successful
 * AND failed runs (failed rows carry `outcome: 'failed'` + `errorCode`).
 * Mongo-only diagnostics: never copied into Redis payloads or frontend/share
 * responses. Absent on rows completed via the Redis response-cache fast path
 * (a completed row with no `transcriptMeta` and no `pipelineVersion` was
 * Redis-served). The API only reads it; the summarizer owns every write.
 */
export interface TranscriptMeta {
  outcome: 'ok' | 'failed';
  /** Layer that produced the transcript; null on failure. */
  source: 's3' | 'ytdlp' | 'api' | 'proxy' | 'whisper' | 'gemini' | 'metadata' | null;
  type: 'manual' | 'auto-generated' | 'asr' | 'metadata' | 'cached' | null;
  /** s3 rows only: the layer that originally produced the cached blob; null if unknown. */
  origin: 'ytdlp' | 'api' | 'proxy' | 'whisper' | 'gemini' | 'metadata' | null;
  /** Caption track picked by THIS run's metadata phase; null = no usable json3 track. */
  captionTrack: 'manual' | 'auto-generated' | null;
  /** Matched caption key (e.g. "ar-SA", "en-en") — differs from the video
   *  language when YouTube auto-translated. */
  captionLang: string | null;
  /** "http_429" | "http_403" | "http_<n>" | "request" | "parse" | "empty" | null. */
  captionFetchError: string | null;
  /** youtube-transcript-api skipped because of the Redis caption-429 marker. */
  captionApiSkipped: boolean;
  /** Layers that RAN and FAILED before `source`, in order. */
  attempted: string[];
  segments: number | null;
  chars: number | null;
  /** Transcript-phase wall time (frames run in parallel); present on failed rows too. */
  fetchWallMs: number | null;
  /** Failed rows: the TranscriptError code (e.g. NO_TRANSCRIPT, RATE_LIMITED). */
  errorCode: string | null;
}

export interface VideoSummaryCacheDocument {
  _id: ObjectId;
  youtubeId: string;
  url: string;
  status: 'pending' | 'processing' | 'completed' | 'failed';
  title?: string;
  channel?: string;
  duration?: number;
  thumbnailUrl?: string;
  summary?: unknown;
  chapters?: unknown;
  chapterSource?: string;
  descriptionAnalysis?: unknown;
  context?: unknown;
  outputType?: string;
  version: number;
  /** The served version: the newest completed user-pool row. A re-run takes
   *  it only on completion (promoteCompletedVersion, 1d.8). */
  isLatest: boolean;
  /** Content-addressed dedup key — SHA-256 of (youtubeId, PIPELINE_VERSION,
   *  providers, version). Used by `upsertCacheByDedupKey` to collapse
   *  cross-user submissions for the same content onto a single cache row.
   *  Legacy rows pre-Step-1 lack this field; the unique index is partial on
   *  `{$exists: true}` so they coexist until the backfill catches them. */
  dedupKey?: string;
  /** Set on bypassCache version bumps — consumed by the summarizer, which
   *  skips its youtubeId-keyed response cache when the flag is present.
   *  Cleared by an explicit $unset in the summarizer's final save
   *  (mongodb_repository.py) — that save is a $set merge, NOT a document
   *  replace, so the unset is load-bearing. */
  forceRefresh?: boolean;
  /** D25: a version produced by the eval user (`users.isEvalUser`). Set once
   *  at insert, never cleared. Eval rows are never `isLatest`, never hold the
   *  shared version-1 dedupKey other users attach to, are hidden from other
   *  users' version lists, and are pruned only among themselves. */
  evalRun?: boolean;
  /** 1a.7: a benchmark run (admin/eval `cold: true`) — the summarizer skips
   *  its S3 transcript cache and scene-frame manifest so the run measures cold
   *  media (fresh results are still written). Cleared with `forceRefresh` in
   *  the summarizer's final save, so a later re-run of the row is warm. */
  coldMedia?: boolean;
  /** Who dispatched the row's latest run: the user's id as a string (the
   *  summarizer's `POST /summarize` stamp and `pipeline_runner` read strings)
   *  and the originating request id. Written by `stampDispatchContext` on every
   *  dispatch; read by the summarizer to attribute `llm_usage` when an SSE
   *  request runs the pipeline; `$unset` by the GDPR cascade. */
  userId?: string;
  requestId?: string;
  /** The minutes ledger's run id for this dispatch (server-generated per
   *  submission, P3.1): the terminal status settles / releases the hold keyed
   *  by it, while `requestId` keeps naming the run's llm_usage rows. */
  billingRunId?: string;
  retryCount: number;
  errorCode?: string;
  errorMessage?: string;
  /** Timestamp of the most recent successful dispatch-guard release. Set by
   *  `tryClaimDispatchRelease` so duplicate FAILED events (from summarizer
   *  status-callback retries) become a no-op instead of racing to blind-DEL a
   *  freshly-acquired lock. Not user-facing. */
  dispatchGuardReleasedAt?: Date;
  processedAt?: Date;
  processingTimeMs?: number;
  createdAt: Date;
  updatedAt: Date;
  // Share fields (V1.4)
  shareSlug?: string;
  sharedAt?: Date;
  viewsCount?: number;
  likesCount?: number;
  // Pipeline output fields
  // Canonical version stamp written by the summarizer at pipeline write time
  // (packages/shared/src/config/pipeline-version.json). Absent on docs that
  // predate stamping — those are treated as current-legacy and served as-is;
  // a mismatching stamp offers the user an update (library-update.ts isStaleVersion).
  pipelineVersion?: string;
  // Transcript provenance stamped by the summarizer per run (see TranscriptMeta).
  // Absent on Redis-served rows and on rows predating the field; the API never
  // writes or forwards it.
  transcriptMeta?: TranscriptMeta;
  intent?: unknown;
  triage?: unknown;
  output?: unknown;
  enrichment?: unknown;
  synthesis?: unknown;
  // New clean shape (v3)
  creator?: string;
  meta?: unknown;
  tabs?: unknown[];
  pipeline?: unknown;
  // Legacy v2 (backward compat reads)
  assembledMeta?: unknown;
  assembledTabs?: unknown[];
}

export interface UserVideoDocument {
  _id: ObjectId;
  userId: ObjectId;
  videoSummaryId: ObjectId;
  youtubeId: string;
  title?: string;
  channel?: string;
  duration?: number;
  thumbnailUrl?: string;
  status: string;
  folderId: ObjectId | null;
  /** D-LIB: the completed copy an update left, while the row it moved to can still fail (library-update.repository.ts). */
  previousVideoSummaryId?: ObjectId;
  playlistInfo?: {
    playlistId: string;
    playlistTitle: string;
    position: number;
    totalVideos: number;
  };
  addedAt: Date;
  createdAt: Date;
  updatedAt: Date;
}

export interface CreateVideoSummaryData {
  youtubeId: string;
  url: string;
  status: 'pending' | 'processing' | 'completed' | 'failed';
  version: number;
  isLatest: boolean;
  retryCount: number;
  /** Optional on the input shape so legacy callers compile during migration.
   *  Required for `upsertCacheByDedupKey` (enforced at the method signature). */
  dedupKey?: string;
  /** Set on bypassCache version bumps — tells the summarizer to skip its
   *  youtubeId-keyed response cache so the fresh row gets a real pipeline run
   *  regardless of which producer (worker or SSE client) wins the lock. */
  forceRefresh?: boolean;
  /** Eval-user version — see `VideoSummaryCacheDocument.evalRun`. */
  evalRun?: boolean;
  /** Cold-media benchmark run — see `VideoSummaryCacheDocument.coldMedia`. */
  coldMedia?: boolean;
}

/** Which version pool a prune works on — eval rows and user rows never prune each other. */
export interface VersionPool {
  evalRun: boolean;
  /** Newest versions of the pool to keep. */
  keep: number;
}

/** Mongo filter for one version pool (D25): eval rows, or every other row. */
function versionPoolFilter(evalRun: boolean): Filter<VideoSummaryCacheDocument> {
  return evalRun ? { evalRun: true } : { evalRun: { $ne: true } };
}

export interface CreateUserVideoData {
  userId: string;
  videoSummaryId: string;
  youtubeId: string;
  title?: string;
  channel?: string;
  duration?: number;
  thumbnailUrl?: string;
  status: string;
  folderId?: string | null;
}

export class VideoRepository {
  private readonly cacheCollection: Collection<VideoSummaryCacheDocument>;
  private readonly userVideosCollection: Collection<UserVideoDocument>;

  constructor(db: Db) {
    this.cacheCollection = db.collection('videoSummaryCache');
    this.userVideosCollection = db.collection('userVideos');
  }

  // Video Summary Cache methods

  async findCacheByYoutubeId(youtubeId: string, latestOnly = true): Promise<VideoSummaryCacheDocument | null> {
    const query: Record<string, unknown> = { youtubeId };
    if (latestOnly) {
      query.isLatest = true;
    }
    return this.cacheCollection.findOne(query);
  }

  async findCacheById(id: string): Promise<VideoSummaryCacheDocument | null> {
    return this.cacheCollection.findOne({ _id: new ObjectId(id) });
  }

  async createCacheEntry(data: CreateVideoSummaryData): Promise<VideoSummaryCacheDocument> {
    const doc: Omit<VideoSummaryCacheDocument, '_id'> = {
      ...data,
      createdAt: new Date(),
      updatedAt: new Date(),
    };
    const result = await this.cacheCollection.insertOne(doc as VideoSummaryCacheDocument);
    return { ...doc, _id: result.insertedId } as VideoSummaryCacheDocument;
  }

  /**
   * Atomic content-addressed cache row upsert. Concurrent callers with the
   * same `dedupKey` collapse onto a single row via the partial unique index
   * on `videoSummaryCache.dedupKey`; exactly one caller sees `wasInsert: true`
   * and is responsible for dispatching the pipeline. The rest get the existing
   * row back (`wasInsert: false`) and attach to its in-flight or completed
   * state.
   *
   * Uses `$setOnInsert` so a late submitter cannot stomp the original row's
   * `url`, `status`, or any other field — the row's state is owned by the
   * original winner and the in-progress pipeline.
   */
  /** The shared row for a dedupKey, read-only (the serve-only path must not create one). */
  async findCacheByDedupKey(dedupKey: string): Promise<VideoSummaryCacheDocument | null> {
    return this.cacheCollection.findOne({ dedupKey });
  }

  async upsertCacheByDedupKey(
    data: CreateVideoSummaryData & { dedupKey: string },
  ): Promise<{ doc: VideoSummaryCacheDocument; wasInsert: boolean }> {
    const now = new Date();
    const insertDoc: Omit<VideoSummaryCacheDocument, '_id'> = {
      ...data,
      createdAt: now,
      updatedAt: now,
    };

    const result = await this.cacheCollection.findOneAndUpdate(
      { dedupKey: data.dedupKey },
      { $setOnInsert: insertDoc },
      {
        upsert: true,
        returnDocument: 'after',
        includeResultMetadata: true,
      },
    );

    // With upsert: true + returnDocument: 'after', the driver always returns
    // a doc. `lastErrorObject.updatedExisting` discriminates: false on insert,
    // true on attach. `upserted` (an ObjectId when inserted) is the secondary
    // signal — we prefer `updatedExisting` because it's typed more explicitly.
    const wasInsert = result.lastErrorObject?.updatedExisting === false;
    if (!result.value) {
      // Should be unreachable with upsert: true + returnDocument: 'after',
      // but the driver types mark `value` optional so we guard explicitly.
      // DatabaseError → mapped to a 500 by the global error handler, not a
      // raw Error which leaks "Error" as the name in logs / Sentry breadcrumbs.
      throw new DatabaseError('upsertCacheByDedupKey: driver returned no document despite upsert');
    }
    return { doc: result.value, wasInsert };
  }

  async updateCacheEntry(id: string, updates: Partial<VideoSummaryCacheDocument>): Promise<void> {
    await this.cacheCollection.updateOne(
      { _id: new ObjectId(id) },
      { $set: { ...updates, updatedAt: new Date() } }
    );
  }

  /**
   * A user-pool version takes `isLatest` only once it completes (1d.8), so a
   * failed or still-running re-run never displaces the served version. Set
   * self, demote older latest rows, then yield to a newer latest row: any
   * interleaving of concurrent completions converges on the newest one, and an
   * older run finishing late never displaces it. Eval rows are never promoted.
   */
  async promoteCompletedVersion(id: string): Promise<boolean> {
    const row = await this.cacheCollection.findOne(
      { _id: new ObjectId(id), status: 'completed', isLatest: { $ne: true }, ...versionPoolFilter(false) },
      { projection: { youtubeId: 1, version: 1 } },
    );
    if (!row) return false;
    const version = row.version || 1;
    const updatedAt = new Date();
    await this.cacheCollection.updateOne({ _id: row._id }, { $set: { isLatest: true, updatedAt } });
    await this.cacheCollection.updateMany(
      { youtubeId: row.youtubeId, isLatest: true, version: { $lt: version } },
      { $set: { isLatest: false, updatedAt } },
    );
    const newer = await this.cacheCollection.findOne({ youtubeId: row.youtubeId, isLatest: true, version: { $gt: version } });
    if (!newer) return true;
    await this.cacheCollection.updateOne({ _id: row._id }, { $set: { isLatest: false, updatedAt } });
    return false;
  }

  /**
   * A failed user-pool re-run hands its library entries back to the completed
   * version still being served (1d.8). No-op for eval rows, for rows that are
   * no longer failed (a retry already re-dispatched them) and when no
   * completed version exists (a first run that failed stays as it is).
   */
  async restoreServedVersion(failedId: string): Promise<boolean> {
    const failed = await this.cacheCollection.findOne(
      { _id: new ObjectId(failedId), status: 'failed', isLatest: { $ne: true }, ...versionPoolFilter(false) },
      { projection: { youtubeId: 1 } },
    );
    if (!failed) return false;
    const served = await this.cacheCollection.findOne({ youtubeId: failed.youtubeId, isLatest: true, status: 'completed' });
    if (!served) return false;
    await this.userVideosCollection.updateMany(
      { videoSummaryId: failed._id },
      { $set: { videoSummaryId: served._id, status: 'completed', updatedAt: new Date() } },
    );
    return true;
  }

  /** Highest-numbered row; `evalRun` narrows it to one version pool (D25), omitted = every row. */
  async findHighestVersion(youtubeId: string, evalRun?: boolean): Promise<VideoSummaryCacheDocument | null> {
    const filter = evalRun === undefined ? { youtubeId } : { youtubeId, ...versionPoolFilter(evalRun) };
    return this.cacheCollection.findOne(
      filter,
      { sort: { version: -1 }, projection: { version: 1 } }
    );
  }

  async incrementRetryCount(id: string): Promise<void> {
    await this.cacheCollection.updateOne(
      { _id: new ObjectId(id) },
      {
        // Bumping retryCount means a fresh dispatch is about to run, so
        // reset the release marker — a future FAILED for THIS run should be
        // free to claim release. The `$unset` is harmless on rows that have
        // never set the field.
        $set: { status: 'pending', updatedAt: new Date() },
        $unset: { dispatchGuardReleasedAt: '' },
        $inc: { retryCount: 1 },
      }
    );
  }

  /**
   * A stalled row was re-dispatched (createVideo's stall branch): the new run
   * owns it, so it reads `pending` with a fresh `updatedAt` — otherwise the
   * summarizer's stall sweeper would fail it as the dead run's and the new
   * run's hold with it (I2). Like `incrementRetryCount`, it also clears the
   * guard-release marker so a FAILED for THIS run can still release.
   */
  async markRedispatched(id: string): Promise<void> {
    await this.cacheCollection.updateOne(
      { _id: new ObjectId(id) },
      { $set: { status: 'pending', updatedAt: new Date() }, $unset: { dispatchGuardReleasedAt: '' } },
    );
  }

  /**
   * Atomically claim the right to release the dispatch guard for a FAILED
   * cache row. Returns `true` exactly once per terminal-failure transition —
   * subsequent duplicate FAILED events from summarizer status-callback
   * retries see `false` and correctly skip releasing the Redis lock.
   *
   * Two gates:
   *   1. `status: 'failed'` — if a user-driven retry has already flipped the
   *      row back to `pending`/`processing`, the stale FAILED event is a
   *      no-op. Without this, a late-arriving FAILED could clear a fresh
   *      dispatch's Redis lock (a duplicate-event manifestation of the
   *      retry-between-events race).
   *   2. `$expr: lt(dispatchGuardReleasedAt, updatedAt)` — the most recent
   *      `updatedAt` change came from the summarizer writing `status=failed`
   *      (the trigger for this FAILED event). If we already marked released
   *      AFTER that updatedAt, this is a duplicate event from the same
   *      failure. `dispatchGuardReleasedAt` missing OR strictly older than
   *      `updatedAt` ⇒ first-event semantics.
   *
   * Residual race (irreducible without summarizer-side cooperation): between
   * THIS update committing and the caller's Redis release running,
   * `createVideo`'s failed-retry branch could acquire a new lock. The
   * subsequent release would then wipe the new lock. The summarizer's per-
   * `video_summary_id` pipeline lock at
   * `services/summarizer/src/services/cache/pipeline_event_stream.py` is the
   * documented last line of defense for that microsecond window.
   */
  async tryClaimDispatchRelease(id: string): Promise<boolean> {
    const result = await this.cacheCollection.findOneAndUpdate(
      {
        _id: new ObjectId(id),
        status: 'failed',
        $or: [
          { dispatchGuardReleasedAt: { $exists: false } },
          { $expr: { $lt: ['$dispatchGuardReleasedAt', '$updatedAt'] } },
        ],
      },
      { $set: { dispatchGuardReleasedAt: new Date() } },
      { returnDocument: 'after', projection: { _id: 1 } },
    );
    return !!result;
  }

  /** Version metadata, newest first. Eval rows only when `includeEval` (D25). */
  async getVersions(
    youtubeId: string,
    limit: number,
    options: { includeEval: boolean },
  ): Promise<VideoSummaryCacheDocument[]> {
    const filter = options.includeEval ? { youtubeId } : { youtubeId, ...versionPoolFilter(false) };
    return this.cacheCollection
      .find(filter)
      .project({
        _id: 1,
        youtubeId: 1,
        version: 1,
        isLatest: 1,
        status: 1,
        title: 1,
        channel: 1,
        duration: 1,
        thumbnailUrl: 1,
        createdAt: 1,
        processedAt: 1,
        processingTimeMs: 1,
        errorCode: 1,
        errorMessage: 1,
      })
      .sort({ version: -1 })
      .limit(limit)
      .toArray() as Promise<VideoSummaryCacheDocument[]>;
  }

  /**
   * Keep the `keep` newest versions of ONE pool (eval rows, or every other
   * row) and delete the rest of that pool; the other pool is never touched
   * (D25). Ranked by version, not by arithmetic on the new version number:
   * eval versions share the numbering, so a pool's versions can have gaps.
   * Returns the number of rows deleted.
   */
  async pruneVersions(youtubeId: string, pool: VersionPool): Promise<number> {
    const filter = { youtubeId, ...versionPoolFilter(pool.evalRun) };
    // One video's version rows — a handful, so skip() costs nothing here. The
    // served row survives even when newer re-runs (failed or running) outnumber
    // `keep` (1d.8).
    const beyondKeep = await this.cacheCollection
      .find(filter)
      .sort({ version: -1, createdAt: -1 })
      .skip(pool.keep)
      .project<{ _id: ObjectId; isLatest?: boolean }>({ _id: 1, isLatest: 1 })
      .toArray();
    const toDelete = beyondKeep.filter(v => !v.isLatest);
    if (toDelete.length === 0) return 0;

    const result = await this.cacheCollection.deleteMany({ _id: { $in: toDelete.map(v => v._id) } });
    return result.deletedCount;
  }

  /**
   * The video's length in seconds from any completed version, or null. A
   * submit for a video already summarised reads it here instead of asking the
   * summarizer's metadata pre-flight (I10): the length never changes.
   */
  async findKnownDuration(youtubeId: string): Promise<number | null> {
    const row = await this.cacheCollection.findOne(
      { youtubeId, status: 'completed', duration: { $gt: 0 } },
      { projection: { duration: 1 } },
    );
    return row?.duration ?? null;
  }

  /** Every version's id and share slug for a video — the global purge's work list. */
  async findCacheKeysByYoutubeId(youtubeId: string): Promise<Array<{ _id: ObjectId; shareSlug?: string }>> {
    return this.cacheCollection
      .find({ youtubeId })
      .project<{ _id: ObjectId; shareSlug?: string }>({ _id: 1, shareSlug: 1 })
      .toArray();
  }

  // User Videos methods

  async findUserVideo(userId: string, videoId: string): Promise<UserVideoDocument | null> {
    return this.userVideosCollection.findOne({
      _id: new ObjectId(videoId),
      userId: new ObjectId(userId),
    });
  }

  async findUserVideoByYoutubeId(userId: string, youtubeId: string, folderId?: string | null): Promise<UserVideoDocument | null> {
    const query: Record<string, unknown> = {
      userId: new ObjectId(userId),
      youtubeId,
    };
    if (folderId !== undefined) {
      query.folderId = folderId ? new ObjectId(folderId) : null;
    }
    return this.userVideosCollection.findOne(query);
  }

  async createUserVideo(data: CreateUserVideoData): Promise<UserVideoDocument> {
    const doc: Omit<UserVideoDocument, '_id'> = {
      userId: new ObjectId(data.userId),
      videoSummaryId: new ObjectId(data.videoSummaryId),
      youtubeId: data.youtubeId,
      title: data.title,
      channel: data.channel,
      duration: data.duration,
      thumbnailUrl: data.thumbnailUrl,
      status: data.status,
      folderId: data.folderId ? new ObjectId(data.folderId) : null,
      addedAt: new Date(),
      createdAt: new Date(),
      updatedAt: new Date(),
    };
    const result = await this.userVideosCollection.insertOne(doc as UserVideoDocument);
    return { ...doc, _id: result.insertedId } as UserVideoDocument;
  }

  /**
   * Point a library entry at another row (an update to the latest version):
   * it keeps its id and folder and takes the row's denormalised fields where
   * the data has them, remembering `previousVideoSummaryId` when given. Null
   * when the entry is gone.
   */
  async repointUserVideo(userVideoId: string, data: CreateUserVideoData, previousVideoSummaryId?: string): Promise<UserVideoDocument | null> {
    const { userId, videoSummaryId, status, title, channel, duration, thumbnailUrl } = data;
    const previous = previousVideoSummaryId ? { previousVideoSummaryId: new ObjectId(previousVideoSummaryId) } : {};
    return this.userVideosCollection.findOneAndUpdate(
      { _id: new ObjectId(userVideoId), userId: new ObjectId(userId) },
      {
        $set: { videoSummaryId: new ObjectId(videoSummaryId), status, title, channel, duration, thumbnailUrl, ...previous, updatedAt: new Date() },
        ...(!previousVideoSummaryId && { $unset: { previousVideoSummaryId: '' as const } }),
      },
      { returnDocument: 'after', ignoreUndefined: true },
    );
  }

  async deleteUserVideo(userId: string, videoId: string): Promise<boolean> {
    const result = await this.userVideosCollection.deleteOne({
      _id: new ObjectId(videoId),
      userId: new ObjectId(userId),
    });
    return result.deletedCount > 0;
  }

  async deleteUserVideoByYoutubeId(userId: string, youtubeId: string, folderId?: string | null): Promise<void> {
    const query: Record<string, unknown> = {
      userId: new ObjectId(userId),
      youtubeId,
    };
    if (folderId !== undefined) {
      query.folderId = folderId ? new ObjectId(folderId) : null;
    }
    await this.userVideosCollection.deleteOne(query);
  }

  async updateUserVideoFolder(userId: string, videoId: string, folderId: string | null): Promise<boolean> {
    const result = await this.userVideosCollection.updateOne(
      {
        _id: new ObjectId(videoId),
        userId: new ObjectId(userId),
      },
      {
        $set: {
          folderId: folderId ? new ObjectId(folderId) : null,
          updatedAt: new Date(),
        },
      }
    );
    return result.matchedCount > 0;
  }

  async getUserVideos(
    userId: string,
    folderId?: string,
    options: { limit?: number; offset?: number } = {}
  ): Promise<Array<UserVideoDocument & { cache?: VideoSummaryCacheDocument }>> {
    const { limit = 50, offset = 0 } = options;
    const matchStage: Record<string, unknown> = { userId: new ObjectId(userId) };
    if (folderId) {
      matchStage.folderId = new ObjectId(folderId);
    }

    return this.userVideosCollection.aggregate([
      { $match: matchStage },
      { $sort: { createdAt: -1 } },
      { $skip: offset },
      { $limit: limit },
      {
        $lookup: {
          from: 'videoSummaryCache',
          localField: 'videoSummaryId',
          foreignField: '_id',
          as: 'cache',
        },
      },
      { $unwind: { path: '$cache', preserveNullAndEmptyArrays: true } },
    ]).toArray() as Promise<Array<UserVideoDocument & { cache?: VideoSummaryCacheDocument }>>;
  }

  async countUserVideos(userId: string, folderId?: string): Promise<number> {
    const query: Record<string, unknown> = { userId: new ObjectId(userId) };
    if (folderId) {
      query.folderId = new ObjectId(folderId);
    }
    return this.userVideosCollection.countDocuments(query);
  }

  async userOwnsVideo(userId: string, youtubeId: string): Promise<boolean> {
    const video = await this.userVideosCollection.findOne({
      userId: new ObjectId(userId),
      youtubeId,
    });
    return !!video;
  }

  async userHasAccessToSummary(userId: string, videoSummaryId: string): Promise<boolean> {
    const video = await this.userVideosCollection.findOne({
      userId: new ObjectId(userId),
      videoSummaryId: new ObjectId(videoSummaryId),
    });
    return !!video;
  }

  async updateUserVideoPlaylistInfo(videoId: string, playlistInfo: UserVideoDocument['playlistInfo']): Promise<void> {
    await this.userVideosCollection.updateOne(
      { _id: new ObjectId(videoId) },
      { $set: { playlistInfo, updatedAt: new Date() } }
    );
  }

  async getPlaylistVideos(userId: string, playlistId: string): Promise<UserVideoDocument[]> {
    return this.userVideosCollection
      .find({
        userId: new ObjectId(userId),
        'playlistInfo.playlistId': playlistId,
      })
      .sort({ 'playlistInfo.position': 1 })
      .toArray();
  }

  // ─── Pipeline output methods ───

  async updateTriage(id: string, triage: unknown): Promise<void> {
    await this.cacheCollection.updateOne(
      { _id: new ObjectId(id) },
      { $set: { triage, updatedAt: new Date() } },
    );
  }

  async updateOutput(id: string, output: unknown): Promise<void> {
    await this.cacheCollection.updateOne(
      { _id: new ObjectId(id) },
      { $set: { output, updatedAt: new Date() } },
    );
  }

  /**
   * Drop the previous run's synthesis before a row is (re-)dispatched: the
   * merge guard can't tell runs apart, so a stale masterSummary would block
   * this run's partial and mix into a cached replay.
   */
  async clearSynthesis(id: string): Promise<void> {
    await this.cacheCollection.updateOne({ _id: new ObjectId(id) }, { $unset: { synthesis: '' } });
  }

  /**
   * Record who dispatched the row's next run so the summarizer can attribute
   * its `llm_usage` (B2). The queue payload carries the user to the worker,
   * but an SSE request can win the summarizer's per-video lock first and run
   * the pipeline in the HTTP process, where only the row knows the owner —
   * the same reason `forceRefresh` rides on the row.
   *
   * Always overwrites: the row is shared and re-dispatched, so an absent
   * `requestId` is `$unset` — an earlier dispatcher's id must never bill this
   * run. No `updatedAt` bump: the summarizer's stall sweeper and `createVideo`'s
   * stall check read it as liveness, and `tryClaimDispatchRelease` compares it
   * with the release marker — bumping it here would let a late duplicate
   * FAILED event release the guard this dispatch just acquired.
   */
  async stampDispatchContext(id: string, userId: string, requestId?: string, billingRunId?: string): Promise<void> {
    const set: Partial<VideoSummaryCacheDocument> = { userId };
    const unset: Record<string, ''> = {};
    if (requestId) set.requestId = requestId;
    else unset.requestId = '';
    // Same rule for the billing run: an earlier dispatch's run must never be settled by this one.
    if (billingRunId) set.billingRunId = billingRunId;
    else unset.billingRunId = '';
    const update: UpdateFilter<VideoSummaryCacheDocument> = Object.keys(unset).length > 0
      ? { $set: set, $unset: unset }
      : { $set: set };
    await this.cacheCollection.updateOne({ _id: new ObjectId(id) }, update);
  }

  /** Merge one `synthesis_complete` emission — rules in buildSynthesisMerge. */
  async mergeSynthesis(id: string, synthesis: SynthesisFields): Promise<void> {
    const merge = buildSynthesisMerge(synthesis);
    if (!merge) return;
    const unset = Object.keys(merge.unset).length > 0 ? { $unset: merge.unset } : {};
    await this.cacheCollection.updateOne(
      { _id: new ObjectId(id), ...merge.guard },
      { $set: { ...merge.set, updatedAt: new Date() }, ...unset },
    );
  }

  async updateEnrichment(id: string, enrichment: unknown): Promise<void> {
    await this.cacheCollection.updateOne(
      { _id: new ObjectId(id) },
      { $set: { enrichment, updatedAt: new Date() } },
    );
  }

  // ─── v2: Assembly methods ───

  async updateAssembledMeta(id: string, meta: unknown): Promise<void> {
    await this.cacheCollection.updateOne(
      { _id: new ObjectId(id) },
      { $set: { assembledMeta: meta, assembledTabs: [], updatedAt: new Date() } },
    );
  }

  async appendAssembledTab(id: string, tab: unknown): Promise<void> {
    return this.appendAssembledTabs(id, [tab]);
  }

  async appendAssembledTabs(id: string, tabs: unknown[]): Promise<void> {
    if (tabs.length === 0) return;
    const MAX_TABS = 30;
    const result = await this.cacheCollection.updateOne(
      { _id: new ObjectId(id) },
      {
        $push: { assembledTabs: { $each: tabs, $slice: -MAX_TABS } } as Record<string, unknown>,
        $set: { updatedAt: new Date() },
      },
    );
    if (result.modifiedCount === 0) {
      throw new Error(`appendAssembledTabs: no document matched id=${id}`);
    }
  }

}
