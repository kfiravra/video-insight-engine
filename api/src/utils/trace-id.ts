import { createHash, randomUUID } from 'node:crypto';

/**
 * The Langfuse trace id of one pipeline run: `sha256(runId)` as hex, first 32
 * chars. The same formula as the summarizer's `trace_id_for_run` and the SDK's
 * `Langfuse.create_trace_id(seed=runId)`, so the api can link a run to its
 * trace without asking Langfuse. A test pins the shared vector.
 */
export function traceIdForRun(runId: string): string {
  return createHash('sha256').update(runId, 'utf8').digest('hex').slice(0, 32);
}

/** A new run id (one per dispatched attempt) and its trace id. */
export function newRunIds(): { runId: string; traceId: string } {
  const runId = randomUUID();
  return { runId, traceId: traceIdForRun(runId) };
}
