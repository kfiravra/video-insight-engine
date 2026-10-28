import { rejectionToError } from './billing-errors.js';
import { stampedRun } from './run-stamp.js';
import type { FastifyBaseLogger } from 'fastify';
import type { HoldView } from '@vie/types';
import type { AllocationRejection } from './allocation.js';
import type { BillingGate, GateSubmission } from './billing-gate.js';
import type { DispatchStampReader, SubmitOutcome } from './run-stamp.js';
import type { BillingService, PlaylistQuote } from './billing.service.js';
import type { LlmUsageRepository } from '../../repositories/llm-usage.repository.js';

// The minutes ledger decides every submit, on the request path (P3.1; the
// only gate since P3.2): quote → hold before createVideo, a refusal thrown
// (402/422/403/429, METADATA_UNAVAILABLE fails closed), every give-back
// awaited, the terminal status settled or released.

export interface LedgerEnforcementDeps {
  billing: BillingService;
  gate: BillingGate;
  llmUsage: LlmUsageRepository;
  dispatchStamps: DispatchStampReader;
  logger: FastifyBaseLogger;
}

export interface PlaylistItemToPrice {
  youtubeId: string;
  /** Seconds from the playlist extract; null asks the metadata pre-flight. */
  durationSec: number | null;
}

/** The playlist as quoted up front (items of known length), each item's length resolved (null = unknown). */
export interface PlaylistPlan {
  quote: PlaylistQuote;
  durationsSec: Array<number | null>;
}

export class LedgerEnforcement {
  constructor(private readonly deps: LedgerEnforcementDeps) {}

  /** Quote + hold before createVideo. Throws the refusal, or MetadataUnavailableError (fails closed). */
  async admit(submission: GateSubmission): Promise<HoldView> {
    const admission = await this.deps.gate.admit(submission);
    if (!admission.admitted) throw rejectionToError(admission.rejection);
    return admission.hold;
  }

  /** createVideo resolved: a cached or attached video gives its hold back at once (Q6). */
  async submitResolved(runId: string, outcome: SubmitOutcome): Promise<void> {
    if (outcome !== 'created') await this.giveBack(runId, outcome);
  }

  /** createVideo threw: the hold goes back before the error surfaces. */
  async submitFailed(runId: string): Promise<void> {
    await this.giveBack(runId, 'submit_error');
  }

  /**
   * Terminal status of a dispatched run: completed → settle with the run's
   * LLM $, failed → release, cached (the summarizer answered from its
   * response cache, no run happened — I3) → release like a cached submit.
   * Never throws — the status relay must not fail on it; a hold left open is
   * settled or released by the repair job.
   */
  async runFinished(videoSummaryId: string, outcome: 'completed' | 'failed' | 'cached'): Promise<void> {
    try {
      const run = stampedRun(await this.deps.dispatchStamps.findCacheById(videoSummaryId));
      if (!run) return;
      const costUsd = await this.deps.llmUsage.sumRunCost(run.costRequestId, videoSummaryId);
      if (outcome === 'completed') await this.deps.billing.settle(run.runId, { costUsd });
      else await this.deps.billing.release(run.runId, outcome, { costUsd });
    } catch (err) {
      this.deps.logger.warn({ err, videoSummaryId, outcome }, 'billing_run_finish_failed');
    }
  }

  /**
   * Quote a whole playlist up front, for the import summary ("needs 340 min,
   * you have 120"). Only a blocked account is refused here: any other
   * refusal can still serve cached or attachable items at no cost (Q6), so
   * the import goes item by item (see PlaylistService).
   */
  async planPlaylist(userId: string, items: PlaylistItemToPrice[]): Promise<PlaylistPlan> {
    const durationsSec = await Promise.all(items.map((item) => this.durationOrNull(item)));
    const known = durationsSec.filter((sec): sec is number => sec !== null);
    const quote = await this.deps.billing.quoteMany(userId, known);
    const blocked = quote.decisions.find((decision): decision is AllocationRejection => !decision.ok && decision.reason === 'blocked');
    if (blocked) throw rejectionToError(blocked);
    return { quote, durationsSec };
  }

  private async durationOrNull(item: PlaylistItemToPrice): Promise<number | null> {
    try {
      return await this.deps.gate.durationOf(item.youtubeId, item.durationSec);
    } catch (err) {
      this.deps.logger.warn({ err, youtubeId: item.youtubeId }, 'playlist_item_duration_unavailable');
      return null;
    }
  }

  private async giveBack(runId: string, reason: 'cached' | 'attached' | 'submit_error'): Promise<void> {
    try {
      await this.deps.billing.release(runId, reason);
    } catch (err) {
      // The video is already served (or the submit already failed); the repair
      // job releases a hold whose run was never stamped after the max age.
      this.deps.logger.warn({ err, runId, reason }, 'billing_hold_give_back_failed');
    }
  }
}
