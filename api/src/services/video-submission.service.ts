import { randomUUID } from 'node:crypto';
import { FastifyBaseLogger } from 'fastify';
import { VideoService } from './video.service.js';
import { IdempotencyService } from './idempotency.service.js';
import { VideoRepository } from '../repositories/video.repository.js';
import { extractYoutubeId } from '../utils/youtube.js';
import { RunNotAllowedError } from '../utils/errors.js';
import { submitOutcome } from './billing/run-stamp.js';
import { updateOffer } from './library-update.js';
import { refusalServesWithoutRun } from './billing/billing-errors.js';
import { queuePlanOf, type QueuePlan } from './queue-topology.js';
import type { ProviderConfig } from './summarizer-client.js';
import type { GateSubmission } from './billing/billing-gate.js';
import type { LedgerEnforcement } from './billing/ledger-enforcement.js';
import type { UpdateOffer } from './library-update.js';

/** Result of `VideoService.createVideo` — inferred so the two stay in sync. */
export type CreateVideoResult = Awaited<ReturnType<VideoService['createVideo']>>;

export interface SubmitVideoInput {
  userId: string;
  url: string;
  folderId?: string;
  bypassCache?: boolean;
  /** D-LIB: regenerate the user's own copy an older pipeline wrote — a paid run, charged like a new video. */
  updateToLatest?: boolean;
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
  | ({ outcome: 'duplicate'; video: DuplicateVideoView } & UpdateOffer)
  | { outcome: 'in_flight' }
  | { outcome: 'created'; result: CreateVideoResult };

type IdempotencyGateResult =
  | { kind: 'proceed'; hash: string | null }
  | { kind: 'duplicate'; video: DuplicateVideoView; offer: UpdateOffer }
  | { kind: 'in_flight' };

/** What an earlier submit's idempotency key answers this one with. */
type ReplayGate = Exclude<IdempotencyGateResult, { kind: 'proceed' }>;

/**
 * Where a submission stands with the minutes ledger. `run` is the ledger's run
 * (its `requestId` is a server id generated per submission, P3.1 — never the
 * client's X-Request-ID); null without a video id.
 */
interface SubmitBilling {
  run: GateSubmission | null;
  /** What the hold paid with, for the queue priority (P3.4); `free` without a hold. */
  queuePlan: QueuePlan;
  /**
   * The ledger refused to pay for a run, but the refusal still lets a cached
   * or attachable video through for free (Q6): createVideo runs serve-only
   * and this error is thrown only if serving would need a run.
   */
  servingOnly?: unknown;
}

/**
 * Full video-submission flow: idempotency gate → minutes ledger (quote +
 * hold) → createVideo → give-back / hash unwind on failure. Extracted from
 * the POST /api/videos handler so EVERY submission path — user-facing and the
 * assistant's internal `/internal/assistant/generate` — runs the same gates.
 * The ledger throws its refusal (402/422/403/429, METADATA_UNAVAILABLE);
 * cached / attached / failed submits give the hold back before returning.
 */
export class VideoSubmissionService {
  constructor(
    private readonly videoService: VideoService,
    private readonly idempotencyService: IdempotencyService,
    private readonly videoRepository: VideoRepository,
    private readonly logger: FastifyBaseLogger,
    private readonly billing: LedgerEnforcement,
  ) {}

  async submit(input: SubmitVideoInput): Promise<SubmitVideoResult> {
    const gate = await this.openIdempotencyGate(input);
    if (gate.kind !== 'proceed') return this.replay(input.userId, gate);
    const { hash } = gate;

    const billing = await this.admit(input, hash);
    const result = await this.create(input, hash, billing);
    await this.resolveBilling(billing, result);
    await this.settleHash(hash, result, input.requestId);
    return { outcome: 'created', result };
  }

  /**
   * A replay of an earlier submit: the video it made, or in-flight while that
   * one still runs. It serves nothing new, yet a blocked account is refused
   * even that (D-BLOCK: no submits of any kind).
   */
  private async replay(userId: string, gate: ReplayGate): Promise<SubmitVideoResult> {
    await this.billing.refuseIfBlocked(userId);
    return gate.kind === 'duplicate' ? { outcome: 'duplicate', video: gate.video, ...gate.offer } : { outcome: 'in_flight' };
  }

  /**
   * Hold, or — for a refusal a free serve could still satisfy — go on
   * serve-only. Any other refusal unwinds the hash and propagates.
   */
  private async admit(input: SubmitVideoInput, hash: string | null): Promise<SubmitBilling> {
    const run = await this.billingRun(input);
    if (!run) return { run, queuePlan: 'free' };
    try {
      const hold = await this.billing.admit(run);
      return { run, queuePlan: queuePlanOf(hold.split) };
    } catch (err) {
      // Serve-only never dispatches, so its plan is never read.
      if (refusalServesWithoutRun(err)) return { run, queuePlan: 'free', servingOnly: err };
      await this.unwindHash(hash, input.requestId);
      throw err;
    }
  }

  private async create(input: SubmitVideoInput, hash: string | null, billing: SubmitBilling): Promise<CreateVideoResult> {
    const serveOnly = billing.servingOnly !== undefined;
    try {
      return await this.videoService.createVideo(input.userId, input.url, {
        folderId: input.folderId,
        bypassCache: input.bypassCache,
        updateToLatest: input.updateToLatest,
        cold: input.cold,
        providers: input.providers,
        queuePlan: billing.queuePlan,
        requestId: input.requestId,
        billingRunId: billing.run?.requestId,
        ...(serveOnly && { serveOnly }),
      });
    } catch (err) {
      if (!serveOnly && billing.run) await this.billing.submitFailed(billing.run.requestId);
      await this.unwindHash(hash, input.requestId);
      // Serving would have needed a run the ledger refused: surface that refusal.
      throw err instanceof RunNotAllowedError ? billing.servingOnly : err;
    }
  }

  /** Cached and attached videos run no pipeline for this caller (Q6): their hold goes back now. */
  private async resolveBilling(billing: SubmitBilling, result: CreateVideoResult): Promise<void> {
    // A serve-only submit took no hold, so there is nothing to give back.
    if (!billing.run || billing.servingOnly !== undefined) return;
    await this.billing.submitResolved(billing.run.requestId, submitOutcome(result));
  }

  /**
   * The ledger's run for this submit, keyed by a fresh server id (P3.1): a
   * client reusing X-Request-ID can never collide with a finished run, and an
   * Idempotency-Key replay never reaches here. Null without a video id
   * (createVideo then rejects the URL). A video already summarised brings
   * its length, so a cached submit skips the metadata pre-flight (I10).
   */
  private async billingRun(input: SubmitVideoInput): Promise<GateSubmission | null> {
    const youtubeId = extractYoutubeId(input.url);
    if (!youtubeId) return null;
    const durationSec = await this.videoRepository.findKnownDuration(youtubeId);
    return { requestId: randomUUID(), userId: input.userId, youtubeId, durationSec };
  }

  /**
   * Reserve-before-work idempotency gate. Atomic insert of a `pending`
   * placeholder on the unique-hash index — concurrent identical submits race
   * here and exactly one proceeds. `bypassCache=true` (or `cold`, which
   * implies it) is the explicit escape hatch and skips the gate entirely, as
   * does `updateToLatest`: a paid re-run that a plain submit's key must not
   * answer as a duplicate; malformed URLs also skip (createVideo
   * raises InvalidYouTubeUrlError downstream).
   */
  private async openIdempotencyGate(input: SubmitVideoInput): Promise<IdempotencyGateResult> {
    const youtubeId = extractYoutubeId(input.url);
    if (!youtubeId || input.bypassCache || input.updateToLatest || input.cold) return { kind: 'proceed', hash: null };

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
    const replayed = await this.buildDuplicateView(input.userId, existing.userVideoId.toString());
    if (replayed) return { kind: 'duplicate', ...replayed };

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

  /** Resolve a completed hit into a response view (and its update offer, D-LIB); null when the user-video is gone. */
  private async buildDuplicateView(userId: string, userVideoId: string): Promise<{ video: DuplicateVideoView; offer: UpdateOffer } | null> {
    const userVideo = await this.videoRepository.findUserVideo(userId, userVideoId);
    if (!userVideo) return null;

    const summary = await this.videoRepository.findCacheById(userVideo.videoSummaryId.toString());
    const video: DuplicateVideoView = {
      id: userVideo._id.toString(),
      videoSummaryId: userVideo.videoSummaryId.toString(),
      youtubeId: userVideo.youtubeId,
      title: summary?.title || userVideo.title,
      channel: summary?.channel || userVideo.channel,
      duration: summary?.duration || userVideo.duration,
      thumbnailUrl: summary?.thumbnailUrl || userVideo.thumbnailUrl,
      status: summary?.status || userVideo.status,
    };
    return { video, offer: updateOffer(summary) };
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
