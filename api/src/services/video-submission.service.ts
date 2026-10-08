import { FastifyBaseLogger } from 'fastify';
import { VideoService } from './video.service.js';
import { CostMonitorService, type CostReservation } from './cost-monitor.service.js';
import { IdempotencyService } from './idempotency.service.js';
import { VideoRepository } from '../repositories/video.repository.js';
import { extractYoutubeId } from '../utils/youtube.js';
import type { ProviderConfig } from './summarizer-client.js';
import type { UserTier } from '@vie/types';

/** Result of `VideoService.createVideo` — inferred so the two stay in sync. */
export type CreateVideoResult = Awaited<ReturnType<VideoService['createVideo']>>;

export interface SubmitVideoInput {
  userId: string;
  url: string;
  tier: UserTier;
  folderId?: string;
  bypassCache?: boolean;
  /** Cold-media benchmark run (admin/eval only — createVideo enforces it). */
  cold?: boolean;
  providers?: ProviderConfig;
  /** Pre-validated `Idempotency-Key` header value (validation is an HTTP
   *  concern and stays at the route boundary). */
  clientKey?: string;
  /** Originating request id, propagated into the pipeline and log context. */
  requestId?: string;
}

/** Snapshot of an already-created video returned on a duplicate submission. */
export interface DuplicateVideoView {
  id: string;
  videoSummaryId: string;
  youtubeId: string;
  title?: string;
  channel?: string;
  duration?: number;
  thumbnailUrl?: string;
  status?: string;
}

/**
 * Discriminated submission outcome. Routes map these to HTTP:
 * `duplicate` → 200 (+ cached/duplicate flags), `in_flight` → 409,
 * `created` → 201/200 with the raw createVideo result.
 */
export type SubmitVideoResult =
  | { outcome: 'duplicate'; video: DuplicateVideoView }
  | { outcome: 'in_flight' }
  | { outcome: 'created'; result: CreateVideoResult };

type IdempotencyGateResult =
  | { kind: 'proceed'; hash: string | null }
  | { kind: 'duplicate'; video: DuplicateVideoView }
  | { kind: 'in_flight' };

/**
 * Full video-submission flow: idempotency gate → cost reservation (daily cap)
 * → createVideo → reservation refund/hash unwind on failure. Extracted from
 * the POST /api/videos handler so EVERY submission path — user-facing and the
 * assistant's internal `/internal/assistant/generate` — runs the same gates.
 * Before this service existed, the internal route called `createVideo`
 * directly and bypassed both the daily cap and the reservation unwind.
 *
 * Throws `DailyLimitReachedError` (from `reserveUserCost`) and whatever
 * `createVideo` throws; the caller's error handler maps them to HTTP.
 */
export class VideoSubmissionService {
  constructor(
    private readonly videoService: VideoService,
    private readonly costMonitorService: CostMonitorService,
    private readonly idempotencyService: IdempotencyService,
    private readonly videoRepository: VideoRepository,
    private readonly logger: FastifyBaseLogger,
  ) {}

  async submit(input: SubmitVideoInput): Promise<SubmitVideoResult> {
    const gate = await this.openIdempotencyGate(input);
    if (gate.kind === 'duplicate') return { outcome: 'duplicate', video: gate.video };
    if (gate.kind === 'in_flight') return { outcome: 'in_flight' };
    const { hash } = gate;

    // Atomic reservation — increment-then-check serializes concurrent starts
    // on the per-user/day doc. Throws DailyLimitReachedError past the tier
    // cap. `reservation` is null for unlimited tiers.
    let reservation: CostReservation | null;
    try {
      reservation = await this.costMonitorService.reserveUserCost(input.userId, input.tier);
    } catch (err) {
      await this.unwindHash(hash, input.requestId);
      throw err;
    }

    let result: CreateVideoResult;
    try {
      result = await this.videoService.createVideo(input.userId, input.url, {
        folderId: input.folderId,
        bypassCache: input.bypassCache,
        cold: input.cold,
        providers: input.providers,
        tier: input.tier,
        requestId: input.requestId,
      });
    } catch (err) {
      await this.costMonitorService.refundReservation(reservation);
      await this.unwindHash(hash, input.requestId);
      throw err;
    }

    // Cached videos run no pipeline, so no llm_usage rows will arrive — refund
    // now. Attached videos (cross-user single-flight) behave identically: this
    // caller didn't trigger work. Swallow refund errors — the video is already
    // created and the nightly reconcile heals a stuck reservation.
    if (result.cached || result.attached) {
      await this.costMonitorService.refundReservation(reservation).catch((err) => {
        this.logger.warn(
          { err, userId: input.userId, requestId: input.requestId },
          'cached/attached-video refund failed; nightly reconcile will recover',
        );
      });
    }

    await this.settleHash(hash, result, input.requestId);
    return { outcome: 'created', result };
  }

  /**
   * Reserve-before-work idempotency gate. Atomic insert of a `pending`
   * placeholder on the unique-hash index — concurrent identical submits race
   * here and exactly one proceeds. `bypassCache=true` (or `cold`, which
   * implies it) is the explicit escape hatch and skips the gate entirely;
   * malformed URLs also skip (createVideo
   * raises InvalidYouTubeUrlError downstream).
   */
  private async openIdempotencyGate(input: SubmitVideoInput): Promise<IdempotencyGateResult> {
    const youtubeId = extractYoutubeId(input.url);
    if (!youtubeId || input.bypassCache || input.cold) return { kind: 'proceed', hash: null };

    const hash = this.idempotencyService.computeKey({
      userId: input.userId,
      youtubeId,
      providers: input.providers,
      clientKey: input.clientKey,
    });

    let reserveResult = await this.idempotencyService.reserveHash({
      hash,
      userId: input.userId,
      youtubeId,
    });
    if (reserveResult.created) return { kind: 'proceed', hash };

    const existing = reserveResult.doc;
    if (existing.status !== 'completed' || !existing.userVideoId || !existing.videoSummaryId) {
      // status === 'pending' — original work is still in flight.
      return { kind: 'in_flight' };
    }

    // Original finished — check the referenced user-video still exists. If the
    // user deleted it, drop the stale hash (scoped to the row we saw, so a
    // racing fresh reservation is left alone) and reserve again.
    const video = await this.buildDuplicateView(input.userId, existing.userVideoId.toString());
    if (video) return { kind: 'duplicate', video };

    await this.idempotencyService.invalidateStaleCompleted(hash, existing.videoSummaryId.toString());
    reserveResult = await this.idempotencyService.reserveHash({
      hash,
      userId: input.userId,
      youtubeId,
    });
    // Someone else won between our delete and retry — bow out gracefully.
    if (!reserveResult.created) return { kind: 'in_flight' };
    return { kind: 'proceed', hash };
  }

  /** Resolve a completed hit into a response view; null when the user-video is gone. */
  private async buildDuplicateView(userId: string, userVideoId: string): Promise<DuplicateVideoView | null> {
    const userVideo = await this.videoRepository.findUserVideo(userId, userVideoId);
    if (!userVideo) return null;

    const summary = await this.videoRepository.findCacheById(userVideo.videoSummaryId.toString());
    return {
      id: userVideo._id.toString(),
      videoSummaryId: userVideo.videoSummaryId.toString(),
      youtubeId: userVideo.youtubeId,
      title: summary?.title || userVideo.title,
      channel: summary?.channel || userVideo.channel,
      duration: summary?.duration || userVideo.duration,
      thumbnailUrl: summary?.thumbnailUrl || userVideo.thumbnailUrl,
      status: summary?.status || userVideo.status,
    };
  }

  /**
   * Failure unwind: drop the reserved hash so retries are allowed without
   * waiting for the TTL. Best-effort — a leaked placeholder self-heals via TTL.
   */
  private async unwindHash(hash: string | null, requestId?: string): Promise<void> {
    if (!hash) return;
    await this.idempotencyService.invalidateByHash(hash).catch((err) => {
      // Log only a hash prefix — the full hash is a stable per-(user,video)
      // ID and PII-adjacent. 8 hex chars is enough for log correlation.
      this.logger.warn(
        { err, hashPrefix: hash.slice(0, 8), requestId },
        'idempotency invalidate-on-unwind failed',
      );
    });
  }

  /**
   * Promote the pending hash to `completed` with the real IDs. Failure here
   * would leave the placeholder pending until TTL and lock the user out of
   * retries — so on missing IDs or a Mongo error, invalidate instead.
   */
  private async settleHash(hash: string | null, result: CreateVideoResult, requestId?: string): Promise<void> {
    if (!hash) return;
    if (result.video?.id && result.video?.videoSummaryId) {
      await this.idempotencyService.completeHash({
        hash,
        videoSummaryId: result.video.videoSummaryId,
        userVideoId: result.video.id,
      }).catch((err) => {
        this.logger.warn(
          { err, hashPrefix: hash.slice(0, 8), requestId },
          'idempotency complete failed; invalidating placeholder',
        );
        return this.unwindHash(hash, requestId);
      });
    } else {
      await this.unwindHash(hash, requestId);
    }
  }
}
