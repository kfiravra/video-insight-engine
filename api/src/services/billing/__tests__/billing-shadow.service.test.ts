import { describe, it, expect, beforeAll, beforeEach, vi } from 'vitest';
import { ObjectId } from 'mongodb';
import { getTestDb, mockLogger } from '../../../test/setup.js';
import { createBillingIndexes } from '../../../plugins/mongodb.js';
import { MetadataUnavailableError } from '../../../utils/errors.js';
import { insertUser, makeShadowHarness } from './billing-fixtures.js';
import type { ShadowHarness } from './billing-fixtures.js';

const YOUTUBE_ID = 'dQw4w9WgXcQ';

describe('BillingShadowService (P1.7, real Mongo replica set)', () => {
  let h: ShadowHarness;
  let userId: string;

  const submission = (requestId: string, durationSec?: number) => ({ requestId, userId, youtubeId: YOUTUBE_ID, durationSec });
  const ctx = () => ({ actorId: 'system:test', reason: 'test grant', auditId: new ObjectId().toHexString() });
  const ledger = (filter: Record<string, unknown> = {}) => getTestDb().collection('ledgerEntries').find(filter).toArray();
  const expectConsistent = async () => expect((await h.billing.verifyAccount(userId)).drift).toEqual([]);
  const finish = async (requestId: string, status: 'completed' | 'failed') => {
    const videoSummaryId = new ObjectId().toHexString();
    h.stamps.set(videoSummaryId, requestId);
    h.shadow.runFinished(videoSummaryId, status);
    await h.shadow.drain();
    return videoSummaryId;
  };
  /** Use up the free video so later submits must pay. */
  const spendFreeVideo = async () => {
    h.shadow.submitAdmitted(submission('req-free', 300));
    await h.shadow.drain();
  };

  beforeAll(async () => {
    await createBillingIndexes(getTestDb());
  });

  beforeEach(async () => {
    vi.clearAllMocks();
    h = makeShadowHarness(getTestDb());
    userId = await insertUser(getTestDb());
  });

  describe('submit', () => {
    it('should hold and record an agreeing created row when both gates admit', async () => {
      h.shadow.submitAdmitted(submission('req-1'));
      await h.shadow.drain();

      expect(await h.rowOf('req-1')).toMatchObject({
        path: 'created',
        oldDecision: { admitted: true, charged: true },
        newDecision: { admitted: true, charged: true, split: { free: 10, plan: 0, credit: 0, lots: [] } },
        classification: 'agree',
      });
      expect(await ledger({ type: 'hold' })).toHaveLength(1);
    });

    it('should use a known duration without asking the metadata pre-flight', async () => {
      h.shadow.submitAdmitted(submission('req-1', 125));
      await h.shadow.drain();

      expect(h.metadata.getVideoMetadata).not.toHaveBeenCalled();
      expect((await h.rowOf('req-1'))?.newDecision.split?.free).toBe(3);
    });

    it.each(['cached', 'attached'] as const)(
      'should release a %s video after its hold, even when the hold is still waiting on metadata',
      async (outcome) => {
        h.metadata.getVideoMetadata.mockImplementation(
          () => new Promise((resolve) => setTimeout(() => resolve({ duration: 600 }), 20)),
        );

        h.shadow.submitAdmitted(submission('req-1'));
        h.shadow.submitResolved('req-1', outcome);
        await h.shadow.drain();

        expect(await h.rowOf('req-1')).toMatchObject({ path: outcome, newDecision: { admitted: true, charged: false }, classification: 'agree' });
        expect((await h.billing.summary(userId)).free.available).toBe(true);
        await expectConsistent();
      },
    );

    it('should give the hold back and agree when createVideo throws', async () => {
      h.shadow.submitAdmitted(submission('req-1'));
      h.shadow.submitFailed('req-1');
      await h.shadow.drain();

      expect(await h.rowOf('req-1')).toMatchObject({ path: 'failed', newDecision: { reason: 'submit_error', charged: false }, classification: 'agree' });
      await expectConsistent();
    });

    it('should quote only when the old gate refused, and explain admitting what the $ cap refused', async () => {
      h.shadow.submitRefused(submission('req-1'));
      await h.shadow.drain();

      const row = await h.rowOf('req-1');
      expect(row).toMatchObject({ path: 'refused', oldDecision: { admitted: false }, newDecision: { admitted: true, charged: false } });
      expect(row).toMatchObject({ classification: 'policy_delta', policyRef: expect.stringContaining('§4.1') });
      expect(await ledger()).toEqual([]);
    });

    it('should explain refusing a second free video within 24 h (Q3)', async () => {
      await spendFreeVideo();

      h.shadow.submitAdmitted(submission('req-2'));
      await h.shadow.drain();

      expect(await h.rowOf('req-2')).toMatchObject({
        newDecision: { admitted: false, reason: 'free_window_used' },
        classification: 'policy_delta',
        policyRef: expect.stringContaining('Q3'),
      });
    });

    it('should hold credits for a paid video', async () => {
      await h.billing.grantLot(userId, { minutes: 100 }, ctx());
      await spendFreeVideo();

      h.shadow.submitAdmitted(submission('req-2', 45 * 60));
      await h.shadow.drain();

      expect((await h.rowOf('req-2'))?.newDecision).toMatchObject({ admitted: true, split: { credit: 45 } });
      await expectConsistent();
    });

    it('should class an unavailable duration on its own, never as a policy delta (W1)', async () => {
      h.metadata.getVideoMetadata.mockRejectedValue(new MetadataUnavailableError('not_found'));

      h.shadow.submitAdmitted(submission('req-1'));
      await h.shadow.drain();

      const row = await h.rowOf('req-1');
      expect(row).toMatchObject({ newDecision: { reason: 'metadata_unavailable' }, classification: 'metadata_unavailable' });
      expect(row?.policyRef).toBeUndefined();
    });

    it('should keep the refusal reason when a refused submit then fails in createVideo (I2)', async () => {
      await spendFreeVideo();
      h.shadow.submitAdmitted(submission('req-2'));
      h.shadow.submitFailed('req-2');
      await h.shadow.drain();

      expect(await h.rowOf('req-2')).toMatchObject({
        path: 'failed', newDecision: { admitted: false, reason: 'free_window_used' }, classification: 'policy_delta',
      });
    });

    it('should log a ledger failure and record it as a disagreement', async () => {
      h.metadata.getVideoMetadata.mockResolvedValue({ duration: -5 });

      h.shadow.submitAdmitted(submission('req-1'));
      await h.shadow.drain();

      expect(await h.rowOf('req-1')).toMatchObject({ newDecision: { reason: 'shadow_error' }, classification: 'disagree' });
      expect(mockLogger.warn).toHaveBeenCalledWith(expect.objectContaining({ requestId: 'req-1' }), expect.any(String));
    });

    it('should mark a repeated submit for the same requestId as retry with one hold', async () => {
      h.shadow.submitAdmitted(submission('req-1'));
      h.shadow.submitAdmitted(submission('req-1'));
      await h.shadow.drain();

      expect(await h.rowOf('req-1')).toMatchObject({ path: 'retry', classification: 'agree' });
      expect(await ledger({ type: 'hold' })).toHaveLength(1);
    });

    it('should do nothing at all when disabled', async () => {
      const off = makeShadowHarness(getTestDb(), { enabled: false });

      off.shadow.submitAdmitted(submission('req-1'));
      off.shadow.runFinished(new ObjectId().toHexString(), 'completed');

      expect(off.runner.pending).toBe(0);
      expect(off.metadata.getVideoMetadata).not.toHaveBeenCalled();
      expect(await off.rowOf('req-1')).toBeNull();
    });
  });

  describe('terminal status', () => {
    it('should settle a completed run with the Σ llm_usage cost of its requestId', async () => {
      await getTestDb().collection('llm_usage').insertMany([
        { request_id: 'req-1', cost_usd: 0.12 },
        { request_id: 'req-1', cost_usd: 0.18 },
        { request_id: 'req-other', cost_usd: 9 },
      ]);
      h.shadow.submitAdmitted(submission('req-1'));

      await finish('req-1', 'completed');

      const debits = await ledger({ type: 'debit' });
      expect(debits.map((d) => d.costUsd)).toEqual([0.3]);
      expect(await h.rowOf('req-1')).toMatchObject({ path: 'completed', newDecision: { charged: true }, classification: 'agree' });
      await expectConsistent();
    });

    it('should release a failed run in full and explain the delta against the old gate', async () => {
      h.shadow.submitAdmitted(submission('req-1'));

      await finish('req-1', 'failed');

      expect(await ledger({ type: 'release' })).toHaveLength(1);
      expect(await h.rowOf('req-1')).toMatchObject({
        path: 'failed',
        newDecision: { charged: false },
        classification: 'policy_delta',
        policyRef: expect.stringContaining('released in full'),
      });
      await expectConsistent();
    });

    it('should only move the row along for a run the ledger had refused', async () => {
      await spendFreeVideo();
      h.shadow.submitAdmitted(submission('req-2'));

      await finish('req-2', 'completed');

      expect(await h.rowOf('req-2')).toMatchObject({ path: 'completed', classification: 'policy_delta' });
      expect(mockLogger.warn).not.toHaveBeenCalled();
    });

    it('should write nothing for a run whose cache row has no dispatch stamp', async () => {
      h.shadow.runFinished(new ObjectId().toHexString(), 'completed');
      await h.shadow.drain();

      expect(await ledger()).toEqual([]);
    });
  });

  describe('idempotency (P1.8)', () => {
    it('should debit once when the run completes twice (translation\'s second "completed")', async () => {
      h.shadow.submitAdmitted(submission('req-1'));
      await finish('req-1', 'completed');

      await finish('req-1', 'completed');

      expect(await ledger({ type: 'debit' })).toHaveLength(1);
      expect(await h.rowOf('req-1')).toMatchObject({ path: 'retry', newDecision: { charged: true }, classification: 'agree' });
    });

    it('should write no release when a failure arrives after the completion', async () => {
      h.shadow.submitAdmitted(submission('req-1'));
      await finish('req-1', 'completed');

      await finish('req-1', 'failed');

      expect(await ledger({ type: 'release' })).toEqual([]);
      expect(await h.rowOf('req-1')).toMatchObject({ path: 'retry', newDecision: { charged: true }, classification: 'agree' });
      await expectConsistent();
    });
  });
});
