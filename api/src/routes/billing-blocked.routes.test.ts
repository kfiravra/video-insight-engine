/**
 * D-BLOCK on the wire, over a real LedgerEnforcement on the test replica set:
 * a blocked account submits nothing of any kind — a video already in its
 * library, an idempotency replay, an update, a re-run, a playlist, the
 * assistant's generate — and is answered 403 ACCOUNT_BLOCKED without
 * createVideo running. Its library stays readable: the lists, the video, the
 * folders and a completed video's stream never consult the ledger.
 */
import { describe, it, expect, beforeAll, afterAll, beforeEach, vi } from 'vitest';
import { FastifyInstance } from 'fastify';
import { ObjectId } from 'mongodb';
import type { FastifyBaseLogger } from 'fastify';
import { buildTestApp, createMockContainer, type MockContainer } from '../test/helpers.js';
import { getTestDb, mockLogger } from '../test/setup.js';
import { createBillingIndexes } from '../plugins/mongodb.js';
import { MetadataUnavailableError } from '../utils/errors.js';
import { PlaylistService } from '../services/playlist.service.js';
import { insertUser, makeBillingHarness, makeEnforcement } from '../services/billing/__tests__/billing-fixtures.js';
import type { BillingHarness } from '../services/billing/__tests__/billing-fixtures.js';
import type { LedgerEnforcement, PlaylistItemToPrice } from '../services/billing/ledger-enforcement.js';
import type { FolderService } from '../services/folder.service.js';
import type { SummarizerClient } from '../services/summarizer-client.js';
import type { VideoService } from '../services/video.service.js';

const INTERNAL_SECRET = 'dev-internal-secret-change-me';
const VIDEO_URL = 'https://www.youtube.com/watch?v=dQw4w9WgXcQ';
const ctx = () => ({ actorId: 'system:test', reason: 'test', auditId: new ObjectId().toHexString() });

describe('a blocked account (D-BLOCK) — HTTP', () => {
  let app: FastifyInstance;
  let mockContainer: MockContainer;
  let h: BillingHarness;
  let userId: string;
  let token: string;
  const fetchStub = vi.fn();
  /** The app keeps one object; each test swaps the harness behind it. */
  const ledger = (): LedgerEnforcement => makeEnforcement(getTestDb(), h);
  const enforcementProxy = {
    admit: (s: never) => ledger().admit(s),
    submitResolved: (id: string, o: never) => ledger().submitResolved(id, o),
    submitFailed: (id: string) => ledger().submitFailed(id),
    runFinished: (id: string, s: 'completed' | 'failed') => ledger().runFinished(id, s),
    planPlaylist: (user: string, items: PlaylistItemToPrice[]) => ledger().planPlaylist(user, items),
    refuseIfBlocked: (user: string) => ledger().refuseIfBlocked(user),
  } as unknown as LedgerEnforcement;

  const asUser = () => ({ authorization: `Bearer ${token}`, 'content-type': 'application/json' });
  const submit = (payload: Record<string, unknown> = {}) =>
    app.inject({ method: 'POST', url: '/api/videos', headers: asUser(), payload: { url: VIDEO_URL, ...payload } });
  const generate = () => app.inject({
    method: 'POST', url: '/internal/assistant/generate',
    headers: { 'content-type': 'application/json', 'x-internal-secret': INTERNAL_SECRET, 'x-user-id': userId },
    payload: { url: VIDEO_URL },
  });
  const get = (url: string) => app.inject({ method: 'GET', url, headers: { authorization: `Bearer ${token}` } });
  const refusal = (response: { statusCode: number; json: () => { error?: string } }) => [response.statusCode, response.json().error];

  /** An earlier submit's idempotency key, completed on a video still in the library. */
  const replayOfLibraryVideo = () => {
    const userVideoId = new ObjectId();
    const summaryId = new ObjectId();
    mockContainer.idempotencyService.reserveHash.mockResolvedValueOnce({
      created: false,
      doc: { hash: 'test-hash', status: 'completed', userId: new ObjectId(userId), youtubeId: 'dQw4w9WgXcQ', userVideoId, videoSummaryId: summaryId },
    });
    mockContainer.videoRepository.findUserVideo.mockResolvedValueOnce({
      _id: userVideoId, videoSummaryId: summaryId, youtubeId: 'dQw4w9WgXcQ', status: 'completed',
    });
    mockContainer.videoRepository.findCacheById.mockResolvedValueOnce({ _id: summaryId, status: 'completed', title: 'Mine' });
  };

  beforeAll(async () => {
    vi.stubGlobal('fetch', fetchStub);
    await createBillingIndexes(getTestDb());
    mockContainer = createMockContainer();
    // The real import path, so the playlist goes through the ledger too.
    const playlistService = new PlaylistService(
      mockContainer.videoService as unknown as VideoService,
      mockContainer.folderService as unknown as FolderService,
      {} as unknown as SummarizerClient,
      enforcementProxy,
      mockLogger as unknown as FastifyBaseLogger,
    );
    const overrides = { ...mockContainer, billingEnforcement: enforcementProxy, playlistService };
    app = await buildTestApp(overrides as unknown as Partial<MockContainer>);
    await app.ready();
  });

  afterAll(async () => {
    await app.close();
    vi.unstubAllGlobals();
  });

  beforeEach(async () => {
    vi.clearAllMocks();
    h = makeBillingHarness(getTestDb());
    userId = await insertUser(getTestDb());
    token = app.jwt.sign({ userId, email: 'user@example.com', type: 'access' });
    await h.billing.setCaps(userId, { blocked: true }, ctx());
    mockContainer.idempotencyService.reserveHash.mockResolvedValue({ created: true, doc: { status: 'pending' } });
    // What createVideo would answer: the video is already in the library.
    mockContainer.videoService.createVideo.mockResolvedValue({
      video: { id: new ObjectId().toHexString(), videoSummaryId: new ObjectId().toHexString(), youtubeId: 'dQw4w9WgXcQ', status: 'completed' },
      cached: true,
      alreadyExists: true,
    });
  });

  describe('refused with 403 ACCOUNT_BLOCKED', () => {
    it.each([
      ['a video already in the library', {}],
      ['an update to the latest version', { updateToLatest: true }],
      ['a bypassCache re-run (retry)', { bypassCache: true }],
    ])('should refuse POST /api/videos for %s', async (_label, payload) => {
      const response = await submit(payload);

      expect(refusal(response)).toEqual([403, 'ACCOUNT_BLOCKED']);
      expect(mockContainer.videoService.createVideo).not.toHaveBeenCalled();
    });

    it('should refuse an idempotency replay of a video already in the library', async () => {
      replayOfLibraryVideo();

      expect(refusal(await submit())).toEqual([403, 'ACCOUNT_BLOCKED']);
    });

    it('should refuse the assistant\'s generate route', async () => {
      expect(refusal(await generate())).toEqual([403, 'ACCOUNT_BLOCKED']);
      expect(mockContainer.videoService.createVideo).not.toHaveBeenCalled();
    });

    it('should refuse the assistant\'s generate route on an idempotency replay', async () => {
      replayOfLibraryVideo();

      expect(refusal(await generate())).toEqual([403, 'ACCOUNT_BLOCKED']);
    });

    it('should refuse a playlist import before creating a folder, even with no item length known', async () => {
      // No length from the extract nor the pre-flight: nothing to quote, so only the account itself can refuse.
      h.metadata.getVideoMetadata.mockRejectedValue(new MetadataUnavailableError('timeout'));
      fetchStub.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({
          playlist_id: 'PLblocked1', title: 'P', channel: null, thumbnail_url: null, total_videos: 1,
          videos: [{ video_id: 'dQw4w9WgXcQ', title: 'V', position: 1, duration: null, thumbnail_url: null }],
        }),
      });

      const response = await app.inject({
        method: 'POST', url: '/api/playlists/import', headers: asUser(),
        payload: { url: 'https://www.youtube.com/playlist?list=PLblocked1' },
      });

      expect(refusal(response)).toEqual([403, 'ACCOUNT_BLOCKED']);
      expect([mockContainer.folderService.create, mockContainer.videoService.createVideo].map((fn) => fn.mock.calls.length)).toEqual([0, 0]);
    });
  });

  describe('the library stays readable', () => {
    it('should list the library (GET /api/videos)', async () => {
      expect((await get('/api/videos')).statusCode).toBe(200);
    });

    it('should open a video (GET /api/videos/:id)', async () => {
      mockContainer.videoService.getVideo.mockResolvedValueOnce({ id: 'v1', status: 'completed' });

      expect((await get(`/api/videos/${new ObjectId().toHexString()}`)).statusCode).toBe(200);
    });

    it('should list the folders (GET /api/folders)', async () => {
      mockContainer.folderService.list.mockResolvedValueOnce([]);

      expect((await get('/api/folders')).statusCode).toBe(200);
    });

    it('should stream a completed video', async () => {
      mockContainer.videoRepository.findCacheById.mockResolvedValueOnce({ status: 'completed' });
      const read = vi.fn()
        .mockResolvedValueOnce({ done: false, value: new TextEncoder().encode('data: {"event":"done"}\n\n') })
        .mockResolvedValueOnce({ done: true, value: undefined });
      fetchStub.mockResolvedValueOnce({ ok: true, status: 200, body: { getReader: () => ({ read, cancel: vi.fn().mockResolvedValue(undefined) }) } });

      const response = await get(`/api/videos/${new ObjectId().toHexString()}/stream`);

      expect([response.statusCode, response.body.includes('"event":"done"')]).toEqual([200, true]);
    });
  });
});
