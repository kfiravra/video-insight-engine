/**
 * Playlist import on the token ledger: the whole playlist is quoted up front
 * ("needs ≈ 5,040 tokens, you have 1,000"); a blocked account is refused
 * before a folder is created; otherwise items are admitted in order and the
 * import stops at the first estimate the balance cannot cover (a prefix),
 * one hold per item on its own server run id. Every account starts with its
 * 1,000-token signup grant.
 */
import { describe, it, expect, beforeAll, beforeEach, afterAll, vi } from 'vitest';
import { ObjectId } from 'mongodb';
import type { FastifyBaseLogger } from 'fastify';
import { getTestDb, mockLogger } from '../../test/setup.js';
import { createBillingIndexes } from '../../plugins/mongodb.js';
import { AccountBlockedError, MetadataUnavailableError, RunNotAllowedError } from '../../utils/errors.js';
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

describe('PlaylistService on the token ledger', () => {
  let h: BillingHarness;
  let userId: string;
  let service: PlaylistService;
  let createVideo: ReturnType<typeof vi.fn>;
  let folders: { create: ReturnType<typeof vi.fn>; getById: ReturnType<typeof vi.fn>; delete: ReturnType<typeof vi.fn> };

  const holds = () => getTestDb().collection('ledgerEntries').find({ type: 'hold' }).toArray();
  /** Tokens held per run (a hold that spans two lots writes one entry per lot). */
  const heldByRun = async (): Promise<Map<string, number>> => {
    const byRun = new Map<string, number>();
    for (const entry of await holds()) byRun.set(entry.source.ref, (byRun.get(entry.source.ref) ?? 0) - entry.tokens);
    return byRun;
  };
  const created = (overrides: Record<string, unknown> = {}) => ({
    video: { id: new ObjectId().toHexString(), videoSummaryId: new ObjectId().toHexString(), status: 'pending' },
    cached: false,
    ...overrides,
  });

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
    folders = { create: vi.fn().mockResolvedValue({ id: 'folder-1', name: 'P' }), getById: vi.fn(), delete: vi.fn().mockResolvedValue(undefined) };
    service = new PlaylistService(
      { createVideo } as unknown as VideoService,
      folders as unknown as FolderService,
      {} as unknown as SummarizerClient,
      makeEnforcement(getTestDb(), h),
      mockLogger as unknown as FastifyBaseLogger,
    );
  });

  it('should import nothing it cannot pay for and report the playlist\'s total', async () => {
    useExtract([60, 90, 20]);

    const result = await service.import(userId, PLAYLIST_URL);

    expect(result.videos).toEqual([]);
    expect(result.failedVideos.map((f) => f.error)).toEqual(['INSUFFICIENT_TOKENS', 'INSUFFICIENT_TOKENS', 'INSUFFICIENT_TOKENS']);
    // 60 min ≈ 1,740 + 90 min ≈ 2,280 + 20 min ≈ 1,020 against the signup grant.
    expect(result.billing).toEqual({ neededTokens: 5040, availableTokens: 1000 });
    expect(await holds()).toEqual([]);
  });

  it('should stop at the first item it cannot pay for, even when a later shorter one would fit (prefix)', async () => {
    useExtract([45, 5]);

    const result = await service.import(userId, PLAYLIST_URL);

    expect(result.videos).toEqual([]);
    expect(result.failedVideos.map((f) => [f.position, f.error])).toEqual([[1, 'INSUFFICIENT_TOKENS'], [2, 'INSUFFICIENT_TOKENS']]);
  });

  it('should remove the folder it made when nothing could be imported (I7)', async () => {
    useExtract([60]);

    const result = await service.import(userId, PLAYLIST_URL);

    expect([result.folder, folders.delete.mock.calls.length]).toEqual([null, 1]);
  });

  it('should still serve a cached item after the tokens ran out (C1 / Q6)', async () => {
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

  // Nothing to quote: no length from the extract nor the pre-flight, or no item at all (D-BLOCK).
  it.each([
    ['no item\'s length is known', [null, null]],
    ['the playlist is empty', []],
  ])('should refuse a blocked account even when %s', async (_label, minutes) => {
    await h.billing.setCaps(userId, { blocked: true }, ctx());
    h.metadata.getVideoMetadata.mockRejectedValue(new MetadataUnavailableError('timeout'));
    useExtract(minutes);
    createVideo.mockResolvedValue(created({ cached: true }));

    await expect(service.import(userId, PLAYLIST_URL)).rejects.toBeInstanceOf(AccountBlockedError);
    expect([folders.create.mock.calls.length, createVideo.mock.calls.length]).toEqual([0, 0]);
  });

  it('should admit the prefix the balance covers, stop at the first item it cannot, and report the quote', async () => {
    await h.billing.grantLot(userId, { tokens: 1000 }, ctx());
    useExtract([5, 5, 5, 5]);

    const result = await service.import(userId, PLAYLIST_URL);

    expect(result.videos.map((v) => v.position)).toEqual([1, 2]);
    expect(result.failedVideos.map((f) => [f.position, f.error])).toEqual([[3, 'INSUFFICIENT_TOKENS'], [4, 'INSUFFICIENT_TOKENS']]);
    expect(result.billing).toEqual({ neededTokens: 3000, availableTokens: 2000 });
    expect([...(await heldByRun()).values()]).toEqual([750, 750]);
  });

  it('should skip an item that is too long and keep importing the rest', async () => {
    await h.billing.grantLot(userId, { tokens: 10_000 }, ctx());
    useExtract([10, 200, 10]);

    const result = await service.import(userId, PLAYLIST_URL);

    expect(result.videos.map((v) => v.position)).toEqual([1, 3]);
    expect(result.failedVideos).toEqual([expect.objectContaining({ position: 2, error: 'VIDEO_TOO_LONG' })]);
  });

  it('should give one hold per item, each on its own server run id stamped by its dispatch', async () => {
    await h.billing.grantLot(userId, { tokens: 5000 }, ctx());
    useExtract([10, 10]);

    await service.import(userId, PLAYLIST_URL);

    const runIds = createVideo.mock.calls.map((call) => call[2].billingRunId);
    expect(new Set(runIds).size).toBe(2);
    expect(createVideo.mock.calls.map((call) => call[2].requestId)).toEqual(runIds);
    expect([...(await heldByRun()).keys()].sort()).toEqual([...runIds].sort());
  });

  it.each([
    ['a free account\'s items as free', false, 'free'],
    ['a Pro account\'s items as paid', true, 'paid'],
  ])('should queue %s (D18)', async (_label, pro, plan) => {
    if (pro) await h.billing.setPlan(userId, { planId: 'pro' }, ctx());
    useExtract([5]);

    await service.import(userId, PLAYLIST_URL);

    expect(createVideo.mock.calls.map((call) => call[2].queuePlan)).toEqual([plan]);
  });

  it('should give a cached item its hold back', async () => {
    useExtract([10]);
    createVideo.mockResolvedValue(created({ cached: true }));

    const result = await service.import(userId, PLAYLIST_URL);

    expect(result.cachedCount).toBe(1);
    expect(await h.billing.summary(userId)).toMatchObject({ balance: 1000, holds: [] });
  });

  it('should record an item whose length the pre-flight cannot get and import the others', async () => {
    await h.billing.grantLot(userId, { tokens: 5000 }, ctx());
    h.metadata.getVideoMetadata.mockRejectedValue(new Error('yt-dlp down'));
    useExtract([10, null]);

    const result = await service.import(userId, PLAYLIST_URL);

    expect(result.videos).toHaveLength(1);
    expect(result.failedVideos).toEqual([expect.objectContaining({ position: 2, error: 'METADATA_UNAVAILABLE' })]);
  });
});
