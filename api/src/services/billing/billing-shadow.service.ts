import { ObjectId } from 'mongodb';
import { SERVES_WITHOUT_RUN, classifyShadow, errorReason, rejectionReason } from './shadow-policy.js';
import type { FastifyBaseLogger } from 'fastify';
import type { BillingShadowDecision, BillingShadowPath } from '@vie/types';
import type { BillingGate, GateSubmission } from './billing-gate.js';
import type { BillingService } from './billing.service.js';
import type { ShadowRunner } from './shadow-runner.js';
import type { ShadowReason } from './shadow-policy.js';
import type {
  BillingShadowDocument,
  BillingShadowRepository,
  ShadowNewDecision,
} from '../../repositories/billing-shadow.repository.js';
import type { LlmUsageRepository } from '../../repositories/llm-usage.repository.js';

// P1.7 shadow mode: every submit and terminal status also writes the minutes
// ledger and a `billingShadow` row next to the old gate's decision, but never
// decides. Every public method returns at once (the work is queued on the
// ShadowRunner, one chain per requestId) and never throws.

/** How createVideo resolved a submission the old gate admitted. */
export type SubmitOutcome = 'created' | 'cached' | 'attached';

/** Name a createVideo result's outcome (served from cache, attached to another run, or a new run). */
export function submitOutcome(result: { cached?: boolean; attached?: boolean }): SubmitOutcome {
  if (result.cached) return 'cached';
  if (result.attached) return 'attached';
  return 'created';
}

/**
 * The cache row's dispatch stamp names the run: `billingRunId` keys its hold
 * (P3.1), `requestId` its llm_usage rows (P0 B2). Rows stamped before P3.1
 * carry only `requestId`, which then keyed the hold too.
 */
export interface DispatchStampReader {
  findCacheById(videoSummaryId: string): Promise<{ requestId?: string; billingRunId?: string } | null>;
}

/** The hold key and the llm_usage key of the run a terminal status is about; null when nothing was stamped. */
export function stampedRun(stamp: { requestId?: string; billingRunId?: string } | null): { runId: string; costRequestId: string } | null {
  const runId = stamp?.billingRunId ?? stamp?.requestId;
  if (!runId) return null;
  return { runId, costRequestId: stamp?.requestId ?? runId };
}

export interface BillingShadowDeps {
  /** `BILLING_SHADOW_ENABLED`; off = every method is a no-op. */
  enabled: boolean;
  gate: BillingGate;
  billing: BillingService;
  rows: BillingShadowRepository;
  llmUsage: LlmUsageRepository;
  dispatchStamps: DispatchStampReader;
  runner: ShadowRunner;
  logger: FastifyBaseLogger;
}

const RUN_DECIDED_BY_OLD_GATE: BillingShadowDecision = { admitted: true, charged: true };

export class BillingShadowService {
  constructor(private readonly deps: BillingShadowDeps) {}

  /** The old gate admitted the submit: quote + hold for `requestId`. */
  submitAdmitted(submission: GateSubmission): void {
    this.schedule(submission.requestId, 'hold', () => this.recordAdmission(submission));
  }

  /** The old gate refused the submit (daily cap): quote only. */
  submitRefused(submission: GateSubmission): void {
    this.schedule(submission.requestId, 'quote', () => this.recordRefusal(submission));
  }

  /** createVideo resolved; cached and attached runs give the hold back (Q6). */
  submitResolved(requestId: string, outcome: SubmitOutcome): void {
    if (outcome === 'created') return;
    this.schedule(requestId, outcome, () => this.recordRelease(requestId, outcome));
  }

  /** createVideo threw after the old gate admitted: the hold goes back. */
  submitFailed(requestId: string): void {
    this.schedule(requestId, 'submit_error', () => this.recordRelease(requestId, 'submit_error'));
  }

  /** Terminal pipeline status: completed → settle with the run's $, failed → release. */
  runFinished(videoSummaryId: string, status: 'completed' | 'failed'): void {
    this.schedule(`video:${videoSummaryId}`, 'stamp', async () => {
      const run = stampedRun(await this.deps.dispatchStamps.findCacheById(videoSummaryId));
      if (!run) return;
      // On the run's own chain, so it waits for a hold still in flight.
      this.schedule(run.runId, status, () => this.recordTerminal(run.runId, status, run.costRequestId));
    });
  }

  /** Resolve once every queued shadow step has settled (tests, shutdown). */
  async drain(): Promise<void> {
    await this.deps.runner.drain();
  }

  private schedule(key: string, label: string, step: () => Promise<void>): void {
    if (!this.deps.enabled) return;
    try {
      this.deps.runner.enqueue(key, label, step);
    } catch (err) {
      this.deps.logger.warn({ err, key, step: label }, 'billing shadow step could not be queued');
    }
  }

  private async recordAdmission(submission: GateSubmission): Promise<void> {
    const prior = await this.deps.rows.findByRequestId(submission.requestId);
    const newDecision = await this.admissionDecision(submission);
    await this.writeRow(submission, prior ? 'retry' : 'created', RUN_DECIDED_BY_OLD_GATE, newDecision);
  }

  private async admissionDecision(submission: GateSubmission): Promise<ShadowNewDecision> {
    try {
      const admission = await this.deps.gate.admit(submission);
      if (admission.admitted) return { admitted: true, charged: true, split: admission.hold.split };
      return { admitted: false, charged: false, reason: rejectionReason(admission.rejection, admission.quote.minutes) };
    } catch (err) {
      return { admitted: false, charged: false, reason: this.reasonOrLog(err, submission.requestId) };
    }
  }

  private async recordRefusal(submission: GateSubmission): Promise<void> {
    let newDecision: ShadowNewDecision;
    try {
      const { decision, minutes } = await this.deps.gate.quote(submission);
      newDecision = decision.ok
        ? { admitted: true, charged: false, split: decision.split }
        : { admitted: false, charged: false, reason: rejectionReason(decision, minutes) };
    } catch (err) {
      newDecision = { admitted: false, charged: false, reason: this.reasonOrLog(err, submission.requestId) };
    }
    await this.writeRow(submission, 'refused', { admitted: false, charged: false }, newDecision);
  }

  private async recordRelease(requestId: string, why: 'cached' | 'attached' | 'submit_error'): Promise<void> {
    const row = await this.deps.rows.findByRequestId(requestId);
    if (!row) return;
    const outcome = row.newDecision.admitted ? await this.deps.billing.release(requestId, why) : null;
    const newDecision: ShadowNewDecision = {
      ...row.newDecision,
      ...(why !== 'submit_error' && this.servedFree(row.newDecision) && { admitted: true }),
      charged: outcome?.status === 'already_debited',
      ...(why === 'submit_error' && { reason: 'submit_error' }),
    };
    const path: BillingShadowPath = why === 'submit_error' ? 'failed' : why;
    await this.writeRow(this.submissionOf(row), path, { admitted: true, charged: false }, newDecision);
  }

  private async recordTerminal(requestId: string, status: 'completed' | 'failed', costRequestId: string): Promise<void> {
    const row = await this.deps.rows.findByRequestId(requestId);
    if (!row) return;
    if (!row.newDecision.admitted) {
      await this.writeRow(this.submissionOf(row), status, RUN_DECIDED_BY_OLD_GATE, row.newDecision);
      return;
    }
    const costUsd = await this.deps.llmUsage.sumCostByRequestId(costRequestId);
    const outcome = status === 'completed'
      ? await this.deps.billing.settle(requestId, { costUsd })
      : await this.deps.billing.release(requestId, 'failed', { costUsd });
    const firstTime = outcome.status === 'debited' || outcome.status === 'released';
    const charged = outcome.status === 'debited' || outcome.status === 'already_debited';
    await this.writeRow(
      this.submissionOf(row),
      firstTime ? status : 'retry',
      RUN_DECIDED_BY_OLD_GATE,
      { ...row.newDecision, charged },
    );
  }

  private async writeRow(
    submission: GateSubmission,
    path: BillingShadowPath,
    oldDecision: BillingShadowDecision,
    newDecision: ShadowNewDecision,
  ): Promise<void> {
    const { requestId, userId, youtubeId } = submission;
    const { classification, policyRef } = classifyShadow({ path, oldDecision, newDecision });
    if (classification === 'disagree') {
      this.deps.logger.warn({ requestId, path, oldDecision, newDecision }, 'billing shadow disagrees with the old gate');
    }
    await this.deps.rows.upsert({
      requestId, userId: new ObjectId(userId), youtubeId, path, oldDecision, newDecision, classification, policyRef,
    });
  }

  /** Enforcement serves a cached / attached video the ledger refused to run for free (Q6) — mirror it. */
  private servedFree(decision: ShadowNewDecision): boolean {
    return !decision.admitted && decision.reason !== undefined && SERVES_WITHOUT_RUN.has(decision.reason);
  }

  private submissionOf(row: BillingShadowDocument): GateSubmission {
    return { requestId: row.requestId, userId: row.userId.toHexString(), youtubeId: row.youtubeId };
  }

  private reasonOrLog(err: unknown, requestId: string): ShadowReason {
    const reason = errorReason(err);
    if (reason) return reason;
    this.deps.logger.warn({ err, requestId }, 'billing shadow could not reach a decision');
    return 'shadow_error';
  }
}
