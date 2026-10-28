/**
 * P3.1 — BILLING_LEDGER_ENFORCE on: the minutes ledger quotes and holds before
 * createVideo, refuses with 402/422/403/429 (METADATA_UNAVAILABLE fails
 * closed), gives a cached / attached / failed submit's hold back before
 * returning, and keys the run on a server id. The old dollar gate is skipped.
 */
import { describe, it, expect, beforeAll, beforeEach, vi } from 'vitest';
import { ObjectId } from 'mongodb';
import type { FastifyBaseLogger } from 'fastify';
import { getTestDb, mockLogger } from '../../test/setup.js';
import { createBillingIndexes } from '../../plugins/mongodb.js';
import { IdempotencyRepository } from '../../repositories/idempotency.repository.js';
import { VideoRepository } from '../../repositories/video.repository.js';
import {
  AccountBlockedError,
  InsufficientMinutesError,
  MetadataUnavailableError,
  RunNotAllowedError,
  TooManyRunsError,
  VideoTooLongForPlanError,
} from '../../utils/errors.js';
import { IdempotencyService } from '../idempotency.service.js';
import { VideoSubmissionService, type SubmitVideoInput } from '../video-submission.service.js';
import { insertUser, makeEnforcement, makeShadowHarness } from '../billing/__tests__/billing-fixtures.js';
import type { ShadowHarness } from '../billing/__tests__/billing-fixtures.js';
import type { CostMonitorService } from '../cost-monitor.service.js';
import type { VideoService } from '../video.service.js';

const logger = mockLogger as unknown as FastifyBaseLogger;
const ctx = () => ({ actorId: 'system:test', reason: 'test', auditId: new ObjectId().toHexString() });
let videoCounter = 0;
const freshVideo = () => `https://www.youtube.com/watch?v=en${String(++videoCounter).padStart(9, '0')}`;

describe('VideoSubmissionService with BILLING_LEDGER_ENFORCE on', () => {
  let h: ShadowHarness;
  let service: VideoSubmissionService;
  let userId: string;
  let createVideo: ReturnType<typeof vi.fn>;
  let costMonitor: { reserveUserCost: ReturnType<typeof vi.fn>; refundReservation: ReturnType<typeof vi.fn> };

  const input = (overrides: Partial<SubmitVideoInput> = {}): SubmitVideoInput => ({
    userId, url: freshVideo(), tier: 'free', requestId: 'client-req-1', ...overrides,
  });
  const submitMinutes = async (minutes: number, overrides: Partial<SubmitVideoInput> = {}) => {
    h.metadata.getVideoMetadata.mockResolvedValue({ duration: minutes * 60 });
    return service.submit(input(overrides));
  };
  const runIdOf = (call = 0): string => createVideo.mock.calls[call][2].billingRunId;
  const holds = () => getTestDb().collection('ledgerEntries').find({ type: 'hold' }).toArray();
  const idempotencyRows = () => getTestDb().collection('idempotencyKeys').countDocuments();
  const created = (overrides: Record<string, unknown> = {}) => ({
    video: { id: new ObjectId().toHexString(), videoSummaryId: new ObjectId().toHexString(), youtubeId: 'x', status: 'pending' },
    cached: false,
    ...overrides,
  });

  beforeAll(async () => {
    await createBillingIndexes(getTestDb());
    await getTestDb().collection('idempotencyKeys').createIndex({ hash: 1 }, { unique: true });
  });

  /** Like the real createVideo for a new run: the library row exists, so a replay meets the duplicate. */
  const createdWithLibraryRow = async (owner: string) => {
    const result = created();
    const videoSummaryId = new ObjectId(result.video.videoSummaryId);
    await getTestDb().collection('videoSummaryCache').insertOne({ _id: videoSummaryId, youtubeId: 'x', status: 'pending' });
    await getTestDb().collection('userVideos').insertOne({
      _id: new ObjectId(result.video.id), userId: new ObjectId(owner), videoSummaryId, youtubeId: 'x', status: 'pending',
    });
    return result;
  };

  beforeEach(async () => {
    vi.clearAllMocks();
    h = makeShadowHarness(getTestDb());
    userId = await insertUser(getTestDb());
    // Like the real createVideo for a video nobody has: serve-only finds nothing to serve.
    createVideo = vi.fn().mockImplementation(async (owner: string, _url: string, options: { serveOnly?: boolean }) => {
      if (options.serveOnly) throw new RunNotAllowedError();
      return createdWithLibraryRow(owner);
    });
    costMonitor = { reserveUserCost: vi.fn(), refundReservation: vi.fn().mockResolvedValue(undefined) };
    service = new VideoSubmissionService(
      { createVideo } as unknown as VideoService,
      costMonitor as unknown as CostMonitorService,
      new IdempotencyService(new IdempotencyRepository(getTestDb()), logger),
      new VideoRepository(getTestDb()),
      logger,
      h.shadow,
      makeEnforcement(getTestDb(), h),
    );
  });

  describe('admission', () => {
    it('should hold before createVideo, keyed on a server id, and skip the old dollar gate', async () => {
      await submitMinutes(10);

      const [hold] = await holds();
      expect(hold.source.ref).toBe(runIdOf());
      expect(runIdOf()).not.toBe('client-req-1');
      expect(createVideo.mock.calls[0][2].requestId).toBe('client-req-1');
      expect(costMonitor.reserveUserCost).not.toHaveBeenCalled();
      expect(await getTestDb().collection('billingShadow').countDocuments()).toBe(0);
    });

    it('should refuse with 402 INSUFFICIENT_MINUTES before createVideo and release the idempotency key', async () => {
      await submitMinutes(10);

      const error = await submitMinutes(10).catch((err: unknown) => err);

      expect(error).toBeInstanceOf(InsufficientMinutesError);
      expect(error).toMatchObject({ status: 402, needed: 10, available: 0, nextFreeAt: expect.any(String) });
      // The second submit only asked to be served free (nothing to serve), and dispatched nothing.
      expect(createVideo.mock.calls[1][2]).toMatchObject({ serveOnly: true });
      expect(await holds()).toHaveLength(1);
      expect(await idempotencyRows()).toBe(1);
    });

    it('should refuse a video over 180 minutes with 422 VIDEO_TOO_LONG_FOR_PLAN', async () => {
      await h.billing.grantLot(userId, { minutes: 1000 }, ctx());

      await expect(submitMinutes(200)).rejects.toBeInstanceOf(VideoTooLongForPlanError);
    });

    it('should refuse a blocked account with 403 ACCOUNT_BLOCKED', async () => {
      await h.billing.setCaps(userId, { blocked: true }, ctx());

      await expect(submitMinutes(10)).rejects.toBeInstanceOf(AccountBlockedError);
    });

    it('should refuse past maxConcurrent with 429 TOO_MANY_RUNS', async () => {
      await h.billing.grantLot(userId, { minutes: 100 }, ctx());
      await h.billing.setCaps(userId, { maxConcurrent: 1 }, ctx());
      await submitMinutes(10);

      await expect(submitMinutes(10)).rejects.toBeInstanceOf(TooManyRunsError);
    });

    it('should fail closed when the duration cannot be had, holding nothing', async () => {
      h.metadata.getVideoMetadata.mockRejectedValue(new MetadataUnavailableError('not_found'));

      await expect(service.submit(input())).rejects.toMatchObject({ code: 'METADATA_UNAVAILABLE', status: 404 });
      expect(createVideo.mock.calls.map((call) => call[2].serveOnly)).toEqual([true]);
      expect(await holds()).toEqual([]);
    });
  });

  describe('give-back', () => {
    it.each(['cached', 'attached'] as const)('should give the free video back for a %s video (Q6)', async (outcome) => {
      createVideo.mockResolvedValue(created(outcome === 'cached' ? { cached: true } : { attached: true }));

      await submitMinutes(10);

      expect((await h.billing.summary(userId)).free.available).toBe(true);
      expect(await getTestDb().collection('ledgerEntries').countDocuments({ type: 'release' })).toBe(1);
    });

    it('should release the hold when createVideo throws, then surface the error', async () => {
      createVideo.mockRejectedValue(new Error('dispatch failed'));

      await expect(submitMinutes(10)).rejects.toThrow('dispatch failed');

      expect((await h.billing.summary(userId)).holds).toEqual([]);
      expect((await h.billing.verifyAccount(userId)).drift).toEqual([]);
    });
  });

  describe('a refusal still serves what costs nothing (C1 / Q6)', () => {
    it.each(['cached', 'attached'] as const)('should serve a %s video free after the free video is used, holding nothing', async (outcome) => {
      await submitMinutes(10);
      createVideo.mockImplementationOnce(async () => created(outcome === 'cached' ? { cached: true } : { attached: true }));

      const result = await submitMinutes(10);

      expect(result.outcome).toBe('created');
      expect(createVideo.mock.calls[1][2]).toMatchObject({ serveOnly: true });
      expect(await holds()).toHaveLength(1);
    });

    it('should refuse with the ledger\'s 402 when serving would need a run', async () => {
      await submitMinutes(10);
      createVideo.mockRejectedValueOnce(new RunNotAllowedError());

      const error = await submitMinutes(10).catch((err: unknown) => err);

      expect(error).toBeInstanceOf(InsufficientMinutesError);
      expect(await idempotencyRows()).toBe(1);
    });

    it('should not serve a blocked account even from cache', async () => {
      await h.billing.setCaps(userId, { blocked: true }, ctx());
      createVideo.mockResolvedValueOnce(created({ cached: true }));

      await expect(submitMinutes(10)).rejects.toBeInstanceOf(AccountBlockedError);
      expect(createVideo).not.toHaveBeenCalled();
    });

    it('should serve a cached video when the pre-flight cannot get its length', async () => {
      h.metadata.getVideoMetadata.mockRejectedValue(new MetadataUnavailableError('timeout'));
      createVideo.mockResolvedValueOnce(created({ cached: true }));

      const result = await service.submit(input());

      expect(result.outcome).toBe('created');
      expect(await holds()).toEqual([]);
    });
  });

  describe('one hold per submission', () => {
    it('should not hold again for an Idempotency-Key replay', async () => {
      const url = freshVideo();
      await submitMinutes(10, { url, clientKey: 'key-1' });

      const replay = await submitMinutes(10, { url, clientKey: 'key-1' });

      expect(replay.outcome).not.toBe('created');
      expect(await holds()).toHaveLength(1);
    });

    it('should not hold again for a retried submit of the same video', async () => {
      const url = freshVideo();
      await submitMinutes(10, { url });

      await submitMinutes(10, { url });

      expect(await holds()).toHaveLength(1);
    });

    it('should give a client that reuses X-Request-ID a new run per video, never a conflict', async () => {
      await h.billing.grantLot(userId, { minutes: 100 }, ctx());
      await submitMinutes(10);

      await submitMinutes(10);

      expect(new Set((await holds()).map((hold) => hold.source.ref)).size).toBe(2);
    });
  });
});

describe('VideoSubmissionService with BILLING_LEDGER_ENFORCE off', () => {
  it('should never block on minutes and still write the shadow ledger', async () => {
    await createBillingIndexes(getTestDb());
    const h = makeShadowHarness(getTestDb());
    const userId = await insertUser(getTestDb());
    const createVideo = vi.fn().mockImplementation(async () => ({
      video: { id: new ObjectId().toHexString(), videoSummaryId: new ObjectId().toHexString(), youtubeId: 'x', status: 'pending' },
      cached: false,
    }));
    const service = new VideoSubmissionService(
      { createVideo } as unknown as VideoService,
      { reserveUserCost: vi.fn().mockResolvedValue(null), refundReservation: vi.fn() } as unknown as CostMonitorService,
      { computeKey: vi.fn().mockReturnValue(null) } as unknown as IdempotencyService,
      new VideoRepository(getTestDb()),
      mockLogger as unknown as FastifyBaseLogger,
      h.shadow,
    );

    for (const id of ['offaaaaaaaa', 'offbbbbbbbb']) {
      const result = await service.submit({ userId, url: `https://www.youtube.com/watch?v=${id}`, tier: 'free', bypassCache: true });
      expect(result.outcome).toBe('created');
    }
    await h.shadow.drain();

    const rows = await getTestDb().collection('billingShadow').find({}).toArray();
    expect(rows.map((row) => row.newDecision.admitted).sort()).toEqual([false, true]);
  });
});
