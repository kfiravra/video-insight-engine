import { Db, ObjectId } from 'mongodb';
import type { Document, Filter } from 'mongodb';
import type { FastifyBaseLogger } from 'fastify';
import { VideoRepository } from '../repositories/video.repository.js';
import {
  VideoDeletionRepository,
  type VideoDeletionCounts,
  type VideoDeletionInitiator,
} from '../repositories/video-deletion.repository.js';
import { IdempotencyService } from './idempotency.service.js';
import { SummarizerClient, type PurgeVideoResult } from './summarizer-client.js';
import type { IDispatchGuard } from './dispatch-guard.service.js';
import { VideoNotFoundError } from '../utils/errors.js';

/**
 * Collections holding per-video rows that this service may `deleteMany`
 * with a filter. A union (not `string`) so a typo or a future call site
 * cannot aim a delete at `users` or any other unrelated collection.
 */
type VideoScopedCollection =
  | 'videoSummaryCache'
  | 'userVideos'
  | 'shareLikes'
  | 'shareViews'
  | 'agentNotes';

export interface VideoDeleteActor {
  initiatedBy: VideoDeletionInitiator;
  adminId: string | null;
  reason?: string | null;
}

/**
 * `global` removes a video from every store for everyone (admin action).
 * `user` removes one user's reference and their own data for it; the shared
 * summary is kept because other users may reference it and the compute was
 * already paid. A future per-user "custom version" delete adds a scope here
 * and reuses the same stages.
 */
export type VideoDeleteRequest =
  | { scope: 'global'; youtubeId: string; actor: VideoDeleteActor }
  | { scope: 'user'; userId: string; userVideoId: string };

type GlobalDeleteRequest = Extract<VideoDeleteRequest, { scope: 'global' }>;
type UserDeleteRequest = Extract<VideoDeleteRequest, { scope: 'user' }>;

export interface VideoCascadeResult {
  scope: 'global' | 'user';
  youtubeId: string;
  summaryIds: string[];
  counts: VideoDeletionCounts;
  warnings: string[];
  /** True when at least one step failed (details in `warnings`); callers checking only the status must look here. */
  partial: boolean;
}

interface CacheKey {
  _id: ObjectId;
  shareSlug?: string;
}

/** The summarizer's purge endpoint accepts at most this many summary ids per call. */
const PURGE_BATCH_SIZE = 50;

function chunk<T>(items: T[], size: number): T[][] {
  const batches: T[][] = [];
  for (let start = 0; start < items.length; start += size) {
    batches.push(items.slice(start, start + size));
  }
  return batches;
}

/** The assistant stores userId as a string; older rows may hold an ObjectId. */
function userIdForms(userId: string): Filter<Document>[] {
  return ObjectId.isValid(userId) ? [{ userId }, { userId: new ObjectId(userId) }] : [{ userId }];
}

export function emptyVideoDeletionCounts(
  overrides: Partial<VideoDeletionCounts> = {},
): VideoDeletionCounts {
  return {
    videoSummaryCache: 0,
    userVideos: 0,
    shareLikes: 0,
    shareViews: 0,
    agentNotes: 0,
    idempotencyKeys: 0,
    dispatchGuards: 0,
    qdrantPoints: 0,
    s3Objects: 0,
    redisKeys: 0,
    ...overrides,
  };
}

/**
 * The one place a video is deleted. Global deletes run a best-effort saga:
 * the summarizer purge (Qdrant, S3, Redis) goes first because it is
 * idempotent and retryable, so a failure there aborts before any Mongo row
 * is touched; the Mongo stages then collect per-step warnings instead of
 * aborting, mirroring `UserDeletionService`, and an audit row records the
 * counts.
 */
export class VideoCascadeService {
  constructor(
    private readonly db: Db,
    private readonly videoRepository: VideoRepository,
    private readonly idempotencyService: IdempotencyService,
    private readonly dispatchGuard: IDispatchGuard,
    private readonly summarizerClient: SummarizerClient,
    private readonly videoDeletionRepository: VideoDeletionRepository,
    private readonly logger: FastifyBaseLogger,
    private readonly clock: () => Date = () => new Date(),
  ) {}

  async deleteVideo(request: VideoDeleteRequest): Promise<VideoCascadeResult> {
    return request.scope === 'global'
      ? this.deleteGlobally(request)
      : this.deleteForUser(request);
  }

  // ─── global scope ────────────────────────────────────────────────────────

  private async deleteGlobally(request: GlobalDeleteRequest): Promise<VideoCascadeResult> {
    const { youtubeId, actor } = request;
    const startedAt = this.clock();
    const keys = await this.videoRepository.findCacheKeysByYoutubeId(youtubeId);
    const summaryIds = keys.map((key) => key._id.toString());

    const purge = await this.purgeArtifacts(youtubeId, summaryIds);
    const counts = emptyVideoDeletionCounts({
      qdrantPoints: purge.qdrantPoints,
      s3Objects: purge.s3Objects,
      redisKeys: purge.redisKeys,
    });
    const warnings = purge.warnings.map((warning) => `summarizer: ${warning}`);

    await this.deleteReferences(youtubeId, keys, counts, warnings);
    await this.releaseDispatchGuards(youtubeId, summaryIds, counts, warnings);

    const completedAt = this.clock();
    await this.videoDeletionRepository.insert({
      youtubeId,
      summaryIds,
      scope: 'global',
      initiatedBy: actor.initiatedBy,
      adminId: actor.adminId,
      reason: actor.reason ?? null,
      counts,
      startedAt,
      completedAt,
      warnings,
    });
    this.logOutcome({
      youtubeId,
      summaryIds,
      counts,
      warnings,
      durationMs: completedAt.getTime() - startedAt.getTime(),
    });
    return { scope: 'global', youtubeId, summaryIds, counts, warnings, partial: warnings.length > 0 };
  }

  /**
   * Artifacts are keyed by youtubeId, so every batch purges the same objects
   * and only the per-run Redis keys differ; the counts add up correctly. An
   * empty id list still purges (orphans with no summary row).
   */
  private async purgeArtifacts(youtubeId: string, summaryIds: string[]): Promise<PurgeVideoResult> {
    const batches = summaryIds.length === 0 ? [[]] : chunk(summaryIds, PURGE_BATCH_SIZE);
    const total: PurgeVideoResult = { qdrantPoints: 0, s3Objects: 0, redisKeys: 0, warnings: [] };
    for (const batch of batches) {
      const part = await this.summarizerClient.purgeVideo({ youtubeId, videoSummaryIds: batch });
      total.qdrantPoints += part.qdrantPoints;
      total.s3Objects += part.s3Objects;
      total.redisKeys += part.redisKeys;
      total.warnings.push(...part.warnings);
    }
    return total;
  }

  private async deleteReferences(
    youtubeId: string,
    keys: CacheKey[],
    counts: VideoDeletionCounts,
    warnings: string[],
  ): Promise<void> {
    const ids = keys.map((key) => key._id);
    const slugs = keys.flatMap((key) => (key.shareSlug ? [key.shareSlug] : []));
    // Also catches rows already orphaned by an earlier summary deletion.
    const rowFilter: Filter<Document> = { $or: [{ videoSummaryId: { $in: ids } }, { youtubeId }] };
    // Notes are keyed by summary id (video chat), youtube id or library row id
    // (library tools), so the row ids are collected before the rows go.
    const rowIds = await this.findIds('userVideos', rowFilter);
    const noteVideoIds = [...ids, ...rowIds].map((id) => id.toString()).concat(youtubeId);

    await this.runStep('userVideos', youtubeId, warnings, async () => {
      counts.userVideos = await this.deleteMany('userVideos', rowFilter);
    });
    if (slugs.length > 0) {
      await this.runStep('shareLikes', youtubeId, warnings, async () => {
        counts.shareLikes = await this.deleteMany('shareLikes', { shareSlug: { $in: slugs } });
      });
      await this.runStep('shareViews', youtubeId, warnings, async () => {
        counts.shareViews = await this.deleteMany('shareViews', { shareSlug: { $in: slugs } });
      });
    }
    await this.runStep('agentNotes', youtubeId, warnings, async () => {
      counts.agentNotes = await this.deleteMany('agentNotes', { videoId: { $in: noteVideoIds } });
    });
    await this.runStep('idempotencyKeys', youtubeId, warnings, async () => {
      counts.idempotencyKeys = await this.idempotencyService.invalidateByYoutubeId(youtubeId);
    });
    // Summary rows go last: while they exist a retry can still resolve them.
    // Also by youtubeId: a version inserted between the key snapshot and this
    // step would otherwise survive with its artifacts already purged.
    await this.runStep('videoSummaryCache', youtubeId, warnings, async () => {
      counts.videoSummaryCache = await this.deleteMany('videoSummaryCache', {
        $or: [{ _id: { $in: ids } }, { youtubeId }],
      });
    });
  }

  private async releaseDispatchGuards(
    youtubeId: string,
    summaryIds: string[],
    counts: VideoDeletionCounts,
    warnings: string[],
  ): Promise<void> {
    await this.runStep('dispatchGuards', youtubeId, warnings, async () => {
      await Promise.all(summaryIds.map((id) => this.dispatchGuard.release(id, null)));
      counts.dispatchGuards = summaryIds.length;
    });
  }

  private logOutcome(details: {
    youtubeId: string;
    summaryIds: string[];
    counts: VideoDeletionCounts;
    warnings: string[];
    durationMs: number;
  }): void {
    if (details.warnings.length > 0) {
      this.logger.error(details, 'video_global_delete_completed_with_warnings');
      return;
    }
    this.logger.info(details, 'video_global_delete_completed');
  }

  // ─── user scope ──────────────────────────────────────────────────────────

  private async deleteForUser(request: UserDeleteRequest): Promise<VideoCascadeResult> {
    const { userId, userVideoId } = request;
    const row = await this.videoRepository.findUserVideo(userId, userVideoId);
    if (!row) {
      throw new VideoNotFoundError();
    }
    const summaryId = row.videoSummaryId.toString();
    const deleted = await this.videoRepository.deleteUserVideo(userId, userVideoId);
    if (!deleted) {
      // Lost a race with a concurrent delete: keep the pre-cascade 404 semantics.
      throw new VideoNotFoundError();
    }

    const counts = emptyVideoDeletionCounts({ userVideos: 1 });
    const warnings: string[] = [];
    await this.runStep('idempotencyKeys', userVideoId, warnings, async () => {
      counts.idempotencyKeys = await this.idempotencyService.invalidateByUserVideoId(userVideoId);
    });
    await this.runStep('agentNotes', userVideoId, warnings, async () => {
      // Notes are per (user, video), not per library row: keep them while the
      // user still holds the same video in another folder.
      const stillHeld = await this.videoRepository.findUserVideoByYoutubeId(userId, row.youtubeId);
      if (stillHeld) return;
      counts.agentNotes = await this.deleteMany('agentNotes', {
        $or: userIdForms(userId),
        videoId: { $in: [summaryId, row.youtubeId, userVideoId] },
      });
    });
    this.logger.info(
      { userId, userVideoId, youtubeId: row.youtubeId, counts, warnings },
      'video_user_delete_completed',
    );
    return {
      scope: 'user',
      youtubeId: row.youtubeId,
      summaryIds: [summaryId],
      counts,
      warnings,
      partial: warnings.length > 0,
    };
  }

  // ─── helpers ─────────────────────────────────────────────────────────────

  private async findIds(collection: VideoScopedCollection, filter: Filter<Document>): Promise<ObjectId[]> {
    const docs = await this.db.collection(collection).find(filter).project<{ _id: ObjectId }>({ _id: 1 }).toArray();
    return docs.map((doc) => doc._id);
  }

  private async deleteMany(
    collection: VideoScopedCollection,
    filter: Filter<Document>,
  ): Promise<number> {
    const result = await this.db.collection(collection).deleteMany(filter);
    return result.deletedCount;
  }

  private async runStep(
    step: string,
    traceId: string,
    warnings: string[],
    op: () => Promise<void>,
  ): Promise<void> {
    try {
      await op();
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      warnings.push(`${step}: ${message}`);
      this.logger.warn({ traceId, step, err: message }, 'video_delete_step_failed');
    }
  }
}
