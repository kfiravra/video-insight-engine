// How a submit and its dispatched run are named to the ledger.

/** How createVideo resolved a submission. */
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
