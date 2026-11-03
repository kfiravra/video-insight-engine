import { describe, expect, it } from 'vitest';
import { newRunIds, traceIdForRun } from './trace-id.js';

// The vector the summarizer's llm-common test pins too (test_observability_ids.py).
const RUN_ID = '6f1c2d3e-4a5b-4c6d-8e7f-001122334455';
const RUN_TRACE_ID = '9374a382c89f81a18612f80075e88270';

describe('traceIdForRun', () => {
  it('should be the truncated sha256 of the run id when given the shared vector', () => {
    expect(traceIdForRun(RUN_ID)).toBe(RUN_TRACE_ID);
  });

  it('should be 32 lowercase hex chars when given any run id', () => {
    expect(traceIdForRun('anything')).toMatch(/^[0-9a-f]{32}$/);
  });
});

describe('newRunIds', () => {
  it('should pair a fresh uuid with its trace id when called', () => {
    const { runId, traceId } = newRunIds();
    expect(runId).toMatch(/^[0-9a-f-]{36}$/);
    expect(traceId).toBe(traceIdForRun(runId));
    expect(newRunIds().runId).not.toBe(runId);
  });
});
