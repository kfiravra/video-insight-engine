import { randomUUID } from 'node:crypto';
import { AppError, RunNotAllowedError } from '../utils/errors.js';
import { submitOutcome } from './billing/run-stamp.js';
import { refusalServesWithoutRun } from './billing/billing-errors.js';
import type { QueuePlan } from './queue-topology.js';
import type { FastifyBaseLogger } from 'fastify';
import type { LedgerEnforcement } from './billing/ledger-enforcement.js';
import type { ProviderConfig } from './summarizer-client.js';
import type { VideoService } from './video.service.js';

// One playlist item through the token ledger: quote + hold of its estimate on
// its own server run id, served free when the ledger will not pay but serving
// costs nothing (Q6), given back on cached / attached / error.

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
  /** An error code (`INSUFFICIENT_TOKENS`, `METADATA_UNAVAILABLE`, …; `INTERNAL_ERROR` for anything unexpected), never a raw message (I5). */
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
}

export interface ItemImporter {
  import(item: PlaylistItem, index: number): Promise<ItemOutcome>;
  /** An item after the import stopped: failed with the stopping error, unless it can be served free. */
  skip(item: PlaylistItem, error: string): Promise<ItemOutcome>;
}

/**
 * Refusals that stop the import: items are admitted in order and the first
 * one the balance cannot cover ends it, even when a later item is cheaper.
 */
const STOPPING_CODES = new Set(['INSUFFICIENT_TOKENS', 'TOO_MANY_RUNS', 'ACCOUNT_BLOCKED']);
/** …of which these still let a later item through when it costs nothing (a blocked account gets nothing). */
const SERVEABLE_STOP_CODES = new Set(['INSUFFICIENT_TOKENS', 'TOO_MANY_RUNS']);

/** The code a playlist item failure is reported with (I5): the domain code, or INTERNAL_ERROR. */
function failureCode(err: unknown): string {
  return err instanceof AppError ? err.code : 'INTERNAL_ERROR';
}

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

/** Quote + hold per item (its own server run id); give back on cached / attached / error. */
export class LedgerItemImporter implements ItemImporter {
  constructor(
    private readonly videoService: VideoService,
    private readonly enforcement: LedgerEnforcement,
    private readonly ctx: ItemImportContext,
    /** Per item, the length the up-front quote resolved (null = the pre-flight could not tell). */
    private readonly durationsSec: Array<number | null>,
    private readonly logger: FastifyBaseLogger,
  ) {}

  async import(item: PlaylistItem, index: number): Promise<ItemOutcome> {
    const durationSec = this.durationsSec[index];
    if (durationSec === null || durationSec === undefined) return this.serveFree(item, 'METADATA_UNAVAILABLE', false);
    const runId = randomUUID();
    let queuePlan: QueuePlan;
    try {
      ({ queuePlan } = await this.enforcement.admit({ requestId: runId, userId: this.ctx.userId, youtubeId: item.video_id, durationSec }));
    } catch (err) {
      if (!(err instanceof AppError)) throw err;
      const stops = STOPPING_CODES.has(err.code);
      return refusalServesWithoutRun(err) ? this.serveFree(item, err.code, stops) : failed(item, err.code, stops);
    }
    let result: CreateVideoResult;
    try {
      result = await this.createVideo(item, { requestId: runId, billingRunId: runId, queuePlan });
    } catch (err) {
      await this.enforcement.submitFailed(runId);
      return this.failedWith(item, err);
    }
    await this.enforcement.submitResolved(runId, submitOutcome(result));
    return imported(item, result);
  }

  /** An unexpected item failure: logged with its error, reported by code only (I5). */
  private failedWith(item: PlaylistItem, err: unknown): ItemOutcome {
    this.logger.warn({ err, youtubeId: item.video_id, userId: this.ctx.userId }, 'playlist_item_import_failed');
    return failed(item, failureCode(err));
  }

  /** After the import stopped, an item already in the library, completed, or running still comes free (Q6). */
  async skip(item: PlaylistItem, error: string): Promise<ItemOutcome> {
    return SERVEABLE_STOP_CODES.has(error) ? this.serveFree(item, error, false) : failed(item, error);
  }

  /** The ledger will not pay for a run: serve the item only if that costs nothing, else record `error`. */
  private async serveFree(item: PlaylistItem, error: string, stopsImport: boolean): Promise<ItemOutcome> {
    try {
      // Serve-only never dispatches, so its queue plan is never read.
      const result = await this.createVideo(item, { serveOnly: true, queuePlan: 'free' });
      const served = imported(item, result);
      return stopsImport && served.kind === 'imported' ? { ...served, stopsImport: error } : served;
    } catch (err) {
      if (err instanceof RunNotAllowedError) return failed(item, error, stopsImport);
      return this.failedWith(item, err);
    }
  }

  private async createVideo(
    item: PlaylistItem,
    extra: { requestId?: string; billingRunId?: string; serveOnly?: boolean; queuePlan: QueuePlan },
  ): Promise<CreateVideoResult> {
    const { userId, folderId, providers } = this.ctx;
    return this.videoService.createVideo(userId, videoUrl(item), { folderId, bypassCache: false, providers, ...extra });
  }
}
