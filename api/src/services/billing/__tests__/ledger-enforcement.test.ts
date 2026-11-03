import { describe, it, expect, beforeAll, beforeEach, vi } from 'vitest';
import { getTestDb, mockLogger } from '../../../test/setup.js';
import { createBillingIndexes } from '../../../plugins/mongodb.js';
import { AccountBlockedError, InsufficientTokensError } from '../../../utils/errors.js';
import { stampedRun } from '../run-stamp.js';
import { adminCtx, insertUser, makeBillingHarness, makeEnforcement } from './billing-fixtures.js';
import type { BillingHarness } from './billing-fixtures.js';

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

describe('LedgerEnforcement', () => {
  let h: BillingHarness;
  let userId: string;
  const stamp = { requestId: 'client-req-1', billingRunId: 'run-1' };
  const ledger = (filter: Record<string, unknown> = {}) => getTestDb().collection('ledgerEntries').find(filter).toArray();

  beforeAll(async () => {
    await createBillingIndexes(getTestDb());
  });

  beforeEach(async () => {
    vi.clearAllMocks();
    h = makeBillingHarness(getTestDb());
    userId = await insertUser(getTestDb());
  });

  describe('admit', () => {
    it('should hold the estimate of the video\'s length (a 10-minute video: 840 tokens)', async () => {
      const hold = await makeEnforcement(getTestDb(), h).admit({ requestId: 'run-1', userId, youtubeId: 'dQw4w9WgXcQ' });

      expect(hold).toMatchObject({ requestId: 'run-1', tokens: 840, durationSec: 600 });
      expect(h.metadata.getVideoMetadata).toHaveBeenCalledWith('dQw4w9WgXcQ');
    });

    it('should throw the refusal when the balance does not cover the estimate', async () => {
      const admit = makeEnforcement(getTestDb(), h).admit({ requestId: 'run-1', userId, youtubeId: 'dQw4w9WgXcQ', durationSec: 20 * 60 });

      await expect(admit).rejects.toBeInstanceOf(InsufficientTokensError);
      await expect(admit).rejects.toMatchObject({ needed: 1020, available: 1000 });
    });
  });

  describe('runFinished', () => {
    beforeEach(async () => {
      await h.gate.admit({ requestId: 'run-1', userId, youtubeId: 'dQw4w9WgXcQ', durationSec: 600 });
      await getTestDb().collection('llm_usage').insertMany([
        { request_id: 'client-req-1', video_summary_id: 'vs-1', cost_usd: 0.25 },
        { request_id: 'run-1', video_summary_id: 'vs-1', cost_usd: 99 },
        // The client reused its X-Request-ID on another video: not this run's spend (I4).
        { request_id: 'client-req-1', video_summary_id: 'vs-other', cost_usd: 1.5 },
      ]);
    });

    it('should settle a completed run on the llm_usage cost of its dispatch request id', async () => {
      const enforcement = makeEnforcement(getTestDb(), h, { findCacheById: async () => stamp });

      await enforcement.runFinished('vs-1', 'completed');

      const debits = await ledger({ type: 'debit' });
      expect(debits.map((d) => [d.source.ref, d.tokens, d.costUsd, d.mirrorsHold])).toEqual([['run-1', -750, 0.25, true]]);
      expect(await h.billing.summary(userId)).toMatchObject({ balance: 250, holds: [] });
    });

    it('should release a failed run in full', async () => {
      const enforcement = makeEnforcement(getTestDb(), h, { findCacheById: async () => stamp });

      await enforcement.runFinished('vs-1', 'failed');

      expect(await ledger({ type: 'debit' })).toEqual([]);
      expect(await h.billing.summary(userId)).toMatchObject({ balance: 1000, holds: [] });
    });

    it('should release a run the summarizer answered from its response cache, with no debit (I3)', async () => {
      const enforcement = makeEnforcement(getTestDb(), h, { findCacheById: async () => stamp });

      await enforcement.runFinished('vs-1', 'cached');

      const entries = await ledger({ 'source.ref': 'run-1' });
      expect(entries.map((e) => e.type)).toEqual(['hold', 'release']);
      expect((await h.billing.summary(userId)).balance).toBe(1000);
    });

    it('should do nothing for a row no dispatch stamped', async () => {
      const enforcement = makeEnforcement(getTestDb(), h, { findCacheById: async () => ({}) });

      await enforcement.runFinished('vs-1', 'completed');

      expect((await h.billing.summary(userId)).holds).toHaveLength(1);
    });

    it('should log instead of throwing when the ledger fails, so the status relay never fails', async () => {
      const enforcement = makeEnforcement(getTestDb(), h, { findCacheById: async () => { throw new Error('mongo down'); } });

      await expect(enforcement.runFinished('vs-1', 'completed')).resolves.toBeUndefined();
      expect(mockLogger.warn).toHaveBeenCalledWith(expect.objectContaining({ videoSummaryId: 'vs-1' }), 'billing_run_finish_failed');
    });
  });

  describe('submit outcomes', () => {
    beforeEach(async () => {
      await h.gate.admit({ requestId: 'run-1', userId, youtubeId: 'dQw4w9WgXcQ', durationSec: 600 });
    });

    it.each([
      ['a created run keeps its hold', (e: ReturnType<typeof makeEnforcement>) => e.submitResolved('run-1', 'created'), 160],
      ['a cached video gives the hold back', (e: ReturnType<typeof makeEnforcement>) => e.submitResolved('run-1', 'cached'), 1000],
      ['an attached video gives the hold back', (e: ReturnType<typeof makeEnforcement>) => e.submitResolved('run-1', 'attached'), 1000],
      ['a failed submit gives the hold back', (e: ReturnType<typeof makeEnforcement>) => e.submitFailed('run-1'), 1000],
    ])('%s', async (_label, resolve, balance) => {
      await resolve(makeEnforcement(getTestDb(), h));

      expect((await h.billing.summary(userId)).balance).toBe(balance);
    });
  });

  describe('planPlaylist', () => {
    it('should quote the items of known length together, asking the pre-flight for the rest', async () => {
      h.metadata.getVideoMetadata.mockImplementation(async (id: string) => {
        if (id === 'broken00000') throw new Error('yt-dlp timeout');
        return { duration: 300 };
      });
      const items = [
        { youtubeId: 'aaaaaaaaaaa', durationSec: 600 },
        { youtubeId: 'bbbbbbbbbbb', durationSec: null },
        { youtubeId: 'broken00000', durationSec: null },
      ];

      const plan = await makeEnforcement(getTestDb(), h).planPlaylist(userId, items);

      expect(plan.durationsSec).toEqual([600, 300, null]);
      expect(plan.quote).toMatchObject({ neededTokens: 840 + 750, availableTokens: 1000 });
      expect(plan.quote.decisions.map((d) => (d.ok ? 'ok' : d.reason))).toEqual(['ok', 'insufficient']);
    });

    it('should refuse a blocked account before any pre-flight', async () => {
      await h.billing.setCaps(userId, { blocked: true }, adminCtx());

      await expect(makeEnforcement(getTestDb(), h).planPlaylist(userId, [{ youtubeId: 'aaaaaaaaaaa', durationSec: null }]))
        .rejects.toBeInstanceOf(AccountBlockedError);
      expect(h.metadata.getVideoMetadata).not.toHaveBeenCalled();
    });
  });
});

describe('BillingGate.admit — hold scope (W3)', () => {
  beforeAll(async () => {
    await createBillingIndexes(getTestDb());
  });

  it('should not let another user ride on an open hold by reusing its run id', async () => {
    const h = makeBillingHarness(getTestDb());
    const owner = await insertUser(getTestDb());
    const intruder = await insertUser(getTestDb());
    await h.gate.admit({ requestId: 'run-shared', userId: owner, youtubeId: 'dQw4w9WgXcQ', durationSec: 600 });

    await expect(
      h.gate.admit({ requestId: 'run-shared', userId: intruder, youtubeId: 'dQw4w9WgXcQ', durationSec: 600 }),
    ).rejects.toMatchObject({ code: 'HOLD_CONFLICT', status: 409 });
  });
});
