/**
 * P1.7: the submit path tells the shadow minutes ledger about every old-gate
 * decision but never waits for it or hears back from it — the response, its
 * timing and the old gate's own calls are the same with the shadow on, off,
 * hanging or failing.
 */
import { describe, it, expect, beforeAll, beforeEach, afterEach, vi } from 'vitest';
import type { FastifyBaseLogger } from 'fastify';
import { getTestDb, mockLogger } from '../../test/setup.js';
import { createBillingIndexes } from '../../plugins/mongodb.js';
import { BillingShadowRepository } from '../../repositories/billing-shadow.repository.js';
import { DailyLimitReachedError } from '../../utils/errors.js';
import { VideoSubmissionService, type SubmitVideoInput } from '../video-submission.service.js';
import { insertUser, makeShadowHarness } from '../billing/__tests__/billing-fixtures.js';
import type { BillingShadowService } from '../billing/billing-shadow.service.js';
import type { CostMonitorService } from '../cost-monitor.service.js';
import type { IdempotencyService } from '../idempotency.service.js';
import type { VideoService } from '../video.service.js';
import type { VideoRepository } from '../../repositories/video.repository.js';

const URL = 'https://www.youtube.com/watch?v=dQw4w9WgXcQ';
const RESERVATION = { userId: 'u', dateKey: '2026-11-01', amountUsd: 0.15 };

function createdResult(overrides: Record<string, unknown> = {}) {
  return {
    video: { id: 'uv-1', videoSummaryId: 'vs-1', youtubeId: 'dQw4w9WgXcQ', status: 'pending' },
    cached: false,
    ...overrides,
  };
}

describe('VideoSubmissionService + shadow ledger', () => {
  let videoService: { createVideo: ReturnType<typeof vi.fn> };
  let costMonitor: { reserveUserCost: ReturnType<typeof vi.fn>; refundReservation: ReturnType<typeof vi.fn> };
  let idempotency: Record<string, ReturnType<typeof vi.fn>>;
  let userId: string;

  const input = (overrides: Partial<SubmitVideoInput> = {}): SubmitVideoInput => ({
    userId, url: URL, tier: 'free', requestId: 'req-submit-1', ...overrides,
  });
  const serviceWith = (shadow?: BillingShadowService) =>
    new VideoSubmissionService(
      videoService as unknown as VideoService,
      costMonitor as unknown as CostMonitorService,
      idempotency as unknown as IdempotencyService,
      { findUserVideo: vi.fn(), findCacheById: vi.fn() } as unknown as VideoRepository,
      mockLogger as unknown as FastifyBaseLogger,
      shadow,
    );
  const spyShadow = () => {
    const spy = { submitAdmitted: vi.fn(), submitRefused: vi.fn(), submitResolved: vi.fn(), submitFailed: vi.fn() };
    return { spy, shadow: spy as unknown as BillingShadowService };
  };

  beforeAll(async () => {
    await createBillingIndexes(getTestDb());
  });

  beforeEach(async () => {
    vi.clearAllMocks();
    userId = await insertUser(getTestDb());
    videoService = { createVideo: vi.fn().mockResolvedValue(createdResult()) };
    costMonitor = { reserveUserCost: vi.fn().mockResolvedValue(RESERVATION), refundReservation: vi.fn().mockResolvedValue(undefined) };
    idempotency = {
      computeKey: vi.fn().mockReturnValue('hash-1'),
      reserveHash: vi.fn().mockResolvedValue({ created: true, doc: { status: 'pending' } }),
      completeHash: vi.fn().mockResolvedValue(undefined),
      invalidateByHash: vi.fn().mockResolvedValue(undefined),
      invalidateStaleCompleted: vi.fn().mockResolvedValue(undefined),
    };
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe('request unaffected', () => {
    it('should return the same outcome and make the same old-gate calls with the shadow on as off', async () => {
      const off = await serviceWith().submit(input());
      const offCalls = [costMonitor.reserveUserCost.mock.calls, videoService.createVideo.mock.calls];
      vi.clearAllMocks();
      const h = makeShadowHarness(getTestDb());

      const on = await serviceWith(h.shadow).submit(input());
      await h.shadow.drain();

      expect(on).toEqual(off);
      expect([costMonitor.reserveUserCost.mock.calls, videoService.createVideo.mock.calls]).toEqual(offCalls);
      expect((await h.rowOf('req-submit-1'))?.path).toBe('created');
    });

    it('should not await the shadow: a hanging pre-flight never delays the response', async () => {
      const h = makeShadowHarness(getTestDb());
      h.metadata.getVideoMetadata.mockReturnValue(new Promise(() => undefined));

      const result = await serviceWith(h.shadow).submit(input());

      expect(result.outcome).toBe('created');
      expect(h.runner.pending).toBe(1);
    });

    it('should leave the request and the old gate unaffected when the shadow fails', async () => {
      const h = makeShadowHarness(getTestDb());
      vi.spyOn(BillingShadowRepository.prototype, 'findByRequestId').mockRejectedValue(new Error('mongo down'));

      const result = await serviceWith(h.shadow).submit(input());
      await h.shadow.drain();

      expect(result).toEqual({ outcome: 'created', result: createdResult() });
      expect(costMonitor.refundReservation).not.toHaveBeenCalled();
      expect(mockLogger.warn).toHaveBeenCalledWith(expect.objectContaining({ key: 'req-submit-1' }), 'billing shadow step failed');
    });

    it('should rethrow the old gate\'s refusal unchanged', async () => {
      const refusal = new DailyLimitReachedError(1, '2026-11-02T00:00:00.000Z');
      costMonitor.reserveUserCost.mockRejectedValue(refusal);
      const h = makeShadowHarness(getTestDb());

      await expect(serviceWith(h.shadow).submit(input())).rejects.toBe(refusal);
      await h.shadow.drain();
      expect((await h.rowOf('req-submit-1'))?.path).toBe('refused');
    });
  });

  describe('what the shadow is told', () => {
    it('should report the admission, then the cached outcome', async () => {
      videoService.createVideo.mockResolvedValue(createdResult({ cached: true }));
      const { spy, shadow } = spyShadow();

      await serviceWith(shadow).submit(input());

      expect(spy.submitAdmitted).toHaveBeenCalledWith({ requestId: 'req-submit-1', userId, youtubeId: 'dQw4w9WgXcQ' });
      expect(spy.submitResolved).toHaveBeenCalledWith('req-submit-1', 'cached');
    });

    it('should report an attached outcome', async () => {
      videoService.createVideo.mockResolvedValue(createdResult({ attached: true }));
      const { spy, shadow } = spyShadow();

      await serviceWith(shadow).submit(input());

      expect(spy.submitResolved).toHaveBeenCalledWith('req-submit-1', 'attached');
    });

    it('should report a refusal by the daily cap and no admission', async () => {
      costMonitor.reserveUserCost.mockRejectedValue(new DailyLimitReachedError(1, '2026-11-02T00:00:00.000Z'));
      const { spy, shadow } = spyShadow();

      await serviceWith(shadow).submit(input()).catch(() => undefined);

      expect(spy.submitRefused).toHaveBeenCalledTimes(1);
      expect(spy.submitAdmitted).not.toHaveBeenCalled();
    });

    it('should report a createVideo failure', async () => {
      videoService.createVideo.mockRejectedValue(new Error('dispatch failed'));
      const { spy, shadow } = spyShadow();

      await expect(serviceWith(shadow).submit(input())).rejects.toThrow('dispatch failed');

      expect(spy.submitFailed).toHaveBeenCalledWith('req-submit-1');
    });

    it('should report nothing for a duplicate submission', async () => {
      idempotency.reserveHash.mockResolvedValue({ created: false, doc: { status: 'pending' } });
      const { spy, shadow } = spyShadow();

      expect(await serviceWith(shadow).submit(input())).toEqual({ outcome: 'in_flight' });

      expect(Object.values(spy).every((fn) => fn.mock.calls.length === 0)).toBe(true);
    });

    it('should report nothing without a requestId', async () => {
      const { spy, shadow } = spyShadow();

      await serviceWith(shadow).submit(input({ requestId: undefined }));

      expect(spy.submitAdmitted).not.toHaveBeenCalled();
    });
  });
});
