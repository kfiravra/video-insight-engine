/**
 * P1.7 for playlist import: every item gets its own requestId (stamped by its
 * dispatch, so the terminal status can settle that item), is quoted from the
 * extract's duration, and is held / released / refused like a single submit.
 */
import { describe, it, expect, beforeAll, beforeEach, afterAll, vi } from 'vitest';
import { ObjectId } from 'mongodb';
import type { FastifyBaseLogger } from 'fastify';
import { getTestDb, mockLogger } from '../../test/setup.js';
import { createBillingIndexes } from '../../plugins/mongodb.js';
import { DailyLimitReachedError } from '../../utils/errors.js';
import { PlaylistService } from '../playlist.service.js';
import { insertUser, makeShadowHarness } from '../billing/__tests__/billing-fixtures.js';
import type { ShadowHarness } from '../billing/__tests__/billing-fixtures.js';
import type { BillingShadowService } from '../billing/billing-shadow.service.js';
import type { CostMonitorService } from '../cost-monitor.service.js';
import type { FolderService } from '../folder.service.js';
import type { SummarizerClient } from '../summarizer-client.js';
import type { VideoService } from '../video.service.js';

const PLAYLIST_URL = 'https://www.youtube.com/playlist?list=PLtest123';
const mockFetch = vi.fn();

function extractResponse(durations: Array<number | null>) {
  return {
    playlist_id: 'PLtest123', title: 'Test Playlist', channel: null, thumbnail_url: null,
    total_videos: durations.length,
    videos: durations.map((duration, i) => ({
      video_id: `video${i + 1}`, title: `Video ${i + 1}`, position: i + 1, duration, thumbnail_url: null,
    })),
  };
}

function created(overrides: Record<string, unknown> = {}) {
  return { video: { id: new ObjectId().toHexString(), videoSummaryId: new ObjectId().toHexString(), status: 'pending' }, cached: false, ...overrides };
}

describe('PlaylistService + shadow ledger', () => {
  let h: ShadowHarness;
  let userId: string;
  let videoService: { createVideo: ReturnType<typeof vi.fn> };
  let costMonitor: { reserveUserCost: ReturnType<typeof vi.fn>; refundReservation: ReturnType<typeof vi.fn> };

  const serviceWith = (shadow?: BillingShadowService) =>
    new PlaylistService(
      videoService as unknown as VideoService,
      { create: vi.fn().mockResolvedValue({ id: 'folder-1', name: 'Test Playlist' }), getById: vi.fn() } as unknown as FolderService,
      {} as unknown as SummarizerClient,
      mockLogger as unknown as FastifyBaseLogger,
      costMonitor as unknown as CostMonitorService,
      shadow,
    );
  const useExtract = (durations: Array<number | null>) =>
    mockFetch.mockResolvedValue({ ok: true, json: () => Promise.resolve(extractResponse(durations)) });
  const itemRequestIds = (): string[] => videoService.createVideo.mock.calls.map((call) => call[2].requestId);

  beforeAll(async () => {
    vi.stubGlobal('fetch', mockFetch);
    await createBillingIndexes(getTestDb());
  });

  afterAll(() => {
    vi.unstubAllGlobals();
  });

  beforeEach(async () => {
    vi.clearAllMocks();
    h = makeShadowHarness(getTestDb());
    userId = await insertUser(getTestDb());
    videoService = { createVideo: vi.fn().mockImplementation(async () => created()) };
    costMonitor = {
      reserveUserCost: vi.fn().mockResolvedValue({ userId, dateKey: '2026-11-01', amountUsd: 0.15 }),
      refundReservation: vi.fn().mockResolvedValue(undefined),
    };
  });

  it('should give each item its own requestId and hold it from the extract duration', async () => {
    await h.billing.grantLot(userId, { minutes: 100 }, { actorId: 'system:test', reason: 'test', auditId: new ObjectId().toHexString() });
    useExtract([120, 600]);

    await serviceWith(h.shadow).import(userId, PLAYLIST_URL);
    await h.shadow.drain();

    const requestIds = itemRequestIds();
    expect(new Set(requestIds).size).toBe(2);
    // Items run on their own chains, so either may take the free video; the minutes are the extract's.
    const splits = await Promise.all(requestIds.map(async (id) => (await h.rowOf(id))?.newDecision.split));
    expect(splits.map((split) => (split?.free ?? 0) + (split?.credit ?? 0)).sort((a, b) => a - b)).toEqual([3, 10]);
    expect(splits.filter((split) => split?.free)).toHaveLength(1);
    expect(h.metadata.getVideoMetadata).not.toHaveBeenCalled();
  });

  it('should ask the metadata pre-flight only for an item the extract has no duration for', async () => {
    useExtract([null]);

    await serviceWith(h.shadow).import(userId, PLAYLIST_URL);
    await h.shadow.drain();

    expect(h.metadata.getVideoMetadata).toHaveBeenCalledWith('video1');
  });

  it('should release a cached item', async () => {
    useExtract([120]);
    videoService.createVideo.mockResolvedValue(created({ cached: true }));

    await serviceWith(h.shadow).import(userId, PLAYLIST_URL);
    await h.shadow.drain();

    expect(await h.rowOf(itemRequestIds()[0])).toMatchObject({ path: 'cached', classification: 'agree' });
    expect((await h.billing.summary(userId)).holds).toEqual([]);
  });

  it('should record the items the old daily cap stopped as refused, with no hold', async () => {
    useExtract([120, 120, 120]);
    costMonitor.reserveUserCost
      .mockResolvedValueOnce({ userId, dateKey: '2026-11-01', amountUsd: 0.15 })
      .mockRejectedValueOnce(new DailyLimitReachedError(1, '2026-11-02T00:00:00.000Z'));

    const result = await serviceWith(h.shadow).import(userId, PLAYLIST_URL);
    await h.shadow.drain();

    const rows = await getTestDb().collection('billingShadow').find({ path: 'refused' }).toArray();
    expect(rows.map((row) => row.youtubeId).sort()).toEqual(['video2', 'video3']);
    expect(result.failedVideos.map((f) => f.error)).toEqual(['DAILY_LIMIT_REACHED', 'DAILY_LIMIT_REACHED']);
    expect((await h.billing.summary(userId)).holds).toHaveLength(1);
  });

  it('should return the same import result with the shadow on as off', async () => {
    useExtract([120, 180]);
    videoService.createVideo.mockResolvedValue(created());
    const off = await serviceWith().import(userId, PLAYLIST_URL);
    vi.clearAllMocks();
    useExtract([120, 180]);

    const on = await serviceWith(h.shadow).import(userId, PLAYLIST_URL);
    await h.shadow.drain();

    expect(on).toEqual(off);
  });
});
