import { AccountBlockedError } from '../../utils/errors.js';
import { queuePlanFor } from '../queue-topology.js';
import { rejectionToError } from './billing-errors.js';
import { stampedRun } from './run-stamp.js';
import type { FastifyBaseLogger } from 'fastify';
import type { HoldView, VideoBilling } from '@vie/types';
import type { QueuePlan } from '../queue-topology.js';
import type { BillingGate, GateSubmission } from './billing-gate.js';
import type { DispatchStampReader, SubmitOutcome } from './run-stamp.js';
import type { BillingService, HoldOutcomeStatus, PlaylistQuote } from './billing.service.js';
import type { LlmUsageRepository } from '../../repositories/llm-usage.repository.js';

// The token ledger decides every submit, on the request path (P3.1; the
// only gate since P3.2): quote → hold of the estimate before createVideo, a
// refusal thrown (402/422/403/429, METADATA_UNAVAILABLE fails closed), every
// give-back awaited; the run settles on its real cost when the summarizer
// reports it (run-cost), a failed or cache-hit status releases it.

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

/** A held submit and the queue plan its run is published at (Pro accounts: `paid`). */
export interface AdmittedRun {
  hold: HoldView;
  queuePlan: QueuePlan;
}

/** What a run-cost callback did: the settle's outcome, or `ignored` when the row no longer names that run. */
export type RunCostStatus = HoldOutcomeStatus | 'ignored';

/** The playlist as quoted up front (items of known length), each item's length resolved (null = unknown). */
export interface PlaylistPlan {
  quote: PlaylistQuote;
  durationsSec: Array<number | null>;
}

export class LedgerEnforcement {
  constructor(private readonly deps: LedgerEnforcementDeps) {}

  /** Quote + hold before createVideo. Throws the refusal, or MetadataUnavailableError (fails closed). */
  async admit(submission: GateSubmission): Promise<AdmittedRun> {
    const admission = await this.deps.gate.admit(submission);
    if (!admission.admitted) throw rejectionToError(admission.rejection);
    return { hold: admission.hold, queuePlan: queuePlanFor(admission.quote.planId) };
  }

  /**
   * A blocked account submits nothing, cached included (D-BLOCK) — also on the
   * paths that never quote: an idempotency replay, a playlist whose items have
   * no known length. Its library stays readable; reads never come here.
   */
  async refuseIfBlocked(userId: string): Promise<void> {
    if ((await this.deps.billing.summary(userId)).blocked) throw new AccountBlockedError();
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
   * Terminal status of a dispatched run. `completed` settles nothing: the run
   * is charged on its real cost when the summarizer's run-cost callback
   * arrives (or by the repair job). failed → release; cached (the summarizer
   * answered from its response cache, no run happened — I3) → release like a
   * cached submit. Never throws — the status relay must not fail on it; a
   * hold left open is settled or released by the repair job.
   */
  async runFinished(videoSummaryId: string, outcome: 'completed' | 'failed' | 'cached'): Promise<void> {
    if (outcome === 'completed') return;
    try {
      const run = stampedRun(await this.deps.dispatchStamps.findCacheById(videoSummaryId));
      if (!run) return;
      const costUsd = await this.deps.llmUsage.sumRunCost(run.costRequestId, videoSummaryId);
      await this.deps.billing.release(run.runId, outcome, { costUsd });
    } catch (err) {
      this.deps.logger.warn({ err, videoSummaryId, outcome }, 'billing_run_finish_failed');
    }
  }

  /**
   * The summarizer's total cost of a completed run (POST /internal/run-cost):
   * settle the hold of the run the row is stamped with. A `billingRunId` that
   * no longer matches the stamp means a newer dispatch replaced it; that call
   * settles nothing (`ignored`) and the repair job deals with the old hold.
   */
  async runCosted(videoSummaryId: string, billingRunId: string | null, costUsd: number): Promise<RunCostStatus> {
    const run = stampedRun(await this.deps.dispatchStamps.findCacheById(videoSummaryId));
    if (!run) return 'no_hold';
    if (billingRunId !== null && billingRunId !== run.runId) {
      this.deps.logger.warn({ videoSummaryId, billingRunId, stamped: run.runId }, 'billing_run_cost_stamp_mismatch');
      return 'ignored';
    }
    return (await this.deps.billing.settleActual(run.runId, costUsd)).status;
  }

  /** The video detail's `billing`: what this user's run on the row's stamp holds or cost. */
  async videoBilling(userId: string, videoSummaryId: string): Promise<VideoBilling> {
    const run = stampedRun(await this.deps.dispatchStamps.findCacheById(videoSummaryId));
    return this.deps.billing.videoBilling(run?.runId ?? null, userId);
  }

  /**
   * Quote a whole playlist up front, for the import summary ("needs ≈ 9,600
   * tokens, you have 4,200"). Only a blocked account is refused here, before any
   * pre-flight and whatever the items: any other refusal can still serve
   * cached or attachable items at no cost (Q6), so the import goes item by
   * item (see PlaylistService).
   */
  async planPlaylist(userId: string, items: PlaylistItemToPrice[]): Promise<PlaylistPlan> {
    await this.refuseIfBlocked(userId);
    const durationsSec = await Promise.all(items.map((item) => this.durationOrNull(item)));
    const known = durationsSec.filter((sec): sec is number => sec !== null);
    const quote = await this.deps.billing.quoteMany(userId, known);
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
