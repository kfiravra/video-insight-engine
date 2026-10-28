/**
 * P3.1 for playlist import on the minutes ledger: the whole playlist
 * is quoted up front ("needs 340 min, you have 120"); nothing affordable →
 * refused before a folder is created; otherwise items are admitted in order
 * and the import stops at the first one the minutes cannot cover (a prefix),
 * one hold per item on its own server run id.
 */
import { describe, it, expect, beforeAll, beforeEach, afterAll, vi } from 'vitest';
import { ObjectId } from 'mongodb';
import { getTestDb } from '../../test/setup.js';
import { createBillingIndexes } from '../../plugins/mongodb.js';
import { AccountBlockedError, RunNotAllowedError } from '../../utils/errors.js';
import { PlaylistService } from '../playlist.service.js';
import { insertUser, makeBillingHarness, makeEnforcement } from '../billing/__tests__/billing-fixtures.js';
import type { BillingHarness } from '../billing/__tests__/billing-fixtures.js';
import type { FolderService } from '../folder.service.js';
import type { SummarizerClient } from '../summarizer-client.js';
import type { VideoService } from '../video.service.js';

const PLAYLIST_URL = 'https://www.youtube.com/playlist?list=PLenforce1';
const mockFetch = vi.fn();
const ctx = () => ({ actorId: 'system:test', reason: 'test', auditId: new ObjectId().toHexString() });

function useExtract(minutes: Array<number | null>): void {
  const videos = minutes.map((m, i) => ({
    video_id: `pl${String(i + 1).padStart(9, '0')}`, title: `Video ${i + 1}`, position: i + 1,
    duration: m === null ? null : m * 60, thumbnail_url: null,
  }));
  mockFetch.mockResolvedValue({
    ok: true,
    json: () => Promise.resolve({ playlist_id: 'PLenforce1', title: 'P', channel: null, thumbnail_url: null, total_videos: videos.length, videos }),
  });
}

describe('PlaylistService on the minutes ledger', () => {
  let h: BillingHarness;
  let userId: string;
  let service: PlaylistService;
  let createVideo: ReturnType<typeof vi.fn>;
  let folders: { create: ReturnType<typeof vi.fn>; getById: ReturnType<typeof vi.fn> };

  const holds = () => getTestDb().collection('ledgerEntries').find({ type: 'hold' }).toArray();
  const created = (overrides: Record<string, unknown> = {}) => ({
    video: { id: new ObjectId().toHexString(), videoSummaryId: new ObjectId().toHexString(), status: 'pending' },
    cached: false,
    ...overrides,
  });
  /** Spend the free video so items must be paid from credits. */
  const spendFreeVideo = async () => {
    await h.gate.admit({ requestId: `free-${new ObjectId().toHexString()}`, userId, youtubeId: 'freevideo01', durationSec: 300 });
  };

  beforeAll(async () => {
    vi.stubGlobal('fetch', mockFetch);
    await createBillingIndexes(getTestDb());
  });

  afterAll(() => {
    vi.unstubAllGlobals();
  });

  beforeEach(async () => {
    vi.clearAllMocks();
    h = makeBillingHarness(getTestDb());
    userId = await insertUser(getTestDb());
    // Like the real createVideo for videos nobody has: serve-only finds nothing to serve.
    createVideo = vi.fn().mockImplementation(async (_user: string, _url: string, options: { serveOnly?: boolean }) => {
      if (options.serveOnly) throw new RunNotAllowedError();
      return created();
    });
    folders = { create: vi.fn().mockResolvedValue({ id: 'folder-1', name: 'P' }), getById: vi.fn() };
    service = new PlaylistService(
      { createVideo } as unknown as VideoService,
      folders as unknown as FolderService,
      {} as unknown as SummarizerClient,
      makeEnforcement(getTestDb(), h),
    );
  });

  it('should import nothing it cannot pay for and report the playlist\'s total', async () => {
    await spendFreeVideo();
    useExtract([60, 90, 190]);
    const result = await service.import(userId, PLAYLIST_URL);

    expect(result.videos).toEqual([]);
    expect(result.failedVideos.map((f) => f.error)).toEqual(['INSUFFICIENT_MINUTES', 'INSUFFICIENT_MINUTES', 'INSUFFICIENT_MINUTES']);
    expect(result.billing).toMatchObject({ neededMinutes: 340, availableMinutes: 0 });
    expect(await holds()).toHaveLength(1); // only the free video spent before the import
  });

  it('should still serve a cached item after the minutes ran out (C1 / Q6)', async () => {
    await spendFreeVideo();
    useExtract([20, 20]);
    createVideo
      .mockRejectedValueOnce(new RunNotAllowedError())
      .mockResolvedValueOnce(created({ cached: true }));

    const result = await service.import(userId, PLAYLIST_URL);

    expect(result.videos.map((v) => v.position)).toEqual([2]);
    expect(result.cachedCount).toBe(1);
    expect(createVideo.mock.calls.map((call) => call[2].serveOnly)).toEqual([true, true]);
  });

  it('should serve nothing to a blocked account, even from cache', async () => {
    await h.billing.setCaps(userId, { blocked: true }, ctx());
    useExtract([10]);
    createVideo.mockResolvedValue(created({ cached: true }));

    await expect(service.import(userId, PLAYLIST_URL)).rejects.toBeInstanceOf(AccountBlockedError);
    expect(createVideo).not.toHaveBeenCalled();
  });

  it('should refuse a blocked account before creating a folder', async () => {
    await h.billing.setCaps(userId, { blocked: true }, ctx());
    useExtract([10]);

    await expect(service.import(userId, PLAYLIST_URL)).rejects.toBeInstanceOf(AccountBlockedError);
    expect(folders.create).not.toHaveBeenCalled();
  });

  it('should admit the prefix the minutes cover, stop at the first item they cannot, and report the quote', async () => {
    await h.billing.grantLot(userId, { minutes: 50 }, ctx());
    await spendFreeVideo();
    useExtract([20, 20, 20, 5]);

    const result = await service.import(userId, PLAYLIST_URL);

    expect(result.videos.map((v) => v.position)).toEqual([1, 2]);
    expect(result.failedVideos.map((f) => [f.position, f.error])).toEqual([[3, 'INSUFFICIENT_MINUTES'], [4, 'INSUFFICIENT_MINUTES']]);
    expect(result.billing).toEqual({ neededMinutes: 65, availableMinutes: 50, freeVideoAvailable: false });
  });

  it('should skip an item that is too long and keep importing the rest', async () => {
    await h.billing.grantLot(userId, { minutes: 300 }, ctx());
    useExtract([10, 200, 10]);

    const result = await service.import(userId, PLAYLIST_URL);

    expect(result.videos.map((v) => v.position)).toEqual([1, 3]);
    expect(result.failedVideos).toEqual([expect.objectContaining({ position: 2, error: 'VIDEO_TOO_LONG_FOR_PLAN' })]);
  });

  it('should give one hold per item, each on its own server run id stamped by its dispatch', async () => {
    await h.billing.grantLot(userId, { minutes: 300 }, ctx());
    useExtract([10, 10]);

    await service.import(userId, PLAYLIST_URL);

    const runIds = createVideo.mock.calls.map((call) => call[2].billingRunId);
    expect(new Set(runIds).size).toBe(2);
    expect(createVideo.mock.calls.map((call) => call[2].requestId)).toEqual(runIds);
    expect((await holds()).map((hold) => hold.source.ref).sort()).toEqual([...runIds].sort());
  });

  it('should give a cached item its hold back', async () => {
    useExtract([10]);
    createVideo.mockResolvedValue(created({ cached: true }));

    const result = await service.import(userId, PLAYLIST_URL);

    expect(result.cachedCount).toBe(1);
    expect((await h.billing.summary(userId)).free.available).toBe(true);
  });

  it('should record an item whose length the pre-flight cannot get and import the others', async () => {
    await h.billing.grantLot(userId, { minutes: 300 }, ctx());
    h.metadata.getVideoMetadata.mockRejectedValue(new Error('yt-dlp down'));
    useExtract([10, null]);

    const result = await service.import(userId, PLAYLIST_URL);

    expect(result.videos).toHaveLength(1);
    expect(result.failedVideos).toEqual([expect.objectContaining({ position: 2, error: 'METADATA_UNAVAILABLE' })]);
  });
});
