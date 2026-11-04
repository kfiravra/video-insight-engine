import { describe, it, expect, beforeAll, afterAll, beforeEach, vi } from 'vitest';
import { FastifyInstance } from 'fastify';
import { buildTestApp, createMockContainer, getAuthHeader, type MockContainer } from '../test/helpers.js';
import { ColdRunForbiddenError } from '../utils/errors.js';

describe('videos routes', () => {
  let app: FastifyInstance;
  let mockContainer: MockContainer;
  let authHeader: string;

  beforeAll(async () => {
    mockContainer = createMockContainer();
    app = await buildTestApp(mockContainer);
    await app.ready();
    authHeader = await getAuthHeader(app);
  });

  afterAll(async () => {
    await app.close();
  });

  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('GET /api/videos', () => {
    it('should return list of videos with default pagination', async () => {
      const mockVideos = [
        { id: 'v1', youtubeId: 'abc123', title: 'Test Video', status: 'completed' },
        { id: 'v2', youtubeId: 'def456', title: 'Another Video', status: 'processing' },
      ];
      mockContainer.videoService.getVideos.mockResolvedValue({ videos: mockVideos, total: 2 });

      const response = await app.inject({
        method: 'GET',
        url: '/api/videos',
        headers: { authorization: authHeader },
      });

      expect(response.statusCode).toBe(200);
      expect(mockContainer.videoService.getVideos).toHaveBeenCalledWith(
        'test-user-id',
        undefined,
        { limit: 50, offset: 0 },
      );
      expect(response.json()).toEqual({
        videos: mockVideos,
        pagination: { limit: 50, offset: 0, total: 2 },
      });
    });

    it('should filter by folderId when provided', async () => {
      mockContainer.videoService.getVideos.mockResolvedValue({ videos: [], total: 0 });

      const response = await app.inject({
        method: 'GET',
        url: '/api/videos?folderId=507f1f77bcf86cd799439011',
        headers: { authorization: authHeader },
      });

      expect(response.statusCode).toBe(200);
      expect(mockContainer.videoService.getVideos).toHaveBeenCalledWith(
        'test-user-id',
        '507f1f77bcf86cd799439011',
        { limit: 50, offset: 0 },
      );
    });

    it('should pass validated limit and offset through to the service', async () => {
      mockContainer.videoService.getVideos.mockResolvedValue({ videos: [], total: 120 });

      const response = await app.inject({
        method: 'GET',
        url: '/api/videos?limit=10&offset=20',
        headers: { authorization: authHeader },
      });

      expect(response.statusCode).toBe(200);
      expect(mockContainer.videoService.getVideos).toHaveBeenCalledWith(
        'test-user-id',
        undefined,
        { limit: 10, offset: 20 },
      );
      expect(response.json().pagination).toEqual({ limit: 10, offset: 20, total: 120 });
    });

    it('should return 400 when limit exceeds the cap of 100', async () => {
      const response = await app.inject({
        method: 'GET',
        url: '/api/videos?limit=101',
        headers: { authorization: authHeader },
      });

      expect(response.statusCode).toBe(400);
      expect(mockContainer.videoService.getVideos).not.toHaveBeenCalled();
    });

    it('should return 400 when limit is zero', async () => {
      const response = await app.inject({
        method: 'GET',
        url: '/api/videos?limit=0',
        headers: { authorization: authHeader },
      });

      expect(response.statusCode).toBe(400);
      expect(mockContainer.videoService.getVideos).not.toHaveBeenCalled();
    });

    it('should return 400 when offset is negative', async () => {
      const response = await app.inject({
        method: 'GET',
        url: '/api/videos?offset=-5',
        headers: { authorization: authHeader },
      });

      expect(response.statusCode).toBe(400);
      expect(mockContainer.videoService.getVideos).not.toHaveBeenCalled();
    });

    it('should return 401 without auth token', async () => {
      const response = await app.inject({
        method: 'GET',
        url: '/api/videos',
      });

      expect(response.statusCode).toBe(401);
    });
  });

  describe('GET /api/videos/:id', () => {
    it('should return a single video with what the user\'s run of it holds or cost', async () => {
      const mockVideo = {
        id: 'v1',
        videoSummaryId: '507f191e810c19729de860ea',
        youtubeId: 'abc123',
        title: 'Test Video',
        status: 'completed',
      };
      const billing = { state: 'charged', estimatedTokens: 840, chargedTokens: 750 };
      mockContainer.videoService.getVideo.mockResolvedValue(mockVideo);
      mockContainer.billingEnforcement.videoBilling.mockResolvedValueOnce(billing);

      const response = await app.inject({
        method: 'GET',
        url: '/api/videos/507f1f77bcf86cd799439011',
        headers: { authorization: authHeader },
      });

      expect(response.statusCode).toBe(200);
      expect(mockContainer.videoService.getVideo).toHaveBeenCalledWith(
        'test-user-id',
        '507f1f77bcf86cd799439011'
      );
      expect(mockContainer.billingEnforcement.videoBilling).toHaveBeenCalledWith('test-user-id', '507f191e810c19729de860ea');
      expect(response.json()).toEqual({ ...mockVideo, billing });
    });

    it('should return 404 when video not found', async () => {
      const { VideoNotFoundError } = await import('../utils/errors.js');
      mockContainer.videoService.getVideo.mockRejectedValue(new VideoNotFoundError());

      const response = await app.inject({
        method: 'GET',
        url: '/api/videos/507f1f77bcf86cd799439011',
        headers: { authorization: authHeader },
      });

      expect(response.statusCode).toBe(404);
    });
  });

  describe('GET /api/videos/estimate', () => {
    const URL = 'https://www.youtube.com/watch?v=dQw4w9WgXcQ';
    const estimate = (query = `?url=${encodeURIComponent(URL)}`, headers: Record<string, string> = { authorization: authHeader }) =>
      app.inject({ method: 'GET', url: `/api/videos/estimate${query}`, headers });

    it('should require a signed-in user', async () => {
      const response = await estimate(undefined, {});

      expect(response.statusCode).toBe(401);
      expect(mockContainer.videoEstimateService.estimate).not.toHaveBeenCalled();
    });

    it('should answer what a submit would hold, for this user', async () => {
      mockContainer.videoEstimateService.estimate.mockResolvedValueOnce({ youtubeId: 'dQw4w9WgXcQ', durationSec: 600, tokens: 840, free: false });

      const response = await estimate();

      expect(response.statusCode).toBe(200);
      expect(response.json()).toEqual({ youtubeId: 'dQw4w9WgXcQ', durationSec: 600, tokens: 840, free: false });
      expect(mockContainer.videoEstimateService.estimate).toHaveBeenCalledWith('test-user-id', URL);
    });

    it('should answer 0 tokens for a video served free', async () => {
      mockContainer.videoEstimateService.estimate.mockResolvedValueOnce({ youtubeId: 'dQw4w9WgXcQ', durationSec: 212, tokens: 0, free: true });

      expect((await estimate()).json()).toMatchObject({ tokens: 0, free: true });
    });

    it('should count against its own hourly bucket, never the submit bucket', async () => {
      await estimate();

      expect(mockContainer.estimateBurstLimiter.consume).toHaveBeenCalledWith('test-user-id');
      expect(mockContainer.submitBurstLimiter.consume).not.toHaveBeenCalled();
    });

    it.each([
      ['no url', ''],
      ['a url that is not a URL', '?url=not-a-url'],
    ])('should answer 400 for %s, without counting it', async (_label, query) => {
      const response = await estimate(query);

      expect([response.statusCode, response.json().error]).toEqual([400, 'VALIDATION_ERROR']);
      expect(mockContainer.estimateBurstLimiter.consume).not.toHaveBeenCalled();
    });

    it('should answer 400 INVALID_YOUTUBE_URL for a link that is not a YouTube video', async () => {
      const { InvalidYouTubeUrlError } = await import('../utils/errors.js');
      mockContainer.videoEstimateService.estimate.mockRejectedValueOnce(new InvalidYouTubeUrlError());

      const response = await estimate(`?url=${encodeURIComponent('https://example.com/video')}`);

      expect([response.statusCode, response.json().error]).toEqual([400, 'INVALID_YOUTUBE_URL']);
    });

    it.each([
      ['not_found', 404],
      ['timeout', 503],
      ['upstream', 503],
      ['unsupported', 422],
    ] as const)('should map METADATA_UNAVAILABLE (%s) as the submit does: %i', async (reason, status) => {
      const { MetadataUnavailableError } = await import('../utils/errors.js');
      mockContainer.videoEstimateService.estimate.mockRejectedValueOnce(new MetadataUnavailableError(reason));

      const response = await estimate();

      expect([response.statusCode, response.json().error]).toEqual([status, 'METADATA_UNAVAILABLE']);
    });

    it('should answer 429 RATE_LIMITED once the estimate bucket is spent, before looking anything up', async () => {
      const { RateLimitedError } = await import('../utils/errors.js');
      mockContainer.estimateBurstLimiter.consume.mockRejectedValueOnce(
        new RateLimitedError('Too many estimates in the last hour. Try again in 12 min.', 700),
      );

      const response = await estimate();

      expect([response.statusCode, response.json().error, response.json().retryAfterSec]).toEqual([429, 'RATE_LIMITED', 700]);
      expect(mockContainer.videoEstimateService.estimate).not.toHaveBeenCalled();
    });
  });

  describe('POST /api/videos', () => {
    it('should create a video from YouTube URL', async () => {
      const mockResult = {
        video: {
          id: 'v1',
          videoSummaryId: 'summary1',
          youtubeId: 'dQw4w9WgXcQ',
          status: 'pending',
        },
        cached: false,
      };
      mockContainer.videoService.createVideo.mockResolvedValue(mockResult);

      const response = await app.inject({
        method: 'POST',
        url: '/api/videos',
        headers: {
          authorization: authHeader,
          'content-type': 'application/json',
        },
        payload: {
          url: 'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
        },
      });

      expect(response.statusCode).toBe(201);
      expect(mockContainer.videoService.createVideo).toHaveBeenCalledWith(
        'test-user-id',
        'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
        expect.objectContaining({
          folderId: undefined,
          bypassCache: false,
          providers: undefined,
          queuePlan: 'free',
          requestId: expect.any(String),
        })
      );
      expect(response.json()).toEqual(mockResult);
    });

    it('should pass req.id to videoService.createVideo for downstream propagation', async () => {
      const mockResult = {
        video: { id: 'v1', status: 'pending' },
        cached: false,
      };
      mockContainer.videoService.createVideo.mockResolvedValue(mockResult);

      const incoming = 'frontend-trace-abc12345';
      await app.inject({
        method: 'POST',
        url: '/api/videos',
        headers: {
          authorization: authHeader,
          'content-type': 'application/json',
          'x-request-id': incoming,
        },
        payload: {
          url: 'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
        },
      });

      const optionsArg = mockContainer.videoService.createVideo.mock.calls[0][2];
      expect(optionsArg.requestId).toBe(incoming);
    });

    it('should accept optional folderId', async () => {
      const mockResult = {
        video: { id: 'v1', status: 'pending' },
        cached: false,
      };
      mockContainer.videoService.createVideo.mockResolvedValue(mockResult);

      const response = await app.inject({
        method: 'POST',
        url: '/api/videos',
        headers: {
          authorization: authHeader,
          'content-type': 'application/json',
        },
        payload: {
          url: 'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
          folderId: '507f1f77bcf86cd799439011',
        },
      });

      expect(response.statusCode).toBe(201);
      expect(mockContainer.videoService.createVideo).toHaveBeenCalledWith(
        'test-user-id',
        'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
        expect.objectContaining({
          folderId: '507f1f77bcf86cd799439011',
          bypassCache: false,
          providers: undefined,
          queuePlan: 'free',
        })
      );
    });

    it('should return 400 for invalid URL', async () => {
      const response = await app.inject({
        method: 'POST',
        url: '/api/videos',
        headers: {
          authorization: authHeader,
          'content-type': 'application/json',
        },
        payload: {
          url: 'not-a-valid-url',
        },
      });

      expect(response.statusCode).toBe(400);
    });

    it('should answer 402 with the tokens when the ledger cannot pay and nothing is cached', async () => {
      const { InsufficientTokensError, RunNotAllowedError } = await import('../utils/errors.js');
      mockContainer.billingEnforcement.admit.mockRejectedValueOnce(new InsufficientTokensError(840, 160));
      mockContainer.videoService.createVideo.mockRejectedValueOnce(new RunNotAllowedError());

      const response = await app.inject({
        method: 'POST',
        url: '/api/videos',
        headers: {
          authorization: authHeader,
          'content-type': 'application/json',
        },
        payload: {
          url: 'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
        },
      });

      expect(response.statusCode).toBe(402);
      expect(response.json()).toEqual({
        error: 'INSUFFICIENT_TOKENS', message: expect.any(String), statusCode: 402, needed: 840, available: 160,
      });
      expect(mockContainer.videoService.createVideo.mock.calls[0][2]).toMatchObject({ serveOnly: true });
    });

    it('should hold on a server run id, then call createVideo with it', async () => {
      mockContainer.videoService.createVideo.mockResolvedValue({
        video: { id: 'v1', status: 'pending' },
        cached: false,
      });

      const response = await app.inject({
        method: 'POST',
        url: '/api/videos',
        headers: {
          authorization: authHeader,
          'content-type': 'application/json',
        },
        payload: {
          url: 'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
        },
      });

      expect(response.statusCode).toBe(201);
      const { requestId: runId } = mockContainer.billingEnforcement.admit.mock.calls[0][0];
      expect(mockContainer.billingEnforcement.admit).toHaveBeenCalledWith({ requestId: runId, userId: 'test-user-id', youtubeId: 'dQw4w9WgXcQ', durationSec: null });
      expect(mockContainer.videoService.createVideo.mock.calls[0][2]).toMatchObject({ billingRunId: runId });
      // A new run keeps its hold: the terminal status settles it.
      expect(mockContainer.billingEnforcement.submitResolved).toHaveBeenCalledWith(runId, 'created');
    });

    it('should queue a Pro account\'s run as paid (D18)', async () => {
      mockContainer.billingEnforcement.admit.mockResolvedValueOnce({
        hold: { requestId: 'r', tokens: 840, split: { plan: 840, credit: 0, lots: [] } },
        queuePlan: 'paid',
      });
      mockContainer.videoService.createVideo.mockResolvedValue({ video: { id: 'v1', status: 'pending' }, cached: false });

      await app.inject({
        method: 'POST',
        url: '/api/videos',
        headers: { authorization: authHeader, 'content-type': 'application/json' },
        payload: { url: 'https://www.youtube.com/watch?v=dQw4w9WgXcQ' },
      });

      expect(mockContainer.videoService.createVideo.mock.calls[0][2]).toMatchObject({ queuePlan: 'paid' });
    });

    it('should give the hold back when createVideo throws', async () => {
      mockContainer.videoService.createVideo.mockRejectedValue(new Error('downstream-blew-up'));

      const response = await app.inject({
        method: 'POST',
        url: '/api/videos',
        headers: {
          authorization: authHeader,
          'content-type': 'application/json',
        },
        payload: {
          url: 'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
        },
      });

      expect(response.statusCode).toBe(500);
      const { requestId: runId } = mockContainer.billingEnforcement.admit.mock.calls[0][0];
      expect(mockContainer.billingEnforcement.submitFailed).toHaveBeenCalledWith(runId);
    });

    it('should give the hold back when the video is served from cache', async () => {
      mockContainer.videoService.createVideo.mockResolvedValue({
        video: { id: 'v1', status: 'completed' },
        cached: true,
      });

      const response = await app.inject({
        method: 'POST',
        url: '/api/videos',
        headers: {
          authorization: authHeader,
          'content-type': 'application/json',
        },
        payload: {
          url: 'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
        },
      });

      expect(response.statusCode).toBe(201);
      const { requestId: runId } = mockContainer.billingEnforcement.admit.mock.calls[0][0];
      expect(mockContainer.billingEnforcement.submitResolved).toHaveBeenCalledWith(runId, 'cached');
    });

    it('should give the hold back when the caller attaches to an in-flight pipeline', async () => {
      // Cross-user dedup: user 2 submits the same youtubeId while user 1's
      // pipeline is still pending. The upsert in createVideo returns the
      // existing row with `attached: true`. User 2 triggers no LLM work —
      // they only attach to the SSE stream of user 1's run — so, like a
      // cache hit, their hold goes back (Q6).
      mockContainer.videoService.createVideo.mockResolvedValue({
        video: { id: 'v2', videoSummaryId: 'shared-summary', status: 'processing' },
        cached: false,
        attached: true,
      });

      const response = await app.inject({
        method: 'POST',
        url: '/api/videos',
        headers: {
          authorization: authHeader,
          'content-type': 'application/json',
        },
        payload: {
          url: 'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
        },
      });

      expect(response.statusCode).toBe(201);
      const { requestId: runId } = mockContainer.billingEnforcement.admit.mock.calls[0][0];
      expect(mockContainer.billingEnforcement.submitResolved).toHaveBeenCalledWith(runId, 'attached');
    });
  });

  describe('DELETE /api/videos/:id', () => {
    it('should delete a video', async () => {
      mockContainer.videoCascadeService.deleteVideo.mockResolvedValue(undefined);

      const response = await app.inject({
        method: 'DELETE',
        url: '/api/videos/507f1f77bcf86cd799439011',
        headers: { authorization: authHeader },
      });

      expect(response.statusCode).toBe(204);
      // User scope only: the shared summary is never part of this call.
      expect(mockContainer.videoCascadeService.deleteVideo).toHaveBeenCalledWith({
        scope: 'user',
        userId: 'test-user-id',
        userVideoId: '507f1f77bcf86cd799439011',
      });
    });

    it('should return 404 when video not found', async () => {
      const { VideoNotFoundError } = await import('../utils/errors.js');
      mockContainer.videoCascadeService.deleteVideo.mockRejectedValue(new VideoNotFoundError());

      const response = await app.inject({
        method: 'DELETE',
        url: '/api/videos/507f1f77bcf86cd799439011',
        headers: { authorization: authHeader },
      });

      expect(response.statusCode).toBe(404);
    });
  });

  describe('PATCH /api/videos/:id/move', () => {
    it('should move video to a folder', async () => {
      mockContainer.videoService.moveToFolder.mockResolvedValue({ success: true });

      const response = await app.inject({
        method: 'PATCH',
        url: '/api/videos/507f1f77bcf86cd799439011/move',
        headers: {
          authorization: authHeader,
          'content-type': 'application/json',
        },
        payload: {
          folderId: '507f1f77bcf86cd799439012',
        },
      });

      expect(response.statusCode).toBe(200);
      expect(mockContainer.videoService.moveToFolder).toHaveBeenCalledWith(
        'test-user-id',
        '507f1f77bcf86cd799439011',
        '507f1f77bcf86cd799439012'
      );
    });

    it('should move video to root (no folder)', async () => {
      mockContainer.videoService.moveToFolder.mockResolvedValue({ success: true });

      const response = await app.inject({
        method: 'PATCH',
        url: '/api/videos/507f1f77bcf86cd799439011/move',
        headers: {
          authorization: authHeader,
          'content-type': 'application/json',
        },
        payload: {
          folderId: null,
        },
      });

      expect(response.statusCode).toBe(200);
      expect(mockContainer.videoService.moveToFolder).toHaveBeenCalledWith(
        'test-user-id',
        '507f1f77bcf86cd799439011',
        null
      );
    });
  });

  describe('GET /api/videos/versions/:youtubeId', () => {
    it('should return versions for a video', async () => {
      mockContainer.videoService.userOwnsVideo.mockResolvedValue(true);
      const mockVersions = [
        { id: 'v1', version: 1, isLatest: false, status: 'completed' },
        { id: 'v2', version: 2, isLatest: true, status: 'completed' },
      ];
      mockContainer.videoService.getVersions.mockResolvedValue(mockVersions);

      const response = await app.inject({
        method: 'GET',
        url: '/api/videos/versions/dQw4w9WgXcQ',
        headers: { authorization: authHeader },
      });

      expect(response.statusCode).toBe(200);
      expect(mockContainer.videoService.userOwnsVideo).toHaveBeenCalledWith('test-user-id', 'dQw4w9WgXcQ');
      expect(mockContainer.videoService.getVersions).toHaveBeenCalledWith('test-user-id', 'dQw4w9WgXcQ', { limit: 10 });
      expect(response.json()).toEqual({ versions: mockVersions });
    });

    it('should return 404 when user does not own video', async () => {
      mockContainer.videoService.userOwnsVideo.mockResolvedValue(false);

      const response = await app.inject({
        method: 'GET',
        url: '/api/videos/versions/dQw4w9WgXcQ',
        headers: { authorization: authHeader },
      });

      expect(response.statusCode).toBe(404);
      expect(response.json()).toHaveProperty('error', 'VIDEO_NOT_FOUND');
    });

    it('should accept limit query param', async () => {
      mockContainer.videoService.userOwnsVideo.mockResolvedValue(true);
      mockContainer.videoService.getVersions.mockResolvedValue([]);

      const response = await app.inject({
        method: 'GET',
        url: '/api/videos/versions/dQw4w9WgXcQ?limit=5',
        headers: { authorization: authHeader },
      });

      expect(response.statusCode).toBe(200);
      expect(mockContainer.videoService.getVersions).toHaveBeenCalledWith('test-user-id', 'dQw4w9WgXcQ', { limit: 5 });
    });

    it('should return 400 for invalid youtubeId format', async () => {
      const response = await app.inject({
        method: 'GET',
        url: '/api/videos/versions/invalid',
        headers: { authorization: authHeader },
      });

      expect(response.statusCode).toBe(400);
    });
  });

  describe('POST /api/videos — idempotency gate', () => {
    const VALID_YT_URL = 'https://www.youtube.com/watch?v=dQw4w9WgXcQ';

    const completedHit = (userVideoId: string, summaryId: string) => ({
      created: false,
      doc: {
        _id: { toString: () => 'idem1' },
        hash: 'test-hash',
        status: 'completed' as const,
        userId: { toString: () => 'test-user-id' },
        videoSummaryId: { toString: () => summaryId },
        userVideoId: { toString: () => userVideoId },
        youtubeId: 'dQw4w9WgXcQ',
      },
    });

    const pendingHit = () => ({
      created: false,
      doc: {
        _id: { toString: () => 'idem-pending' },
        hash: 'test-hash',
        status: 'pending' as const,
        userId: { toString: () => 'test-user-id' },
        youtubeId: 'dQw4w9WgXcQ',
      },
    });

    const freshReserve = () => ({
      created: true,
      doc: {
        _id: { toString: () => 'idem-new' },
        hash: 'test-hash',
        status: 'pending' as const,
        userId: { toString: () => 'test-user-id' },
        youtubeId: 'dQw4w9WgXcQ',
      },
    });

    it('returns the cached videoSummaryId without reserving cost or calling createVideo on a duplicate completed hit', async () => {
      const userVideoId = '507f1f77bcf86cd799439011';
      const summaryId = '507f191e810c19729de860ea';
      mockContainer.idempotencyService.reserveHash.mockResolvedValue(completedHit(userVideoId, summaryId));
      mockContainer.videoRepository.findUserVideo.mockResolvedValue({
        _id: { toString: () => userVideoId },
        videoSummaryId: { toString: () => summaryId },
        youtubeId: 'dQw4w9WgXcQ',
        title: 'Cached Title',
        channel: 'Cached Channel',
        duration: 100,
        thumbnailUrl: 'https://example.com/thumb.jpg',
        status: 'completed',
      });
      mockContainer.videoRepository.findCacheById.mockResolvedValue({
        title: 'Summary Title',
        channel: 'Summary Channel',
        duration: 100,
        thumbnailUrl: 'https://example.com/thumb.jpg',
        status: 'completed',
      });

      const response = await app.inject({
        method: 'POST',
        url: '/api/videos',
        headers: { authorization: authHeader, 'content-type': 'application/json' },
        payload: { url: VALID_YT_URL },
      });

      expect(response.statusCode).toBe(200);
      const body = response.json();
      expect(body.duplicate).toBe(true);
      expect(body.cached).toBe(true);
      expect(body.video.id).toBe(userVideoId);
      expect(body.video.videoSummaryId).toBe(summaryId);
      expect(body.video.title).toBe('Summary Title');
      expect(body.video.channel).toBe('Summary Channel');

      // No hold, no createVideo on a hit — that's the whole point.
      expect(mockContainer.billingEnforcement.admit).not.toHaveBeenCalled();
      expect(mockContainer.videoService.createVideo).not.toHaveBeenCalled();
    });

    it('returns 409 when the hash exists in pending state (another request is in-flight)', async () => {
      mockContainer.idempotencyService.reserveHash.mockResolvedValue(pendingHit());

      const response = await app.inject({
        method: 'POST',
        url: '/api/videos',
        headers: { authorization: authHeader, 'content-type': 'application/json' },
        payload: { url: VALID_YT_URL },
      });

      expect(response.statusCode).toBe(409);
      expect(response.json().error).toBe('IDEMPOTENCY_IN_FLIGHT');

      // No work happens for the late caller.
      expect(mockContainer.billingEnforcement.admit).not.toHaveBeenCalled();
      expect(mockContainer.videoService.createVideo).not.toHaveBeenCalled();
    });

    it('falls through to fresh creation when the completed hash points to a deleted userVideo', async () => {
      // First reserve attempt finds a stale completed row pointing at a deleted userVideo.
      // After scoped-invalidate, the retry reserves cleanly.
      mockContainer.idempotencyService.reserveHash
        .mockResolvedValueOnce(completedHit('gone1', 'sumX'))
        .mockResolvedValueOnce(freshReserve());

      mockContainer.videoRepository.findUserVideo.mockResolvedValue(null);
      mockContainer.videoService.createVideo.mockResolvedValue({
        video: { id: 'newUV', videoSummaryId: 'newSum', status: 'pending' },
        cached: false,
      });

      const response = await app.inject({
        method: 'POST',
        url: '/api/videos',
        headers: { authorization: authHeader, 'content-type': 'application/json' },
        payload: { url: VALID_YT_URL },
      });

      expect(response.statusCode).toBe(201);
      // Stale-hit cleanup is scoped to the (hash, observed summaryId) pair so a
      // concurrent fresh reservation cannot be accidentally deleted.
      expect(mockContainer.idempotencyService.invalidateStaleCompleted).toHaveBeenCalledWith(
        'test-hash',
        'sumX',
      );
      expect(mockContainer.idempotencyService.reserveHash).toHaveBeenCalledTimes(2);
      expect(mockContainer.billingEnforcement.admit).toHaveBeenCalled();
      expect(mockContainer.videoService.createVideo).toHaveBeenCalled();
    });

    it('completes the hash with real IDs after a fresh successful creation', async () => {
      mockContainer.idempotencyService.reserveHash.mockResolvedValue(freshReserve());
      mockContainer.videoService.createVideo.mockResolvedValue({
        video: { id: 'uv1', videoSummaryId: 'sum1', status: 'pending' },
        cached: false,
      });

      await app.inject({
        method: 'POST',
        url: '/api/videos',
        headers: { authorization: authHeader, 'content-type': 'application/json' },
        payload: { url: VALID_YT_URL },
      });

      expect(mockContainer.idempotencyService.completeHash).toHaveBeenCalledWith({
        hash: 'test-hash',
        videoSummaryId: 'sum1',
        userVideoId: 'uv1',
      });
    });

    it('invalidates the reserved hash when createVideo throws', async () => {
      mockContainer.idempotencyService.reserveHash.mockResolvedValue(freshReserve());
      mockContainer.videoService.createVideo.mockRejectedValue(new Error('downstream-blew-up'));

      const response = await app.inject({
        method: 'POST',
        url: '/api/videos',
        headers: { authorization: authHeader, 'content-type': 'application/json' },
        payload: { url: VALID_YT_URL },
      });

      expect(response.statusCode).toBe(500);
      expect(mockContainer.idempotencyService.invalidateByHash).toHaveBeenCalledWith('test-hash');
    });

    it('invalidates the reserved hash when the ledger refuses the submit', async () => {
      const { AccountBlockedError } = await import('../utils/errors.js');
      mockContainer.idempotencyService.reserveHash.mockResolvedValue(freshReserve());
      mockContainer.billingEnforcement.admit.mockRejectedValueOnce(new AccountBlockedError());

      const response = await app.inject({
        method: 'POST',
        url: '/api/videos',
        headers: { authorization: authHeader, 'content-type': 'application/json' },
        payload: { url: VALID_YT_URL },
      });

      expect(response.statusCode).toBe(403);
      expect(mockContainer.idempotencyService.invalidateByHash).toHaveBeenCalledWith('test-hash');
      expect(mockContainer.videoService.createVideo).not.toHaveBeenCalled();
    });

    it('skips idempotency entirely when bypassCache is true', async () => {
      mockContainer.videoService.createVideo.mockResolvedValue({
        video: { id: 'uv1', videoSummaryId: 'sum1', status: 'pending' },
        cached: false,
      });

      await app.inject({
        method: 'POST',
        url: '/api/videos',
        headers: { authorization: authHeader, 'content-type': 'application/json' },
        payload: { url: VALID_YT_URL, bypassCache: true },
      });

      expect(mockContainer.idempotencyService.reserveHash).not.toHaveBeenCalled();
      expect(mockContainer.idempotencyService.completeHash).not.toHaveBeenCalled();
    });

    it('should pass cold to createVideo and skip idempotency when cold is true', async () => {
      mockContainer.videoService.createVideo.mockResolvedValue({
        video: { id: 'uv1', videoSummaryId: 'sum1', status: 'pending' },
        cached: false,
      });

      await app.inject({
        method: 'POST',
        url: '/api/videos',
        headers: { authorization: authHeader, 'content-type': 'application/json' },
        payload: { url: VALID_YT_URL, cold: true },
      });

      expect(mockContainer.idempotencyService.reserveHash).not.toHaveBeenCalled();
      expect(mockContainer.videoService.createVideo).toHaveBeenCalledWith(
        'test-user-id',
        VALID_YT_URL,
        expect.objectContaining({ cold: true }),
      );
    });

    it('should return 403 when a user who is neither admin nor eval asks for a cold run', async () => {
      mockContainer.videoService.createVideo.mockRejectedValue(new ColdRunForbiddenError());

      const response = await app.inject({
        method: 'POST',
        url: '/api/videos',
        headers: { authorization: authHeader, 'content-type': 'application/json' },
        payload: { url: VALID_YT_URL, cold: true },
      });

      expect(response.statusCode).toBe(403);
    });

    it('should return 400 when cold is not a boolean', async () => {
      const response = await app.inject({
        method: 'POST',
        url: '/api/videos',
        headers: { authorization: authHeader, 'content-type': 'application/json' },
        payload: { url: VALID_YT_URL, cold: 'yes' },
      });

      expect(response.statusCode).toBe(400);
    });

    it('passes the Idempotency-Key HTTP header to computeKey when supplied', async () => {
      mockContainer.idempotencyService.reserveHash.mockResolvedValue(freshReserve());
      mockContainer.videoService.createVideo.mockResolvedValue({
        video: { id: 'uv1', videoSummaryId: 'sum1', status: 'pending' },
        cached: false,
      });

      await app.inject({
        method: 'POST',
        url: '/api/videos',
        headers: {
          authorization: authHeader,
          'content-type': 'application/json',
          'idempotency-key': 'client-supplied-uuid',
        },
        payload: { url: VALID_YT_URL },
      });

      expect(mockContainer.idempotencyService.computeKey).toHaveBeenCalledWith(
        expect.objectContaining({
          userId: 'test-user-id',
          youtubeId: 'dQw4w9WgXcQ',
          clientKey: 'client-supplied-uuid',
        }),
      );
    });

    it('does not enqueue a queue job on idempotency hit (cost protection)', async () => {
      mockContainer.idempotencyService.reserveHash.mockResolvedValue(completedHit('uv1', 'sumX'));
      mockContainer.videoRepository.findUserVideo.mockResolvedValue({
        _id: { toString: () => 'uv1' },
        videoSummaryId: { toString: () => 'sumX' },
        status: 'completed',
      });
      mockContainer.videoRepository.findCacheById.mockResolvedValue(null);

      await app.inject({
        method: 'POST',
        url: '/api/videos',
        headers: { authorization: authHeader, 'content-type': 'application/json' },
        payload: { url: VALID_YT_URL },
      });

      // createVideo is the entry point to both HTTP-summarizer and the queue
      // publisher — bypassing it is the canonical cost-protection guarantee.
      expect(mockContainer.videoService.createVideo).not.toHaveBeenCalled();
      expect(mockContainer.queuePublisher.publishVideoJob).not.toHaveBeenCalled();
      expect(mockContainer.summarizerClient.triggerSummarization).not.toHaveBeenCalled();
      expect(mockContainer.billingEnforcement.admit).not.toHaveBeenCalled();
    });

    it('rejects an Idempotency-Key header that is too long (>255 chars)', async () => {
      const response = await app.inject({
        method: 'POST',
        url: '/api/videos',
        headers: {
          authorization: authHeader,
          'content-type': 'application/json',
          'idempotency-key': 'a'.repeat(300),
        },
        payload: { url: VALID_YT_URL },
      });

      expect(response.statusCode).toBe(400);
    });
  });
});
