import { describe, it, expect, beforeAll, beforeEach, afterEach, vi } from 'vitest';
import type { FastifyBaseLogger } from 'fastify';
import { VideoService } from './video.service.js';
import { VideoRepository } from '../repositories/video.repository.js';
import { SummarizerClient } from './summarizer-client.js';
import { QueuePublisher } from './queue-publisher.service.js';
import { IdempotencyService } from './idempotency.service.js';
import { DispatchGuardService } from './dispatch-guard.service.js';
import { config } from '../config.js';

// Mock logger for tests
const mockLogger = {
  info: vi.fn(),
  error: vi.fn(),
  warn: vi.fn(),
  debug: vi.fn(),
  trace: vi.fn(),
  fatal: vi.fn(),
  child: vi.fn(() => mockLogger),
  level: 'silent',
  silent: vi.fn(),
} as unknown as FastifyBaseLogger;

describe('VideoService', () => {
  let videoService: VideoService;
  let mockVideoRepository: {
    findCacheById: ReturnType<typeof vi.fn>;
    findCacheByYoutubeId: ReturnType<typeof vi.fn>;
    createCacheEntry: ReturnType<typeof vi.fn>;
    upsertCacheByDedupKey: ReturnType<typeof vi.fn>;
    createUserVideo: ReturnType<typeof vi.fn>;
    findUserVideo: ReturnType<typeof vi.fn>;
    findUserVideoByYoutubeId: ReturnType<typeof vi.fn>;
    getUserVideos: ReturnType<typeof vi.fn>;
    countUserVideos: ReturnType<typeof vi.fn>;
    deleteUserVideo: ReturnType<typeof vi.fn>;
    deleteUserVideoByYoutubeId: ReturnType<typeof vi.fn>;
    updateUserVideoFolder: ReturnType<typeof vi.fn>;
    markPreviousVersionsNotLatest: ReturnType<typeof vi.fn>;
    findHighestVersion: ReturnType<typeof vi.fn>;
    incrementRetryCount: ReturnType<typeof vi.fn>;
    getVersions: ReturnType<typeof vi.fn>;
    pruneVersions: ReturnType<typeof vi.fn>;
    userOwnsVideo: ReturnType<typeof vi.fn>;
    updateCacheEntry: ReturnType<typeof vi.fn>;
  };
  let mockSummarizerClient: {
    triggerSummarization: ReturnType<typeof vi.fn>;
  };
  let mockQueuePublisher: {
    publishVideoJob: ReturnType<typeof vi.fn>;
  };
  let mockIdempotencyService: {
    computeContentKey: ReturnType<typeof vi.fn>;
  };
  let mockDispatchGuard: {
    acquire: ReturnType<typeof vi.fn>;
    release: ReturnType<typeof vi.fn>;
  };
  let mockEvalUsers: {
    isEvalUser: ReturnType<typeof vi.fn>;
  };

  beforeAll(() => {
    mockVideoRepository = {
      findCacheById: vi.fn(),
      findCacheByYoutubeId: vi.fn(),
      createCacheEntry: vi.fn(),
      upsertCacheByDedupKey: vi.fn(),
      createUserVideo: vi.fn(),
      findUserVideo: vi.fn(),
      findUserVideoByYoutubeId: vi.fn(),
      getUserVideos: vi.fn(),
      countUserVideos: vi.fn(),
      deleteUserVideo: vi.fn(),
      deleteUserVideoByYoutubeId: vi.fn(),
      updateUserVideoFolder: vi.fn(),
      markPreviousVersionsNotLatest: vi.fn(),
      findHighestVersion: vi.fn(),
      incrementRetryCount: vi.fn(),
      getVersions: vi.fn(),
      pruneVersions: vi.fn().mockResolvedValue(0),
      userOwnsVideo: vi.fn(),
      updateCacheEntry: vi.fn(),
    };
    mockSummarizerClient = {
      triggerSummarization: vi.fn(),
    };
    mockQueuePublisher = {
      publishVideoJob: vi.fn().mockResolvedValue({ requestId: 'req-1' }),
    };
    mockIdempotencyService = {
      computeContentKey: vi.fn().mockReturnValue('test-dedup-key'),
    };
    mockDispatchGuard = {
      // Default: every acquire wins so existing tests don't have to opt in.
      // Tests that exercise the "already dispatched" path override this.
      acquire: vi.fn().mockResolvedValue({ acquired: true, token: 'test-token' }),
      release: vi.fn().mockResolvedValue(undefined),
    };
    // Default: nobody is the eval user; eval behaviour lives in
    // __tests__/video.service.eval-versions.test.ts.
    mockEvalUsers = {
      isEvalUser: vi.fn().mockResolvedValue(false),
    };
    videoService = new VideoService(
      mockVideoRepository as unknown as VideoRepository,
      mockSummarizerClient as unknown as SummarizerClient,
      mockQueuePublisher as unknown as QueuePublisher,
      mockIdempotencyService as unknown as IdempotencyService,
      mockDispatchGuard as unknown as DispatchGuardService,
      mockEvalUsers,
      mockLogger
    );
  });

  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('getVideos', () => {
    it('should return mapped videos plus the total filtered count', async () => {
      mockVideoRepository.getUserVideos.mockResolvedValue([
        {
          _id: { toString: () => 'uv1' },
          videoSummaryId: { toString: () => 'vs1' },
          youtubeId: 'abc123',
          title: 'Video One',
          status: 'completed',
          createdAt: new Date('2026-01-01T00:00:00.000Z'),
        },
      ]);
      mockVideoRepository.countUserVideos.mockResolvedValue(75);

      const result = await videoService.getVideos('user123', undefined, { limit: 1, offset: 0 });

      expect(result.total).toBe(75);
      expect(result.videos).toHaveLength(1);
      expect(result.videos[0]).toMatchObject({ id: 'uv1', youtubeId: 'abc123', title: 'Video One' });
    });

    it('should forward limit/offset to the repository and count with the same folder filter', async () => {
      mockVideoRepository.getUserVideos.mockResolvedValue([]);
      mockVideoRepository.countUserVideos.mockResolvedValue(0);

      await videoService.getVideos('user123', 'folder1', { limit: 10, offset: 20 });

      expect(mockVideoRepository.getUserVideos).toHaveBeenCalledWith(
        'user123',
        'folder1',
        { limit: 10, offset: 20 },
      );
      expect(mockVideoRepository.countUserVideos).toHaveBeenCalledWith('user123', 'folder1');
    });
  });

  describe('getVideo', () => {
    it('should return output when summary has triage data', async () => {
      const userId = 'user123';
      const videoId = 'vid123';
      const videoSummaryId = 'summary123';

      mockVideoRepository.findUserVideo.mockResolvedValue({
        _id: { toString: () => videoId },
        videoSummaryId: { toString: () => videoSummaryId },
        youtubeId: 'abc123',
        status: 'completed',
      });

      // Summary with triage-based pipeline data
      mockVideoRepository.findCacheById.mockResolvedValue({
        _id: { toString: () => videoSummaryId },
        youtubeId: 'abc123',
        status: 'completed',
        title: 'Test Video',
        triage: { contentTags: ['learning'], tabs: [{ id: 'key_points', label: 'Key Points' }] },
        output: { learningData: { keyPoints: [] } },
        synthesis: { tldr: 'Test', keyTakeaways: [], masterSummary: '', seoDescription: '' },
      });

      const result = await videoService.getVideo(userId, videoId);

      // New flat shape: meta contains triage fields + synthesis fields (no separate synthesis)
      expect(result.meta).not.toBeNull();
      expect((result.meta as Record<string, unknown>)?.contentTags).toEqual(['learning']);
      expect((result.meta as Record<string, unknown>)?.tldr).toBe('Test');
      expect((result.meta as Record<string, unknown>)?.keyTakeaways).toEqual([]);
      expect(result).not.toHaveProperty('synthesis');
    });

    it('should return null output when summary has no intent data', async () => {
      const userId = 'user123';
      const videoId = 'vid123';
      const videoSummaryId = 'summary123';

      mockVideoRepository.findUserVideo.mockResolvedValue({
        _id: { toString: () => videoId },
        videoSummaryId: { toString: () => videoSummaryId },
        youtubeId: 'abc123',
        status: 'completed',
      });

      // V1-style summary with no intent
      mockVideoRepository.findCacheById.mockResolvedValue({
        _id: { toString: () => videoSummaryId },
        youtubeId: 'abc123',
        status: 'completed',
        title: 'Test Video',
        summary: { tldr: 'old', chapters: [], concepts: [] },
      });

      const result = await videoService.getVideo(userId, videoId);

      // No triage/assembledMeta → meta should be null or empty
      expect(result.meta).toBeNull();
    });
  });

  describe('createVideo', () => {
    // Helper: build the shape returned by upsertCacheByDedupKey. The
    // repository always stamps `updatedAt` on insert/update, so the fixture
    // defaults it to "now" — the attach-stall liveness check in createVideo
    // depends on it. Tests that want to exercise the stall path explicitly
    // can override `updatedAt`.
    const upsertResult = (
      wasInsert: boolean,
      doc: { _id: string; youtubeId: string; status: string; title?: string; channel?: string; duration?: number; thumbnailUrl?: string; updatedAt?: Date },
    ) => ({
      wasInsert,
      doc: {
        updatedAt: new Date(),
        ...doc,
        _id: { toString: () => doc._id },
      },
    });

    it('should always return videoSummaryId in the video object (fresh insert)', async () => {
      const userId = 'user123';
      const url = 'https://www.youtube.com/watch?v=dQw4w9WgXcQ';
      const youtubeId = 'dQw4w9WgXcQ';
      const videoSummaryId = 'summary123';

      mockVideoRepository.findUserVideoByYoutubeId.mockResolvedValue(null);
      mockVideoRepository.upsertCacheByDedupKey.mockResolvedValue(
        upsertResult(true, { _id: videoSummaryId, youtubeId, status: 'pending' }),
      );
      mockVideoRepository.createUserVideo.mockResolvedValue({
        _id: { toString: () => 'userVideo123' },
        videoSummaryId: { toString: () => videoSummaryId },
        youtubeId,
        status: 'pending',
      });

      const result = await videoService.createVideo(userId, url, { tier: 'free' });

      expect(result.video).toHaveProperty('videoSummaryId');
      expect(result.video.videoSummaryId).toBe(videoSummaryId);
      expect(result.cached).toBe(false);
      expect(result.attached).toBeFalsy();
    });

    it('should return videoSummaryId with cached:true when upsert attaches to a completed row', async () => {
      const userId = 'user123';
      const url = 'https://www.youtube.com/watch?v=dQw4w9WgXcQ';
      const youtubeId = 'dQw4w9WgXcQ';
      const videoSummaryId = 'summary123';

      mockVideoRepository.findUserVideoByYoutubeId.mockResolvedValue(null);
      mockVideoRepository.upsertCacheByDedupKey.mockResolvedValue(
        upsertResult(false, {
          _id: videoSummaryId,
          youtubeId,
          status: 'completed',
          title: 'Test Video',
          channel: 'Test Channel',
        }),
      );
      mockVideoRepository.createUserVideo.mockResolvedValue({
        _id: { toString: () => 'userVideo123' },
        videoSummaryId: { toString: () => videoSummaryId },
        youtubeId,
        status: 'completed',
      });

      const result = await videoService.createVideo(userId, url, { tier: 'free' });

      expect(result.video.videoSummaryId).toBe(videoSummaryId);
      expect(result.cached).toBe(true);
    });

    it('should NOT dispatch a pipeline and SHOULD set attached:true when attaching to a processing row (cross-user case)', async () => {
      // The headline behaviour: user 2 submits the same video while user 1's
      // pipeline is still pending — no second dispatch, attacher marked so the
      // route refunds their cost reservation.
      const userId = 'user-2';
      const url = 'https://www.youtube.com/watch?v=dQw4w9WgXcQ';
      const youtubeId = 'dQw4w9WgXcQ';
      const videoSummaryId = 'shared-summary';

      mockVideoRepository.findUserVideoByYoutubeId.mockResolvedValue(null);
      mockVideoRepository.upsertCacheByDedupKey.mockResolvedValue(
        upsertResult(false, { _id: videoSummaryId, youtubeId, status: 'processing' }),
      );
      mockVideoRepository.createUserVideo.mockResolvedValue({
        _id: { toString: () => 'userVideo-2' },
        videoSummaryId: { toString: () => videoSummaryId },
        youtubeId,
        status: 'processing',
      });

      const result = await videoService.createVideo(userId, url, { tier: 'free' });

      expect(result.video.videoSummaryId).toBe(videoSummaryId);
      expect(result.cached).toBe(false);
      expect(result.attached).toBe(true);
      // No second pipeline run — the existing producer owns it.
      expect(mockSummarizerClient.triggerSummarization).not.toHaveBeenCalled();
      expect(mockQueuePublisher.publishVideoJob).not.toHaveBeenCalled();
    });

    it('should also set attached:true when attaching to a pending row', async () => {
      const youtubeId = 'dQw4w9WgXcQ';
      const videoSummaryId = 'shared-pending';

      mockVideoRepository.findUserVideoByYoutubeId.mockResolvedValue(null);
      mockVideoRepository.upsertCacheByDedupKey.mockResolvedValue(
        upsertResult(false, { _id: videoSummaryId, youtubeId, status: 'pending' }),
      );
      mockVideoRepository.createUserVideo.mockResolvedValue({
        _id: { toString: () => 'userVideo-pending' },
        videoSummaryId: { toString: () => videoSummaryId },
        youtubeId,
        status: 'pending',
      });

      const result = await videoService.createVideo(
        'user-x',
        'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
        { tier: 'free' },
      );

      expect(result.attached).toBe(true);
      expect(result.cached).toBe(false);
      expect(mockSummarizerClient.triggerSummarization).not.toHaveBeenCalled();
    });

    it('should dispatch when retrying a failed cached row (wasInsert:false, status:failed)', async () => {
      const youtubeId = 'dQw4w9WgXcQ';
      const videoSummaryId = 'summary-failed';

      mockVideoRepository.findUserVideoByYoutubeId.mockResolvedValue(null);
      mockVideoRepository.upsertCacheByDedupKey.mockResolvedValue(
        upsertResult(false, { _id: videoSummaryId, youtubeId, status: 'failed' }),
      );
      mockVideoRepository.incrementRetryCount.mockResolvedValue(undefined);
      mockVideoRepository.createUserVideo.mockResolvedValue({
        _id: { toString: () => 'userVideo-failed' },
        videoSummaryId: { toString: () => videoSummaryId },
        youtubeId,
        status: 'pending',
      });

      const result = await videoService.createVideo(
        'user123',
        'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
        { tier: 'free' },
      );

      expect(result.video.videoSummaryId).toBe(videoSummaryId);
      expect(result.attached).toBeFalsy();
      expect(mockVideoRepository.incrementRetryCount).toHaveBeenCalledWith(videoSummaryId);
      expect(mockSummarizerClient.triggerSummarization).toHaveBeenCalledTimes(1);
    });

    it('should compute the dedupKey via IdempotencyService and pass it to the upsert', async () => {
      const youtubeId = 'dQw4w9WgXcQ';
      const videoSummaryId = 'summary-key';
      mockIdempotencyService.computeContentKey.mockReturnValue('content-key-abc');

      mockVideoRepository.findUserVideoByYoutubeId.mockResolvedValue(null);
      mockVideoRepository.upsertCacheByDedupKey.mockResolvedValue(
        upsertResult(true, { _id: videoSummaryId, youtubeId, status: 'pending' }),
      );
      mockVideoRepository.createUserVideo.mockResolvedValue({
        _id: { toString: () => 'uv-key' },
        videoSummaryId: { toString: () => videoSummaryId },
        youtubeId,
        status: 'pending',
      });

      await videoService.createVideo(
        'user123',
        'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
        { tier: 'free', providers: { default: 'anthropic' } },
      );

      expect(mockIdempotencyService.computeContentKey).toHaveBeenCalledWith({
        youtubeId,
        providers: { default: 'anthropic' },
        version: 1,
      });
      const upsertArg = mockVideoRepository.upsertCacheByDedupKey.mock.calls[0][0];
      expect(upsertArg.dedupKey).toBe('content-key-abc');
    });

    describe('queue-driven pipeline (USE_QUEUE_PIPELINE=true)', () => {
      beforeEach(() => {
        config.USE_QUEUE_PIPELINE = true;
      });

      afterEach(() => {
        config.USE_QUEUE_PIPELINE = false;
      });

      it('should publish to queue and skip the HTTP summarizer client on cache miss', async () => {
        const youtubeId = 'dQw4w9WgXcQ';
        const videoSummaryId = 'summary-queue-1';

        mockVideoRepository.findUserVideoByYoutubeId.mockResolvedValue(null);
        mockVideoRepository.upsertCacheByDedupKey.mockResolvedValue(
          upsertResult(true, { _id: videoSummaryId, youtubeId, status: 'pending' }),
        );
        mockVideoRepository.createUserVideo.mockResolvedValue({
          _id: { toString: () => 'userVideo-queue' },
          videoSummaryId: { toString: () => videoSummaryId },
          youtubeId,
          status: 'pending',
        });

        await videoService.createVideo(
          'user-q',
          'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
          { tier: 'pro' },
        );

        expect(mockQueuePublisher.publishVideoJob).toHaveBeenCalledTimes(1);
        expect(mockSummarizerClient.triggerSummarization).not.toHaveBeenCalled();

        const arg = mockQueuePublisher.publishVideoJob.mock.calls[0][0];
        expect(arg.videoSummaryId).toBe(videoSummaryId);
        expect(arg.youtubeId).toBe(youtubeId);
        expect(arg.tier).toBe('pro');
      });

      it('should publish to queue on failed-video retry', async () => {
        const youtubeId = 'dQw4w9WgXcQ';
        const videoSummaryId = 'summary-queue-failed';

        mockVideoRepository.findUserVideoByYoutubeId.mockResolvedValue(null);
        mockVideoRepository.upsertCacheByDedupKey.mockResolvedValue(
          upsertResult(false, { _id: videoSummaryId, youtubeId, status: 'failed' }),
        );
        mockVideoRepository.createUserVideo.mockResolvedValue({
          _id: { toString: () => 'userVideo-q2' },
          videoSummaryId: { toString: () => videoSummaryId },
          youtubeId,
          status: 'pending',
        });

        await videoService.createVideo(
          'user-q',
          'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
          { tier: 'free' },
        );

        expect(mockQueuePublisher.publishVideoJob).toHaveBeenCalledTimes(1);
        expect(mockSummarizerClient.triggerSummarization).not.toHaveBeenCalled();
      });

      it('should forward requestId into the queue payload', async () => {
        const youtubeId = 'dQw4w9WgXcQ';
        const videoSummaryId = 'summary-req-id';

        mockVideoRepository.findUserVideoByYoutubeId.mockResolvedValue(null);
        mockVideoRepository.upsertCacheByDedupKey.mockResolvedValue(
          upsertResult(true, { _id: videoSummaryId, youtubeId, status: 'pending' }),
        );
        mockVideoRepository.createUserVideo.mockResolvedValue({
          _id: { toString: () => 'userVideo-req' },
          videoSummaryId: { toString: () => videoSummaryId },
          youtubeId,
          status: 'pending',
        });

        await videoService.createVideo(
          'user-q',
          'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
          { tier: 'free', requestId: 'req-from-route-abc12' },
        );

        const arg = mockQueuePublisher.publishVideoJob.mock.calls[0][0];
        expect(arg.requestId).toBe('req-from-route-abc12');
      });
    });

    describe('request-id propagation (HTTP fallback)', () => {
      it('should forward requestId to the summarizer client when not using the queue', async () => {
        const youtubeId = 'dQw4w9WgXcQ';
        const videoSummaryId = 'summary-http-req';

        mockVideoRepository.findUserVideoByYoutubeId.mockResolvedValue(null);
        mockVideoRepository.upsertCacheByDedupKey.mockResolvedValue(
          upsertResult(true, { _id: videoSummaryId, youtubeId, status: 'pending' }),
        );
        mockVideoRepository.createUserVideo.mockResolvedValue({
          _id: { toString: () => 'userVideo-http-req' },
          videoSummaryId: { toString: () => videoSummaryId },
          youtubeId,
          status: 'pending',
        });

        await videoService.createVideo(
          'user-http',
          'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
          { tier: 'free', requestId: 'req-http-fallback-99' },
        );

        const call = mockSummarizerClient.triggerSummarization.mock.calls[0][0];
        expect(call.requestId).toBe('req-http-fallback-99');
      });
    });

    describe('dispatch guard (Step 3)', () => {
      it('acquires the guard before publishing to the queue (cache miss)', async () => {
        config.USE_QUEUE_PIPELINE = true;
        const youtubeId = 'dQw4w9WgXcQ';
        const videoSummaryId = 'guarded-summary';

        mockVideoRepository.findUserVideoByYoutubeId.mockResolvedValue(null);
        mockVideoRepository.upsertCacheByDedupKey.mockResolvedValue(
          upsertResult(true, { _id: videoSummaryId, youtubeId, status: 'pending' }),
        );
        mockVideoRepository.createUserVideo.mockResolvedValue({
          _id: { toString: () => 'uv-guarded' },
          videoSummaryId: { toString: () => videoSummaryId },
          youtubeId,
          status: 'pending',
        });

        await videoService.createVideo(
          'user-g',
          'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
          { tier: 'free' },
        );

        expect(mockDispatchGuard.acquire).toHaveBeenCalledWith(videoSummaryId);
        expect(mockQueuePublisher.publishVideoJob).toHaveBeenCalledTimes(1);
        config.USE_QUEUE_PIPELINE = false;
      });

      it('skips publishing when the guard reports another publisher already dispatched', async () => {
        // The two-API-replica race protection: replica A wins the Mongo upsert
        // AND wins the Redis guard, publishes. Replica B somehow also gets a
        // wasInsert (shouldn't happen with the partial unique index, but defense
        // in depth) — the guard returns acquired:false, B does NOT publish.
        config.USE_QUEUE_PIPELINE = true;
        mockDispatchGuard.acquire.mockResolvedValue({ acquired: false, token: null });

        const youtubeId = 'dQw4w9WgXcQ';
        const videoSummaryId = 'already-dispatched';

        mockVideoRepository.findUserVideoByYoutubeId.mockResolvedValue(null);
        mockVideoRepository.upsertCacheByDedupKey.mockResolvedValue(
          upsertResult(true, { _id: videoSummaryId, youtubeId, status: 'pending' }),
        );
        mockVideoRepository.createUserVideo.mockResolvedValue({
          _id: { toString: () => 'uv-noop' },
          videoSummaryId: { toString: () => videoSummaryId },
          youtubeId,
          status: 'pending',
        });

        await videoService.createVideo(
          'user-g2',
          'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
          { tier: 'free' },
        );

        expect(mockDispatchGuard.acquire).toHaveBeenCalledWith(videoSummaryId);
        expect(mockQueuePublisher.publishVideoJob).not.toHaveBeenCalled();
        expect(mockSummarizerClient.triggerSummarization).not.toHaveBeenCalled();
        config.USE_QUEUE_PIPELINE = false;
      });

      it('also guards the HTTP fallback path (USE_QUEUE_PIPELINE=false)', async () => {
        // Same protection applies when the queue is disabled — the guard sits
        // above the dispatch decision, not below it.
        mockDispatchGuard.acquire.mockResolvedValue({ acquired: false, token: null });

        const youtubeId = 'dQw4w9WgXcQ';
        const videoSummaryId = 'http-guarded';

        mockVideoRepository.findUserVideoByYoutubeId.mockResolvedValue(null);
        mockVideoRepository.upsertCacheByDedupKey.mockResolvedValue(
          upsertResult(true, { _id: videoSummaryId, youtubeId, status: 'pending' }),
        );
        mockVideoRepository.createUserVideo.mockResolvedValue({
          _id: { toString: () => 'uv-http-noop' },
          videoSummaryId: { toString: () => videoSummaryId },
          youtubeId,
          status: 'pending',
        });

        await videoService.createVideo(
          'user-http-g',
          'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
          { tier: 'free' },
        );

        expect(mockSummarizerClient.triggerSummarization).not.toHaveBeenCalled();
      });
    });

    describe('retryCount-only-on-actual-dispatch', () => {
      // Pre-fix: every concurrent attacher on a `failed` row called
      // incrementRetryCount even though the Redis guard ensured only one
      // actually published. The counter overcounted by N-1 for N attachers.
      // Fix: dispatchPipeline returns `{ dispatched }` and only the caller
      // that won the guard bumps retryCount.

      it('does NOT increment retryCount when the dispatch guard rejects', async () => {
        mockDispatchGuard.acquire.mockResolvedValue({ acquired: false, token: null });

        mockVideoRepository.findUserVideoByYoutubeId.mockResolvedValue(null);
        mockVideoRepository.upsertCacheByDedupKey.mockResolvedValue(
          upsertResult(false, { _id: 'failed-row', youtubeId: 'dQw4w9WgXcQ', status: 'failed' }),
        );
        mockVideoRepository.createUserVideo.mockResolvedValue({
          _id: { toString: () => 'uv-noretry' },
          videoSummaryId: { toString: () => 'failed-row' },
          youtubeId: 'dQw4w9WgXcQ',
          status: 'pending',
        });

        await videoService.createVideo(
          'user-noretry',
          'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
          { tier: 'free' },
        );

        expect(mockVideoRepository.incrementRetryCount).not.toHaveBeenCalled();
      });

      it('DOES increment retryCount when the dispatch actually publishes', async () => {
        // Sanity check: the existing failed-retry behaviour still holds when
        // the guard grants the lock.
        mockDispatchGuard.acquire.mockResolvedValue({ acquired: true, token: 'tok' });

        mockVideoRepository.findUserVideoByYoutubeId.mockResolvedValue(null);
        mockVideoRepository.upsertCacheByDedupKey.mockResolvedValue(
          upsertResult(false, { _id: 'failed-row-2', youtubeId: 'dQw4w9WgXcQ', status: 'failed' }),
        );
        mockVideoRepository.createUserVideo.mockResolvedValue({
          _id: { toString: () => 'uv-retry' },
          videoSummaryId: { toString: () => 'failed-row-2' },
          youtubeId: 'dQw4w9WgXcQ',
          status: 'pending',
        });

        await videoService.createVideo(
          'user-retry',
          'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
          { tier: 'free' },
        );

        expect(mockVideoRepository.incrementRetryCount).toHaveBeenCalledWith('failed-row-2');
      });
    });

    describe('createUserVideo ordering on the winner path', () => {
      // Regression test: dispatchPipeline used to run BEFORE createUserVideo,
      // so a dispatch error would strand the originator without any
      // userVideo row. The fix swaps the order — userVideo first, then
      // dispatch — so the user has a row even if the broker is down.

      it('creates the user-video before calling dispatchPipeline (wasInsert)', async () => {
        const callOrder: string[] = [];
        mockVideoRepository.createUserVideo.mockImplementation(async () => {
          callOrder.push('createUserVideo');
          return {
            _id: { toString: () => 'uv-order' },
            videoSummaryId: { toString: () => 'fresh-order' },
            youtubeId: 'dQw4w9WgXcQ',
            status: 'pending',
          };
        });
        mockDispatchGuard.acquire.mockImplementation(async () => {
          callOrder.push('dispatchGuard.acquire');
          return { acquired: true, token: 'tok' };
        });
        mockVideoRepository.findUserVideoByYoutubeId.mockResolvedValue(null);
        mockVideoRepository.upsertCacheByDedupKey.mockResolvedValue(
          upsertResult(true, { _id: 'fresh-order', youtubeId: 'dQw4w9WgXcQ', status: 'pending' }),
        );

        await videoService.createVideo(
          'user-order',
          'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
          { tier: 'free' },
        );

        expect(callOrder.indexOf('createUserVideo')).toBeLessThan(
          callOrder.indexOf('dispatchGuard.acquire'),
        );
      });
    });

    describe('bypassCache version path', () => {
      it('should compute a versioned dedupKey and pass it to createCacheEntry', async () => {
        // bypassCache=true keeps `createCacheEntry` (deliberately inserts a new
        // versioned row each click) — but the row must include the dedupKey so
        // the partial unique index catches the rare two-clicks-same-version race.
        const youtubeId = 'dQw4w9WgXcQ';
        const previousSummaryId = 'old-summary';
        const newSummaryId = 'new-summary-v2';

        mockIdempotencyService.computeContentKey.mockReturnValue('content-key-v2');

        mockVideoRepository.markPreviousVersionsNotLatest.mockResolvedValue({
          _id: { toString: () => previousSummaryId },
          youtubeId,
          version: 1,
        });
        mockVideoRepository.createCacheEntry.mockResolvedValue({
          _id: { toString: () => newSummaryId },
          youtubeId,
          status: 'pending',
          version: 2,
        });
        mockVideoRepository.deleteUserVideoByYoutubeId.mockResolvedValue(undefined);
        mockVideoRepository.createUserVideo.mockResolvedValue({
          _id: { toString: () => 'uv-new' },
          videoSummaryId: { toString: () => newSummaryId },
          youtubeId,
          status: 'pending',
        });
        mockVideoRepository.getVersions.mockResolvedValue([]);

        await videoService.createVideo(
          'user-bypass',
          'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
          { tier: 'free', bypassCache: true },
        );

        expect(mockIdempotencyService.computeContentKey).toHaveBeenCalledWith({
          youtubeId,
          providers: undefined,
          version: 2,
        });
        const cacheArg = mockVideoRepository.createCacheEntry.mock.calls[0][0];
        expect(cacheArg.version).toBe(2);
        expect(cacheArg.dedupKey).toBe('content-key-v2');
      });

      it('should stamp forceRefresh on the fresh version row', async () => {
        // The summarizer reads this flag to skip its youtubeId-keyed response
        // cache — without it a bypassCache run is instantly re-fed the stale
        // cached payload whenever the SSE client wins the producer lock.
        const youtubeId = 'dQw4w9WgXcQ';
        mockVideoRepository.markPreviousVersionsNotLatest.mockResolvedValue({
          _id: 'prev-id',
          youtubeId,
          version: 1,
        });
        mockIdempotencyService.computeContentKey.mockReturnValue('content-key-v2');
        mockVideoRepository.createCacheEntry.mockResolvedValue({
          _id: { toString: () => 'new-cache-id' },
          youtubeId,
          version: 2,
        });
        mockVideoRepository.deleteUserVideoByYoutubeId.mockResolvedValue(undefined);
        mockVideoRepository.createUserVideo.mockResolvedValue({
          _id: { toString: () => 'new-user-video-id' },
        });

        await videoService.createVideo(
          'user-1',
          'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
          { tier: 'free', bypassCache: true },
        );

        const cacheArg = mockVideoRepository.createCacheEntry.mock.calls[0][0];
        expect(cacheArg.forceRefresh).toBe(true);
      });
    });

    describe('pipelineVersion serve/regen gate (project-score-9 4.3)', () => {
      // Docs are stamped with the canonical version at pipeline write time
      // (packages/shared/src/config/pipeline-version.json). The serve path
      // regens docs stamped with a DIFFERENT version; docs WITHOUT the field
      // predate stamping and are served as-is (current-legacy) — a version
      // bump must never mass-invalidate them.
      const youtubeId = 'dQw4w9WgXcQ';
      const url = 'https://www.youtube.com/watch?v=dQw4w9WgXcQ';
      const videoSummaryId = 'summary-versioned';

      const existingUserVideo = () => ({
        _id: { toString: () => 'userVideo-existing' },
        videoSummaryId: { toString: () => videoSummaryId },
        youtubeId,
        status: 'completed',
      });

      it('re-dispatches (regen) when the user already has a completed doc stamped with a stale version', async () => {
        mockVideoRepository.findUserVideoByYoutubeId.mockResolvedValue(existingUserVideo());
        mockVideoRepository.findCacheById.mockResolvedValue({
          _id: { toString: () => videoSummaryId },
          youtubeId,
          status: 'completed',
          title: 'Old Version Video',
          pipelineVersion: 'v0-stale',
          updatedAt: new Date(),
        });

        const result = await videoService.createVideo('user123', url, { tier: 'free' });

        expect(mockSummarizerClient.triggerSummarization).toHaveBeenCalledTimes(1);
        expect(result.video.status).toBe('pending');
        expect(result.cached).toBe(false);
        expect(result.regenerating).toBe(true);
      });

      it('serves as-is when the existing completed doc has NO pipelineVersion (current-legacy)', async () => {
        mockVideoRepository.findUserVideoByYoutubeId.mockResolvedValue(existingUserVideo());
        mockVideoRepository.findCacheById.mockResolvedValue({
          _id: { toString: () => videoSummaryId },
          youtubeId,
          status: 'completed',
          title: 'Legacy Video',
          updatedAt: new Date(),
        });

        const result = await videoService.createVideo('user123', url, { tier: 'free' });

        expect(mockSummarizerClient.triggerSummarization).not.toHaveBeenCalled();
        expect(mockQueuePublisher.publishVideoJob).not.toHaveBeenCalled();
        expect(result.cached).toBe(true);
        expect(result.alreadyExists).toBe(true);
      });

      it('serves as-is when the stored version matches the canonical version', async () => {
        mockVideoRepository.findUserVideoByYoutubeId.mockResolvedValue(existingUserVideo());
        mockVideoRepository.findCacheById.mockResolvedValue({
          _id: { toString: () => videoSummaryId },
          youtubeId,
          status: 'completed',
          title: 'Current Video',
          pipelineVersion: config.PIPELINE_VERSION,
          updatedAt: new Date(),
        });

        const result = await videoService.createVideo('user123', url, { tier: 'free' });

        expect(mockSummarizerClient.triggerSummarization).not.toHaveBeenCalled();
        expect(result.cached).toBe(true);
        expect(result.alreadyExists).toBe(true);
      });

      it('regens instead of serving when attaching to a completed row stamped with a stale version', async () => {
        mockVideoRepository.findUserVideoByYoutubeId.mockResolvedValue(null);
        mockVideoRepository.upsertCacheByDedupKey.mockResolvedValue({
          wasInsert: false,
          doc: {
            _id: { toString: () => videoSummaryId },
            youtubeId,
            status: 'completed',
            title: 'Old Version Video',
            pipelineVersion: 'v0-stale',
            updatedAt: new Date(),
          },
        });
        mockVideoRepository.createUserVideo.mockResolvedValue({
          _id: { toString: () => 'userVideo-new' },
          videoSummaryId: { toString: () => videoSummaryId },
          youtubeId,
          status: 'pending',
        });

        const result = await videoService.createVideo('user-2', url, { tier: 'free' });

        expect(mockSummarizerClient.triggerSummarization).toHaveBeenCalledTimes(1);
        expect(result.video.status).toBe('pending');
        expect(result.cached).toBe(false);
      });

      it('still serves a version-matching completed row on attach', async () => {
        mockVideoRepository.findUserVideoByYoutubeId.mockResolvedValue(null);
        mockVideoRepository.upsertCacheByDedupKey.mockResolvedValue({
          wasInsert: false,
          doc: {
            _id: { toString: () => videoSummaryId },
            youtubeId,
            status: 'completed',
            title: 'Current Video',
            pipelineVersion: config.PIPELINE_VERSION,
            updatedAt: new Date(),
          },
        });
        mockVideoRepository.createUserVideo.mockResolvedValue({
          _id: { toString: () => 'userVideo-new' },
          videoSummaryId: { toString: () => videoSummaryId },
          youtubeId,
          status: 'completed',
        });

        const result = await videoService.createVideo('user-2', url, { tier: 'free' });

        expect(mockSummarizerClient.triggerSummarization).not.toHaveBeenCalled();
        expect(result.cached).toBe(true);
      });
    });
  });
});
