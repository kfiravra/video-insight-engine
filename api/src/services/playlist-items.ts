import { randomUUID } from 'node:crypto';
import { AppError, DailyLimitReachedError, RunNotAllowedError } from '../utils/errors.js';
import { submitOutcome } from './billing/billing-shadow.service.js';
import { refusalServesWithoutRun } from './billing/shadow-policy.js';
import type { FastifyBaseLogger } from 'fastify';
import type { UserTier } from '@vie/types';
import type { BillingShadowService } from './billing/billing-shadow.service.js';
import type { GateSubmission } from './billing/billing-gate.js';
import type { LedgerEnforcement } from './billing/ledger-enforcement.js';
import type { CostMonitorService } from './cost-monitor.service.js';
import type { ProviderConfig } from './summarizer-client.js';
import type { VideoService } from './video.service.js';

// One playlist item through the gate that decides it — the old dollar gate
// (with the shadow ledger beside it) or, with BILLING_LEDGER_ENFORCE, the
// minutes ledger. P3.2 deletes DollarGateItemImporter.

export interface PlaylistItem {
  video_id: string;
  title: string;
  position: number;
  duration: number | null;
}

export interface ImportedVideo {
  id: string;
  videoSummaryId: string;
  youtubeId: string;
  title: string | null;
  status: string;
  position: number;
}

export interface FailedVideoImport {
  youtubeId: string;
  title: string;
  position: number;
  error: string;
}

/**
 * `stopsImport` names the error every later item gets (prefix semantics) — set
 * by a refusal later items cannot do better on, even when this item itself
 * was served free.
 */
export type ItemOutcome =
  | { kind: 'imported'; video: ImportedVideo; cached: boolean; stopsImport?: string }
  | { kind: 'failed'; failure: FailedVideoImport; stopsImport: boolean };

export interface ItemImportContext {
  userId: string;
  folderId: string;
  providers?: ProviderConfig;
  tier: UserTier;
}

export interface ItemImporter {
  import(item: PlaylistItem, index: number): Promise<ItemOutcome>;
  /** An item after the import stopped: failed with the stopping error (enforcing: unless it can be served free). */
  skip(item: PlaylistItem, error: string): Promise<ItemOutcome>;
}

/** Refusals after which later items cannot do better, so the import stops there. */
const STOPPING_CODES = new Set(['INSUFFICIENT_MINUTES', 'TOO_MANY_RUNS', 'ACCOUNT_BLOCKED']);
/** …of which these still let a later item through when it costs nothing (a blocked account gets nothing). */
const SERVEABLE_STOP_CODES = new Set(['INSUFFICIENT_MINUTES', 'TOO_MANY_RUNS']);

function failed(item: PlaylistItem, error: string, stopsImport = false): ItemOutcome {
  return { kind: 'failed', failure: { youtubeId: item.video_id, title: item.title, position: item.position, error }, stopsImport };
}

function videoUrl(item: PlaylistItem): string {
  return `https://www.youtube.com/watch?v=${item.video_id}`;
}

type CreateVideoResult = Awaited<ReturnType<VideoService['createVideo']>>;

function imported(item: PlaylistItem, result: CreateVideoResult): ItemOutcome {
  const video: ImportedVideo = {
    id: result.video.id,
    videoSummaryId: result.video.videoSummaryId,
    youtubeId: item.video_id,
    title: result.video.title ?? item.title,
    status: result.video.status,
    position: item.position,
  };
  return { kind: 'imported', video, cached: result.cached === true };
}

/** BILLING_LEDGER_ENFORCE on: quote + hold per item (its own server run id), give back on cached / attached / error. */
export class LedgerItemImporter implements ItemImporter {
  constructor(
    private readonly videoService: VideoService,
    private readonly enforcement: LedgerEnforcement,
    private readonly ctx: ItemImportContext,
    /** Per item, the length the up-front quote resolved (null = the pre-flight could not tell). */
    private readonly durationsSec: Array<number | null>,
  ) {}

  async import(item: PlaylistItem, index: number): Promise<ItemOutcome> {
    const durationSec = this.durationsSec[index];
    if (durationSec === null || durationSec === undefined) return this.serveFree(item, 'METADATA_UNAVAILABLE', false);
    const runId = randomUUID();
    try {
      await this.enforcement.admit({ requestId: runId, userId: this.ctx.userId, youtubeId: item.video_id, durationSec });
    } catch (err) {
      if (!(err instanceof AppError)) throw err;
      const stops = STOPPING_CODES.has(err.code);
      return refusalServesWithoutRun(err) ? this.serveFree(item, err.code, stops) : failed(item, err.code, stops);
    }
    let result: CreateVideoResult;
    try {
      result = await this.createVideo(item, { requestId: runId, billingRunId: runId });
    } catch (err) {
      await this.enforcement.submitFailed(runId);
      return failed(item, err instanceof Error ? err.message : String(err));
    }
    await this.enforcement.submitResolved(runId, submitOutcome(result));
    return imported(item, result);
  }

  /** After the import stopped, an item already in the library, completed, or running still comes free (Q6). */
  async skip(item: PlaylistItem, error: string): Promise<ItemOutcome> {
    return SERVEABLE_STOP_CODES.has(error) ? this.serveFree(item, error, false) : failed(item, error);
  }

  /** The ledger will not pay for a run: serve the item only if that costs nothing, else record `error`. */
  private async serveFree(item: PlaylistItem, error: string, stopsImport: boolean): Promise<ItemOutcome> {
    try {
      const result = await this.createVideo(item, { serveOnly: true });
      const served = imported(item, result);
      return stopsImport && served.kind === 'imported' ? { ...served, stopsImport: error } : served;
    } catch (err) {
      if (err instanceof RunNotAllowedError) return failed(item, error, stopsImport);
      return failed(item, err instanceof Error ? err.message : String(err));
    }
  }

  private async createVideo(
    item: PlaylistItem,
    extra: { requestId?: string; billingRunId?: string; serveOnly?: boolean },
  ): Promise<CreateVideoResult> {
    const { userId, folderId, providers, tier } = this.ctx;
    return this.videoService.createVideo(userId, videoUrl(item), { folderId, bypassCache: false, providers, tier, ...extra });
  }
}

/** BILLING_LEDGER_ENFORCE off: the old per-item dollar reservation, with the shadow ledger told about each item. */
export class DollarGateItemImporter implements ItemImporter {
  constructor(
    private readonly videoService: VideoService,
    private readonly logger: FastifyBaseLogger,
    private readonly ctx: ItemImportContext,
    private readonly costMonitorService?: CostMonitorService,
    private readonly billingShadow?: BillingShadowService,
  ) {}

  async import(item: PlaylistItem): Promise<ItemOutcome> {
    const shadow = this.shadowSubmission(item);
    let reservation = null;
    let admitted = false;
    try {
      if (this.costMonitorService) reservation = await this.costMonitorService.reserveUserCost(this.ctx.userId, this.ctx.tier);
      admitted = true;
      if (shadow) this.billingShadow?.submitAdmitted(shadow);
      const { folderId, providers, tier } = this.ctx;
      const result = await this.videoService.createVideo(this.ctx.userId, videoUrl(item), {
        folderId, bypassCache: false, providers, tier,
        ...(shadow && { requestId: shadow.requestId, billingRunId: shadow.requestId }),
      });
      if (shadow) this.billingShadow?.submitResolved(shadow.requestId, submitOutcome(result));
      // Cached / attached runs no pipeline for this caller — refund. A failed
      // refund must not fail the import: the video exists, and the catch below
      // would refund a second time.
      if (result.cached || result.attached) await this.refund(reservation, item, 'cached/attached-video refund failed during playlist import');
      return imported(item, result);
    } catch (err) {
      return this.failure(err, item, shadow, admitted, reservation);
    }
  }

  async skip(item: PlaylistItem, error: string): Promise<ItemOutcome> {
    const shadow = this.shadowSubmission(item);
    if (shadow) this.billingShadow?.submitRefused(shadow);
    return failed(item, error);
  }

  private async failure(
    err: unknown,
    item: PlaylistItem,
    shadow: GateSubmission | null,
    admitted: boolean,
    reservation: Awaited<ReturnType<CostMonitorService['reserveUserCost']>> | null,
  ): Promise<ItemOutcome> {
    if (shadow && admitted) this.billingShadow?.submitFailed(shadow.requestId);
    if (shadow && err instanceof DailyLimitReachedError) this.billingShadow?.submitRefused(shadow);
    // A failed refund must not abort the loop — the remaining videos would never be processed.
    if (reservation) await this.refund(reservation, item, 'failed-video refund failed during playlist import');
    if (err instanceof DailyLimitReachedError) {
      this.logger.info({ userId: this.ctx.userId }, 'Playlist import stopped at per-user cost cap');
      return failed(item, 'DAILY_LIMIT_REACHED', true);
    }
    const errorMessage = err instanceof Error ? err.message : String(err);
    this.logger.warn({ youtubeId: item.video_id, error: errorMessage }, 'Failed to import video');
    return failed(item, errorMessage);
  }

  private async refund(
    reservation: Awaited<ReturnType<CostMonitorService['reserveUserCost']>> | null,
    item: PlaylistItem,
    message: string,
  ): Promise<void> {
    if (!this.costMonitorService) return;
    await this.costMonitorService.refundReservation(reservation).catch((refundErr) => {
      this.logger.warn({ err: refundErr, userId: this.ctx.userId, youtubeId: item.video_id }, message);
    });
  }

  /** With the shadow, every item gets its own run id, stamped by its dispatch, so its terminal status settles it. */
  private shadowSubmission(item: PlaylistItem): GateSubmission | null {
    if (!this.billingShadow) return null;
    return { requestId: randomUUID(), userId: this.ctx.userId, youtubeId: item.video_id, durationSec: item.duration };
  }
}
