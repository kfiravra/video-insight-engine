import { FastifyBaseLogger } from 'fastify';
import { VideoRepository, VideoSummaryCacheDocument } from '../repositories/video.repository.js';
import { SummarizerClient, type ProviderConfig } from './summarizer-client.js';
import { QueuePublisher } from './queue-publisher.service.js';
import { IdempotencyService } from './idempotency.service.js';
import type { IDispatchGuard } from './dispatch-guard.service.js';
import { config } from '../config.js';
import { extractYoutubeId } from '../utils/youtube.js';
import { InvalidYouTubeUrlError, VideoNotFoundError, VersionCreationError, InvalidCategoryError, QueuePublishError } from '../utils/errors.js';
import { buildMetaFromDoc, buildTabsFromDoc } from '../utils/meta-builder.js';
import { refreshFrameUrls } from '../utils/refresh-frame-urls.js';
import { parseSourceLanguage } from '../utils/source-language.js';
import type { UserTier } from '@vie/types';

export interface CreateVideoOptions {
  folderId?: string;
  bypassCache?: boolean;
  providers?: ProviderConfig;
  tier: UserTier;
  /**
   * Originating Fastify `request.id`. Flows into the queue payload (as the
   * publisher's `requestId`) AND the legacy HTTP fallback (as `X-Request-ID`)
   * so the downstream pipeline binds the same id no matter which path runs.
   */
  requestId?: string;
}

/**
 * Reads the DB-only `users.isEvalUser` flag (D25). Narrow on purpose: version
 * creation and listing need this one bit of the user document, nothing else.
 */
export interface EvalUserReader {
  isEvalUser(userId: string): Promise<boolean>;
}

interface NewVersionInput {
  userId: string;
  url: string;
  youtubeId: string;
  folderId?: string;
  providers?: ProviderConfig;
  tier: UserTier;
  requestId?: string;
  evalRun: boolean;
  /** Skip the summarizer's response cache (the submission's bypassCache). */
  forceRefresh: boolean;
}

interface CreatedVersion {
  userVideoId: string;
  videoSummaryId: string;
  newVersion: number;
  previousVersion?: number;
}

// Maximum versions to keep per video and per pool — user versions and eval
// versions are counted and pruned separately (prevents unbounded storage growth)
const MAX_VERSIONS_PER_VIDEO = 5;

/** A row without a `version` field predates versioning and counts as v1; no row is 0. */
function versionOf(doc: Pick<VideoSummaryCacheDocument, 'version'> | null | undefined): number {
  return doc ? doc.version || 1 : 0;
}

// A pending/processing cache row whose `updatedAt` is older than this is
// treated as stalled — most likely a worker died without flipping the row
// to `failed`. We re-dispatch through the standard path; the dispatch-guard
// (15-min Redis TTL) and the summarizer's per-videoSummaryId lock keep us
// safe from racing a still-live producer. 30 minutes is generous against
// the longest legitimately-long pipeline runs and short enough that a
// stuck row doesn't trap attachers indefinitely.
const PIPELINE_STALL_THRESHOLD_MS = 30 * 60 * 1000;

export class VideoService {
  constructor(
    private readonly videoRepository: VideoRepository,
    private readonly summarizerClient: SummarizerClient,
    private readonly queuePublisher: QueuePublisher,
    private readonly idempotencyService: IdempotencyService,
    private readonly dispatchGuard: IDispatchGuard,
    private readonly evalUsers: EvalUserReader,
    private readonly logger: FastifyBaseLogger
  ) {}

  /**
   * Dispatch a pipeline run via either the legacy HTTP path or the queue.
   * Falls back to HTTP if the queue publish fails — the DB row is already in
   * `pending`, so the SSE handler can still drive the pipeline directly when
   * the frontend connects.
   */
  private async dispatchPipeline(
    payload: {
      videoSummaryId: string;
      youtubeId: string;
      url: string;
      userId: string;
      tier: UserTier;
      providers?: ProviderConfig;
      bypassCache?: boolean;
      requestId?: string;
    },
  ): Promise<{ dispatched: boolean }> {
    // Belt-and-suspenders: even though Step 1's content-addressed upsert
    // already collapses concurrent submits at the cache row, this guard
    // catches the narrower window where two API replicas might race outside
    // Mongo's serialization, or where dispatchPipeline is called twice for
    // the same row (e.g. failed-retry firing while a prior dispatch is still
    // in-flight). The guard fails open on Redis outage — the summarizer's
    // own per-videoSummaryId lock is the last line of defense.
    //
    // Return value contract: `dispatched: false` when the guard rejected us
    // (someone else owns the run). Callers use this to skip side-effects
    // that should only happen on a real publish — e.g. bumping retryCount.
    const guard = await this.dispatchGuard.acquire(payload.videoSummaryId);
    if (!guard.acquired) {
      this.logger.info(
        {
          videoSummaryId: payload.videoSummaryId,
          reason: 'already_dispatched',
        },
        'dispatch-guard already held; skipping publish',
      );
      return { dispatched: false };
    }

    // Wrap the entire publish path so any throw (broker outage, schema bug,
    // HTTP fallback exception) releases the guard via the Lua CAS before
    // propagating. Without this the guard sits held for the full TTL
    // (DISPATCH_GUARD_TTL_SECONDS), silently blocking the user's retries.
    try {
      if (config.USE_QUEUE_PIPELINE) {
        try {
          // `bypassCache` IS consumed downstream: the worker maps it to
          // force_refresh (worker/pipeline.py), telling the summarizer to
          // skip its youtubeId-keyed Redis response cache. The HTTP/SSE
          // fallback path can't carry the flag, so the same semantic is
          // also stamped on the cache row as `forceRefresh` (see
          // createVideo's bypassCache branch) — pipeline_runner reads
          // whichever signal arrives. Keep BOTH channels in sync.
          await this.queuePublisher.publishVideoJob({
            videoSummaryId: payload.videoSummaryId,
            youtubeId: payload.youtubeId,
            url: payload.url,
            userId: payload.userId,
            tier: payload.tier,
            providers: payload.providers,
            bypassCache: payload.bypassCache,
            requestId: payload.requestId,
          });
          return { dispatched: true };
        } catch (err) {
          // Only swallow broker-side / validation failures (QueuePublishError).
          // Anything else is a real bug and should surface to the caller —
          // masking it with the HTTP fallback would hide schema regressions.
          if (!(err instanceof QueuePublishError)) {
            throw err;
          }
          this.logger.error(
            {
              err,
              videoSummaryId: payload.videoSummaryId,
              youtubeId: payload.youtubeId,
              requestId: payload.requestId,
            },
            'Queue publish failed, falling back to HTTP summarizer call',
          );
        }
      }

      this.summarizerClient.triggerSummarization({
        videoSummaryId: payload.videoSummaryId,
        youtubeId: payload.youtubeId,
        url: payload.url,
        userId: payload.userId,
        providers: payload.providers,
        requestId: payload.requestId,
      });
      return { dispatched: true };
    } catch (err) {
      // Release the guard first so a retry can re-acquire immediately.
      await this.dispatchGuard.release(payload.videoSummaryId, guard.token);
      // Mark the cache row failed so subsequent attachers (same user via
      // alreadyExists, cross-user via upsertCacheByDedupKey) hit the
      // failed-retry branch instead of attaching to a corpse. Best-effort:
      // we still throw the original error to surface a 500 to the caller.
      await this.videoRepository
        .updateCacheEntry(payload.videoSummaryId, {
          status: 'failed',
          errorCode: 'DISPATCH_FAILED',
          errorMessage: err instanceof Error ? err.message : String(err),
        })
        .catch((markErr: unknown) =>
          this.logger.error(
            { err: markErr, videoSummaryId: payload.videoSummaryId },
            'Failed to mark cache row failed after dispatch error',
          ),
        );
      throw err;
    }
  }

  /**
   * A completed doc is stale when it carries a `pipelineVersion` stamp that
   * differs from the canonical version (packages/shared/src/config/
   * pipeline-version.json). Docs WITHOUT the field predate stamping and are
   * treated as current-legacy — served as-is, never regenerated — so a
   * version bump can never mass-invalidate the existing cache. Stamping
   * happens summarizer-side at pipeline write time; the regen here replaces
   * the old out-of-band DB flush.
   */
  private isStaleVersion(doc: Pick<VideoSummaryCacheDocument, 'pipelineVersion'>): boolean {
    return (
      typeof doc.pipelineVersion === 'string' &&
      doc.pipelineVersion !== config.PIPELINE_VERSION
    );
  }

  /**
   * Version numbers are shared by user and eval rows so every row keeps a
   * distinct versioned dedupKey — a user run continues past eval versions
   * instead of colliding with one. An eval run (D25) never demotes the current
   * latest and never takes version 1: that number's dedupKey is the shared row
   * every new submitter attaches to. A user run demotes the current latest and
   * returns null when the video has no user versions (eval rows don't count),
   * so the caller falls through to the normal flow.
   */
  private async nextVersionNumber(
    youtubeId: string,
    evalRun: boolean,
  ): Promise<{ newVersion: number; previousVersion?: number } | null> {
    const highest = versionOf(await this.videoRepository.findHighestVersion(youtubeId));
    if (evalRun) return { newVersion: Math.max(highest, 1) + 1 };

    const previousLatest = await this.videoRepository.markPreviousVersionsNotLatest(youtubeId);
    // No latest row (orphaned versions) → continue from the highest user version
    const previous = versionOf(previousLatest)
      || versionOf(await this.videoRepository.findHighestVersion(youtubeId, false));
    if (!previous) return null;
    return { newVersion: Math.max(previous, highest) + 1, previousVersion: previous };
  }

  /** New version row + this user's library entry re-pointed at it + dispatch. */
  private async insertVersion(input: NewVersionInput): Promise<CreatedVersion | null> {
    const { userId, url, youtubeId, folderId, providers, tier, requestId, evalRun, forceRefresh } = input;
    const numbering = await this.nextVersionNumber(youtubeId, evalRun);
    if (!numbering) return null;

    // The dedupKey is versioned so two concurrent Retry clicks that compute
    // the same newVersion collide on the partial unique index (E11000 →
    // VersionCreationError) instead of silently producing two duplicate rows.
    const dedupKey = this.idempotencyService.computeContentKey({
      youtubeId,
      providers,
      version: numbering.newVersion,
    });
    const cacheEntry = await this.videoRepository.createCacheEntry({
      youtubeId,
      url,
      status: 'pending',
      version: numbering.newVersion,
      isLatest: !evalRun,
      retryCount: 0,
      dedupKey,
      ...(forceRefresh && { forceRefresh: true }),
      ...(evalRun && { evalRun: true }),
    });
    const videoSummaryId = cacheEntry._id.toString();

    await this.videoRepository.deleteUserVideoByYoutubeId(userId, youtubeId, folderId);
    const userVideo = await this.videoRepository.createUserVideo({
      userId,
      videoSummaryId,
      youtubeId,
      status: 'pending',
      folderId,
    });

    // Non-blocking to avoid slowing down the request
    this.videoRepository
      .pruneVersions(youtubeId, { evalRun, keep: MAX_VERSIONS_PER_VIDEO })
      .catch(err => {
        this.logger.warn({ youtubeId, error: err }, 'Failed to cleanup old versions');
      });

    await this.dispatchPipeline({
      videoSummaryId,
      youtubeId,
      url,
      userId,
      tier,
      providers,
      bypassCache: forceRefresh,
      requestId,
    });

    return { userVideoId: userVideo._id.toString(), videoSummaryId, ...numbering };
  }

  /** `insertVersion` with its failures mapped to VersionCreationError. */
  private async createVersion(input: NewVersionInput): Promise<CreatedVersion | null> {
    try {
      return await this.insertVersion(input);
    } catch (error) {
      // Duplicate key: two requests raced to create the same version
      if (error instanceof Error && 'code' in error && (error as { code: number }).code === 11000) {
        throw new VersionCreationError('Version conflict - please retry');
      }
      this.logger.error({
        youtubeId: input.youtubeId,
        userId: input.userId,
        evalRun: input.evalRun,
        error: error instanceof Error ? error.message : String(error),
      }, 'Failed in version creation');
      throw new VersionCreationError();
    }
  }

  async createVideo(userId: string, url: string, options: CreateVideoOptions) {
    const { folderId, bypassCache = false, providers, tier, requestId } = options;
    const youtubeId = extractYoutubeId(url);
    if (!youtubeId) {
      throw new InvalidYouTubeUrlError();
    }

    // D25: every eval-user submission becomes its own eval version — it never
    // creates, attaches to, retries or regenerates a row other users are
    // served. Its bypassCache only decides whether the summarizer may answer
    // from its response cache.
    const evalRun = await this.evalUsers.isEvalUser(userId);

    // Cache bypass creates a new version instead of deleting (for A/B testing prompts)
    if (bypassCache || evalRun) {
      const created = await this.createVersion({
        userId,
        url,
        youtubeId,
        folderId,
        providers,
        tier,
        requestId,
        evalRun,
        forceRefresh: bypassCache,
      });
      if (created) {
        return {
          video: {
            id: created.userVideoId,
            videoSummaryId: created.videoSummaryId,
            youtubeId,
            status: 'pending',
            version: created.newVersion,
            previousVersion: created.previousVersion,
          },
          cached: false,
          newVersion: true,
        };
      }
      // A user run on a video with no user versions falls through to the normal flow
    }

    // Check if user already has this video in this folder (skip if bypassCache)
    if (!bypassCache) {
      const existingInFolder = await this.videoRepository.findUserVideoByYoutubeId(userId, youtubeId, folderId);

      if (existingInFolder) {
        // Already exists in this folder - return existing
        const summary = await this.videoRepository.findCacheById(existingInFolder.videoSummaryId.toString());

        // Version-stamped doc from an older pipeline → regenerate on the same
        // row instead of serving stale output. The user asked for this video
        // NOW, so this is the natural (and only cost-reserved) regen moment;
        // plain GETs never trigger regen. Unstamped docs never land here.
        if (summary && summary.status === 'completed' && this.isStaleVersion(summary)) {
          this.logger.info(
            {
              videoSummaryId: summary._id.toString(),
              storedVersion: summary.pipelineVersion,
              currentVersion: config.PIPELINE_VERSION,
            },
            'Completed doc has stale pipelineVersion — re-dispatching pipeline',
          );

          await this.dispatchPipeline({
            videoSummaryId: summary._id.toString(),
            youtubeId,
            url,
            userId,
            tier,
            providers,
            requestId,
          });

          return {
            video: {
              id: existingInFolder._id.toString(),
              videoSummaryId: summary._id.toString(),
              youtubeId,
              title: summary.title,
              channel: summary.channel,
              duration: summary.duration,
              thumbnailUrl: summary.thumbnailUrl,
              status: 'pending',
            },
            cached: false,
            regenerating: true,
          };
        }

        return {
          video: {
            id: existingInFolder._id.toString(),
            videoSummaryId: existingInFolder.videoSummaryId.toString(),
            youtubeId,
            title: summary?.title || existingInFolder.title,
            channel: summary?.channel || existingInFolder.channel,
            duration: summary?.duration || existingInFolder.duration,
            thumbnailUrl: summary?.thumbnailUrl || existingInFolder.thumbnailUrl,
            status: summary?.status || existingInFolder.status,
          },
          cached: true,
          alreadyExists: true,
        };
      }
    }

    // Content-addressed dedup — the atomic upsert collapses concurrent
    // cross-user submissions for the same (youtubeId, providers, version=1)
    // onto a single cache row. Exactly one caller sees wasInsert=true and
    // is responsible for dispatching the pipeline; the rest attach.
    const dedupKey = this.idempotencyService.computeContentKey({
      youtubeId,
      providers,
      version: 1,
    });
    const { doc: cached, wasInsert } = await this.videoRepository.upsertCacheByDedupKey({
      youtubeId,
      url,
      status: 'pending',
      version: 1,
      isLatest: true,
      retryCount: 0,
      dedupKey,
      // bypassCache reaches here only when no Mongo versions survived (the
      // fall-through at the end of the bypass branch). The summarizer's Redis
      // response cache is keyed by youtubeId and can outlive the Mongo rows,
      // so the bypass intent must still ride on the fresh row.
      ...(bypassCache && { forceRefresh: true }),
    });

    if (wasInsert) {
      // We won the race — create the user-video first so a dispatch failure
      // doesn't strand the originator without a row, then dispatch the
      // pipeline. Attachers from later submits (or earlier in-progress runs)
      // will branch into the !wasInsert paths below.
      const userVideo = await this.videoRepository.createUserVideo({
        userId,
        videoSummaryId: cached._id.toString(),
        youtubeId,
        status: 'pending',
        folderId,
      });

      await this.dispatchPipeline({
        videoSummaryId: cached._id.toString(),
        youtubeId,
        url,
        userId,
        tier,
        providers,
        bypassCache,
        requestId,
      });

      return {
        video: {
          id: userVideo._id.toString(),
          videoSummaryId: cached._id.toString(),
          youtubeId,
          status: 'pending',
        },
        cached: false,
      };
    }

    // wasInsert === false: we attached to a row another caller (or a prior
    // pipeline run) already created. Branch on current status.

    if (cached.status === 'completed') {
      // Stale version stamp → regen on the same row (mirrors the failed-row
      // retry mechanics below) instead of serving outdated output. Unstamped
      // legacy docs are current by definition (see isStaleVersion).
      if (this.isStaleVersion(cached)) {
        this.logger.info(
          {
            videoSummaryId: cached._id.toString(),
            storedVersion: cached.pipelineVersion,
            currentVersion: config.PIPELINE_VERSION,
          },
          'Attached to completed doc with stale pipelineVersion — re-dispatching pipeline',
        );

        const staleUserVideo = await this.videoRepository.createUserVideo({
          userId,
          videoSummaryId: cached._id.toString(),
          youtubeId,
          status: 'pending',
          folderId,
        });

        await this.dispatchPipeline({
          videoSummaryId: cached._id.toString(),
          youtubeId,
          url,
          userId,
          tier,
          providers,
          requestId,
        });

        return {
          video: {
            id: staleUserVideo._id.toString(),
            videoSummaryId: cached._id.toString(),
            youtubeId,
            status: 'pending',
          },
          cached: false,
          regenerating: true,
        };
      }

      const userVideo = await this.videoRepository.createUserVideo({
        userId,
        videoSummaryId: cached._id.toString(),
        youtubeId,
        title: cached.title,
        channel: cached.channel,
        duration: cached.duration,
        thumbnailUrl: cached.thumbnailUrl,
        status: 'completed',
        folderId,
      });

      return {
        video: {
          id: userVideo._id.toString(),
          videoSummaryId: cached._id.toString(),
          status: 'completed',
          ...this.toCacheResponse(cached),
        },
        cached: true,
      };
    }

    if (cached.status === 'failed') {
      // Existing row failed — retry the pipeline against the SAME row.
      // Order: userVideo first (so a dispatch error doesn't lose the user's
      // row), then dispatch, then increment retryCount ONLY when we actually
      // published. Without the dispatched-gate, concurrent attachers each
      // bump the counter even though only one publish happens.
      const userVideo = await this.videoRepository.createUserVideo({
        userId,
        videoSummaryId: cached._id.toString(),
        youtubeId,
        status: 'pending',
        folderId,
      });

      const { dispatched } = await this.dispatchPipeline({
        videoSummaryId: cached._id.toString(),
        youtubeId,
        url,
        userId,
        tier,
        providers,
        requestId,
      });

      if (dispatched) {
        await this.videoRepository.incrementRetryCount(cached._id.toString());
      }

      return {
        video: {
          id: userVideo._id.toString(),
          videoSummaryId: cached._id.toString(),
          status: 'pending',
        },
        cached: false,
      };
    }

    // status === 'pending' or 'processing' — attach to the in-flight run.
    // Before attaching blindly, check whether the producer is actually alive.
    // A row last touched >30min ago almost certainly belongs to a worker
    // that died mid-pipeline without flipping the row to `failed`; a fresh
    // attacher there would wait forever. Re-dispatch instead — the
    // dispatch-guard rejects a live re-publish, and the summarizer's own
    // per-videoSummaryId lock catches any race.
    const ageMs = Date.now() - cached.updatedAt.getTime();
    if (ageMs > PIPELINE_STALL_THRESHOLD_MS) {
      this.logger.warn(
        {
          videoSummaryId: cached._id.toString(),
          status: cached.status,
          ageMs,
          thresholdMs: PIPELINE_STALL_THRESHOLD_MS,
        },
        'Attaching to in-flight row exceeded stall threshold — re-dispatching',
      );

      const userVideo = await this.videoRepository.createUserVideo({
        userId,
        videoSummaryId: cached._id.toString(),
        youtubeId,
        status: 'pending',
        folderId,
      });

      await this.dispatchPipeline({
        videoSummaryId: cached._id.toString(),
        youtubeId,
        url,
        userId,
        tier,
        providers,
        requestId,
      });

      return {
        video: {
          id: userVideo._id.toString(),
          videoSummaryId: cached._id.toString(),
          status: 'pending',
        },
        cached: false,
      };
    }

    // `attached: true` tells the route to refund this caller's cost
    // reservation (they didn't trigger work; the SSE stream will deliver
    // the producer's events). Same semantic as a cache hit, different label
    // for observability.
    const userVideo = await this.videoRepository.createUserVideo({
      userId,
      videoSummaryId: cached._id.toString(),
      youtubeId,
      status: cached.status,
      folderId,
    });

    return {
      video: {
        id: userVideo._id.toString(),
        videoSummaryId: cached._id.toString(),
        status: cached.status,
      },
      cached: false,
      attached: true,
    };
  }

  async getVideos(
    userId: string,
    folderId?: string,
    options: { limit?: number; offset?: number } = {}
  ) {
    // Total counts the full filtered set (not just the current page) so the
    // route can expose pagination metadata alongside the page of videos.
    const [videos, total] = await Promise.all([
      this.videoRepository.getUserVideos(userId, folderId, options),
      this.videoRepository.countUserVideos(userId, folderId),
    ]);

    return {
      videos: videos.map(v => ({
        id: v._id.toString(),
        videoSummaryId: v.videoSummaryId.toString(),
        youtubeId: v.youtubeId,
        title: v.title || v.cache?.title,
        channel: v.channel || v.cache?.channel,
        duration: v.duration || v.cache?.duration,
        thumbnailUrl: v.thumbnailUrl || v.cache?.thumbnailUrl,
        status: v.cache?.status || v.status,
        folderId: v.folderId?.toString() || null,
        createdAt: v.createdAt.toISOString(),
      })),
      total,
    };
  }

  async getVideo(userId: string, videoId: string) {
    const video = await this.videoRepository.findUserVideo(userId, videoId);

    if (!video) {
      throw new VideoNotFoundError();
    }

    const summary = await this.videoRepository.findCacheById(video.videoSummaryId.toString());

    // Build clean frontend response using shared meta/tabs builder
    // VideoSummaryCacheDocument has dynamic fields; cast via unknown for meta extraction
    const doc = (summary as unknown) as Record<string, unknown> | undefined;
    const meta = doc ? buildMetaFromDoc(doc) : null;
    const tabs = doc ? buildTabsFromDoc(doc) : null;
    // Source-language nested block — present only for non-English videos
    // whose translation phase completed. Top-level tabs are always
    // English-primary; this carries the original-language artifact for the
    // FE toggle. Zod-validated at the DB → API boundary so a summarizer
    // shape drift surfaces as "no translation" instead of leaking malformed
    // data to the FE. Refresh S3 URLs on its tabs too.
    const sourceLanguage = parseSourceLanguage(doc?.sourceLanguage);
    await Promise.all([
      refreshFrameUrls(tabs),
      sourceLanguage ? refreshFrameUrls(sourceLanguage.tabs) : Promise.resolve(),
    ]);

    return {
      id: video._id.toString(),
      videoSummaryId: video.videoSummaryId.toString(),
      youtubeId: video.youtubeId,
      title: video.title || summary?.title,
      creator: video.channel || summary?.channel,
      duration: video.duration || summary?.duration,
      thumbnailUrl: video.thumbnailUrl || summary?.thumbnailUrl,
      status: summary?.status || video.status,
      folderId: video.folderId?.toString() || null,
      meta,
      tabs,
      // sourceLanguage is conditional — omitted entirely for English-source
      // videos rather than serialized as `null`. The FE checks presence to
      // decide whether to render the language toggle.
      ...(sourceLanguage ? { sourceLanguage } : {}),
    };
  }


  async moveToFolder(userId: string, videoId: string, folderId: string | null) {
    const updated = await this.videoRepository.updateUserVideoFolder(userId, videoId, folderId);

    if (!updated) {
      throw new VideoNotFoundError();
    }

    return { success: true };
  }

  /** Override the detected category for a video */
  async overrideCategory(userId: string, videoId: string, category: string) {
    const video = await this.videoRepository.findUserVideo(userId, videoId);
    if (!video) throw new VideoNotFoundError();

    const CATEGORY_TO_TAG: Record<string, string> = {
      cooking: 'food',
      coding: 'tech',
      travel: 'travel',
      reviews: 'review',
      fitness: 'fitness',
      education: 'learning',
      podcast: 'learning',
      diy: 'project',
      gaming: 'tech',
      music: 'music',
      standard: 'learning',
    };

    const contentTag = CATEGORY_TO_TAG[category];
    if (!contentTag) throw new InvalidCategoryError(category);

    const videoSummaryId = video.videoSummaryId.toString();
    const cache = await this.videoRepository.findCacheById(videoSummaryId);
    if (!cache) throw new VideoNotFoundError();

    const existingContext = (cache.context || {}) as Record<string, unknown>;
    await this.videoRepository.updateCacheEntry(videoSummaryId, {
      context: {
        ...existingContext,
        originalCategory: existingContext.category || 'standard',
        category,
      },
    });

    return {
      videoSummaryId,
      category,
      contentTag,
      previousCategory: existingContext.category || 'standard',
    };
  }

  // Check if user owns a video with this youtubeId
  async userOwnsVideo(userId: string, youtubeId: string): Promise<boolean> {
    return this.videoRepository.userOwnsVideo(userId, youtubeId);
  }

  /**
   * Get all versions of a video summary for A/B comparison.
   * Returns metadata only (not full summary content) to limit data exposure.
   */
  async getVersions(userId: string, youtubeId: string, options: { limit?: number } = {}) {
    const { limit = 10 } = options;
    // Server-side validation: enforce bounds regardless of input
    const safeLimit = Math.min(Math.max(1, limit), 50);

    // Eval versions are listed to the eval user only (D25)
    const includeEval = await this.evalUsers.isEvalUser(userId);
    const versions = await this.videoRepository.getVersions(youtubeId, safeLimit, { includeEval });

    return versions.map(v => ({
      id: v._id.toString(),
      youtubeId: v.youtubeId,
      version: v.version || 1,
      isLatest: v.isLatest ?? true,
      status: v.status,
      title: v.title,
      channel: v.channel,
      duration: v.duration,
      thumbnailUrl: v.thumbnailUrl,
      createdAt: v.createdAt?.toISOString(),
      processedAt: v.processedAt?.toISOString(),
      processingTimeMs: v.processingTimeMs,
      errorCode: v.errorCode,
      errorMessage: v.errorMessage,
    }));
  }

  private toCacheResponse(doc: VideoSummaryCacheDocument) {
    return {
      youtubeId: doc.youtubeId,
      title: doc.title,
      channel: doc.channel,
      duration: doc.duration,
      thumbnailUrl: doc.thumbnailUrl,
      version: doc.version,
      isLatest: doc.isLatest,
    };
  }
}
