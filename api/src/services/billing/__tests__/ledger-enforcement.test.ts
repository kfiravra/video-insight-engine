import { describe, it, expect, beforeAll, beforeEach, vi } from 'vitest';
import { getTestDb, mockLogger } from '../../../test/setup.js';
import { createBillingIndexes } from '../../../plugins/mongodb.js';
import { stampedRun } from '../billing-shadow.service.js';
import { insertUser, makeEnforcement, makeShadowHarness } from './billing-fixtures.js';
import type { ShadowHarness } from './billing-fixtures.js';

describe('stampedRun', () => {
  it('should key the hold on billingRunId and the cost on the dispatch request id', () => {
    expect(stampedRun({ requestId: 'client-1', billingRunId: 'run-1' })).toEqual({ runId: 'run-1', costRequestId: 'client-1' });
  });

  it('should fall back to requestId for rows stamped before P3.1', () => {
    expect(stampedRun({ requestId: 'client-1' })).toEqual({ runId: 'client-1', costRequestId: 'client-1' });
  });

  it('should name no run for an unstamped row', () => {
    expect(stampedRun(null)).toBeNull();
    expect(stampedRun({})).toBeNull();
  });
});

describe('LedgerEnforcement.runFinished', () => {
  let h: ShadowHarness;
  let userId: string;
  const stamp = { requestId: 'client-req-1', billingRunId: 'run-1' };

  beforeAll(async () => {
    await createBillingIndexes(getTestDb());
  });

  beforeEach(async () => {
    vi.clearAllMocks();
    h = makeShadowHarness(getTestDb());
    userId = await insertUser(getTestDb());
    await h.gate.admit({ requestId: 'run-1', userId, youtubeId: 'dQw4w9WgXcQ', durationSec: 600 });
    await getTestDb().collection('llm_usage').insertMany([
      { request_id: 'client-req-1', cost_usd: 0.25 },
      { request_id: 'run-1', cost_usd: 99 },
    ]);
  });

  it('should settle the stamped run with the cost of its dispatch request id', async () => {
    const enforcement = makeEnforcement(getTestDb(), h, { findCacheById: async () => stamp });

    await enforcement.runFinished('vs-1', 'completed');

    const debits = await getTestDb().collection('ledgerEntries').find({ type: 'debit' }).toArray();
    expect(debits.map((d) => [d.source.ref, d.costUsd])).toEqual([['run-1', 0.25]]);
  });

  it('should release a failed run', async () => {
    const enforcement = makeEnforcement(getTestDb(), h, { findCacheById: async () => stamp });

    await enforcement.runFinished('vs-1', 'failed');

    expect((await h.billing.summary(userId)).free.available).toBe(true);
  });

  it('should log instead of throwing when the ledger fails, so the status relay never fails', async () => {
    const enforcement = makeEnforcement(getTestDb(), h, { findCacheById: async () => { throw new Error('mongo down'); } });

    await expect(enforcement.runFinished('vs-1', 'completed')).resolves.toBeUndefined();
    expect(mockLogger.warn).toHaveBeenCalledWith(expect.objectContaining({ videoSummaryId: 'vs-1' }), 'billing_run_finish_failed');
  });
});

describe('BillingGate.admit — hold scope (W3)', () => {
  beforeAll(async () => {
    await createBillingIndexes(getTestDb());
  });

  it('should not let another user ride on an open hold by reusing its run id', async () => {
    const h = makeShadowHarness(getTestDb());
    const owner = await insertUser(getTestDb());
    const intruder = await insertUser(getTestDb());
    await h.gate.admit({ requestId: 'run-shared', userId: owner, youtubeId: 'dQw4w9WgXcQ', durationSec: 600 });

    await expect(
      h.gate.admit({ requestId: 'run-shared', userId: intruder, youtubeId: 'dQw4w9WgXcQ', durationSec: 600 }),
    ).rejects.toMatchObject({ code: 'HOLD_CONFLICT', status: 409 });
  });
});
