import { describe, it, expect, beforeAll, afterAll, beforeEach, vi } from 'vitest';
import { FastifyInstance } from 'fastify';
import { ObjectId } from 'mongodb';
import { buildTestApp, createMockContainer, type MockContainer } from '../test/helpers.js';

// The default internal secret from config
const INTERNAL_SECRET = 'dev-internal-secret-change-me';

describe('internal routes', () => {
  let app: FastifyInstance;
  let mockContainer: MockContainer;

  beforeAll(async () => {
    mockContainer = createMockContainer();
    app = await buildTestApp(mockContainer);
    await app.ready();
  });

  afterAll(async () => {
    await app.close();
  });

  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('POST /internal/status', () => {
    describe('authentication', () => {
      it('should return 401 when x-internal-secret header is missing', async () => {
        const response = await app.inject({
          method: 'POST',
          url: '/internal/status',
          headers: { 'content-type': 'application/json' },
          payload: {
            type: 'video.status',
            payload: {
              videoSummaryId: '507f1f77bcf86cd799439011',
              status: 'completed',
            },
          },
        });

        expect(response.statusCode).toBe(401);
        expect(response.json()).toEqual({ error: 'Unauthorized' });
      });

      it('should return 401 when x-internal-secret is invalid', async () => {
        const response = await app.inject({
          method: 'POST',
          url: '/internal/status',
          headers: {
            'content-type': 'application/json',
            'x-internal-secret': 'wrong-secret',
          },
          payload: {
            type: 'video.status',
            payload: {
              videoSummaryId: '507f1f77bcf86cd799439011',
              status: 'completed',
            },
          },
        });

        expect(response.statusCode).toBe(401);
        expect(response.json()).toEqual({ error: 'Unauthorized' });
      });

      it('should return 401 for a same-length-but-wrong secret (constant-time path)', async () => {
        // Same byte length as the real secret, exercising timingSafeEqual rather
        // than the length guard — must still be rejected.
        const sameLengthWrong = 'x'.repeat(INTERNAL_SECRET.length);
        expect(sameLengthWrong.length).toBe(INTERNAL_SECRET.length);

        const response = await app.inject({
          method: 'POST',
          url: '/internal/status',
          headers: {
            'content-type': 'application/json',
            'x-internal-secret': sameLengthWrong,
          },
          payload: {
            type: 'video.status',
            payload: {
              videoSummaryId: '507f1f77bcf86cd799439011',
              status: 'completed',
            },
          },
        });

        expect(response.statusCode).toBe(401);
        expect(response.json()).toEqual({ error: 'Unauthorized' });
      });
    });

    describe('video.status events', () => {
      it('should process video.status event and update userVideos', async () => {
        const videoSummaryId = new ObjectId().toHexString();
        const mockUserVideos = [
          { _id: new ObjectId(), userId: new ObjectId(), videoSummaryId: new ObjectId(videoSummaryId) },
        ];

        // Mock MongoDB operations
        const mockUpdateMany = vi.fn().mockResolvedValue({ modifiedCount: 1 });
        const mockFind = vi.fn().mockReturnValue({
          toArray: vi.fn().mockResolvedValue(mockUserVideos),
        });
        const mockCollection = vi.fn().mockReturnValue({
          updateMany: mockUpdateMany,
          find: mockFind,
        });

        app.mongo.db.collection = mockCollection;

        // Mock broadcast function
        const mockBroadcast = vi.fn();
        app.broadcast = mockBroadcast;

        const response = await app.inject({
          method: 'POST',
          url: '/internal/status',
          headers: {
            'content-type': 'application/json',
            'x-internal-secret': INTERNAL_SECRET,
          },
          payload: {
            type: 'video.status',
            payload: {
              videoSummaryId,
              status: 'completed',
            },
          },
        });

        expect(response.statusCode).toBe(200);
        expect(response.json()).toEqual({ received: true });

        // Verify updateMany was called
        expect(mockCollection).toHaveBeenCalledWith('userVideos');
        expect(mockUpdateMany).toHaveBeenCalledWith(
          { videoSummaryId: new ObjectId(videoSummaryId) },
          expect.objectContaining({
            $set: expect.objectContaining({ status: 'completed' }),
          })
        );
      });

      it('should broadcast to specific userId when provided', async () => {
        const videoSummaryId = new ObjectId().toHexString();
        const userId = 'test-user-123';

        // Mock MongoDB operations
        const mockUpdateMany = vi.fn().mockResolvedValue({ modifiedCount: 1 });
        const mockCollection = vi.fn().mockReturnValue({
          updateMany: mockUpdateMany,
        });
        app.mongo.db.collection = mockCollection;

        // Mock broadcast function
        const mockBroadcast = vi.fn();
        app.broadcast = mockBroadcast;

        const response = await app.inject({
          method: 'POST',
          url: '/internal/status',
          headers: {
            'content-type': 'application/json',
            'x-internal-secret': INTERNAL_SECRET,
          },
          payload: {
            type: 'video.status',
            payload: {
              videoSummaryId,
              userId,
              status: 'processing',
              progress: 50,
            },
          },
        });

        expect(response.statusCode).toBe(200);

        // Verify broadcast was called with userId
        expect(mockBroadcast).toHaveBeenCalledWith(userId, {
          type: 'video.status',
          payload: {
            videoSummaryId,
            userId,
            status: 'processing',
            progress: 50,
          },
        });
      });

      it('should settle the run\'s hold when a video completes', async () => {
        const videoSummaryId = new ObjectId().toHexString();
        const userId = 'completed-user-123';

        const mockUpdateMany = vi.fn().mockResolvedValue({ modifiedCount: 1 });
        const mockCollection = vi.fn().mockReturnValue({
          updateMany: mockUpdateMany,
        });
        app.mongo.db.collection = mockCollection;
        app.broadcast = vi.fn();

        const response = await app.inject({
          method: 'POST',
          url: '/internal/status',
          headers: {
            'content-type': 'application/json',
            'x-internal-secret': INTERNAL_SECRET,
          },
          payload: {
            type: 'video.status',
            payload: {
              videoSummaryId,
              userId,
              status: 'completed',
            },
          },
        });

        expect(response.statusCode).toBe(200);
        expect(mockContainer.billingEnforcement.runFinished).toHaveBeenCalledWith(videoSummaryId, 'completed');
      });

      it('should release a cache-hit completion as cached, not settle it (I3)', async () => {
        const videoSummaryId = new ObjectId().toHexString();
        app.mongo.db.collection = vi.fn().mockReturnValue({ updateMany: vi.fn().mockResolvedValue({ modifiedCount: 1 }) });
        app.broadcast = vi.fn();

        await app.inject({
          method: 'POST',
          url: '/internal/status',
          headers: { 'content-type': 'application/json', 'x-internal-secret': INTERNAL_SECRET },
          payload: { type: 'video.status', payload: { videoSummaryId, userId: 'u-cache', status: 'completed', cacheHit: true } },
        });

        expect(mockContainer.billingEnforcement.runFinished).toHaveBeenCalledWith(videoSummaryId, 'cached');
      });

      it('should release the run\'s hold when a video FAILS', async () => {
        const videoSummaryId = new ObjectId().toHexString();
        const userId = 'failed-user-456';

        const mockUpdateMany = vi.fn().mockResolvedValue({ modifiedCount: 1 });
        app.mongo.db.collection = vi.fn().mockReturnValue({ updateMany: mockUpdateMany });
        app.broadcast = vi.fn();

        await app.inject({
          method: 'POST',
          url: '/internal/status',
          headers: {
            'content-type': 'application/json',
            'x-internal-secret': INTERNAL_SECRET,
          },
          payload: {
            type: 'video.status',
            payload: { videoSummaryId, userId, status: 'failed' },
          },
        });

        expect(mockContainer.billingEnforcement.runFinished).toHaveBeenCalledWith(videoSummaryId, 'failed');
      });

      it('should invalidate idempotency keys when a video transitions to failed (so the user can retry immediately)', async () => {
        const videoSummaryId = new ObjectId().toHexString();
        const userId = 'failed-user-789';

        const mockUpdateMany = vi.fn().mockResolvedValue({ modifiedCount: 1 });
        app.mongo.db.collection = vi.fn().mockReturnValue({ updateMany: mockUpdateMany });
        app.broadcast = vi.fn();

        await app.inject({
          method: 'POST',
          url: '/internal/status',
          headers: {
            'content-type': 'application/json',
            'x-internal-secret': INTERNAL_SECRET,
          },
          payload: {
            type: 'video.status',
            payload: { videoSummaryId, userId, status: 'failed' },
          },
        });

        expect(mockContainer.idempotencyService.invalidateByVideoSummaryId).toHaveBeenCalledWith(
          videoSummaryId,
        );
      });

      it('should release the dispatch guard on FAILED so user-initiated retry can re-dispatch immediately', async () => {
        // Step 3 belt-and-suspenders: without this release, the 900s guard
        // TTL would keep the dispatcher silent for 15 minutes after a failed
        // run — the user clicks Retry, the route goes through branch D
        // (failed-cached-retry → dispatchPipeline), but the guard.acquire
        // returns acquired:false and nothing publishes.
        const videoSummaryId = new ObjectId().toHexString();
        const userId = 'failed-retry-user';

        const mockUpdateMany = vi.fn().mockResolvedValue({ modifiedCount: 1 });
        app.mongo.db.collection = vi.fn().mockReturnValue({ updateMany: mockUpdateMany });
        app.broadcast = vi.fn();

        await app.inject({
          method: 'POST',
          url: '/internal/status',
          headers: {
            'content-type': 'application/json',
            'x-internal-secret': INTERNAL_SECRET,
          },
          payload: {
            type: 'video.status',
            payload: { videoSummaryId, userId, status: 'failed' },
          },
        });

        // Force-release with token=null because the original publisher's
        // token isn't available cross-process.
        expect(mockContainer.dispatchGuardService.release).toHaveBeenCalledWith(
          videoSummaryId,
          null,
        );
      });

      it('should SKIP dispatch-guard release when tryClaimDispatchRelease returns false (duplicate FAILED event)', async () => {
        // Regression for the duplicate-FAILED race: summarizer status
        // callbacks can fire twice on transient HTTP retry, and the second
        // event would previously do a blind DEL of whatever's in the Redis
        // lock — potentially wiping a fresh dispatch's lock acquired between
        // events. tryClaimDispatchRelease's atomic find-and-update returns
        // false on the second event; the handler must short-circuit.
        const videoSummaryId = new ObjectId().toHexString();
        const userId = 'duplicate-failed-user';

        const mockUpdateMany = vi.fn().mockResolvedValue({ modifiedCount: 1 });
        app.mongo.db.collection = vi.fn().mockReturnValue({ updateMany: mockUpdateMany });
        app.broadcast = vi.fn();
        // Simulate "this is a duplicate FAILED event" — the first event already
        // claimed the release, so this second one sees the field set.
        mockContainer.videoRepository.tryClaimDispatchRelease.mockResolvedValueOnce(false);

        await app.inject({
          method: 'POST',
          url: '/internal/status',
          headers: {
            'content-type': 'application/json',
            'x-internal-secret': INTERNAL_SECRET,
          },
          payload: {
            type: 'video.status',
            payload: { videoSummaryId, userId, status: 'failed' },
          },
        });

        expect(mockContainer.videoRepository.tryClaimDispatchRelease).toHaveBeenCalledWith(
          videoSummaryId,
        );
        expect(mockContainer.dispatchGuardService.release).not.toHaveBeenCalled();
      });

      it('should still release the guard when tryClaimDispatchRelease errors (Mongo blip fallback)', async () => {
        // Fallback path: if the claim itself errors, we fall back to the
        // pre-fix behaviour (unconditional release) so a Mongo outage can't
        // strand a user retry behind the 900s TTL. Worst case is the original
        // race window, which is strictly no worse than where we were.
        const videoSummaryId = new ObjectId().toHexString();
        const userId = 'mongo-blip-user';

        const mockUpdateMany = vi.fn().mockResolvedValue({ modifiedCount: 1 });
        app.mongo.db.collection = vi.fn().mockReturnValue({ updateMany: mockUpdateMany });
        app.broadcast = vi.fn();
        mockContainer.videoRepository.tryClaimDispatchRelease.mockRejectedValueOnce(
          new Error('Mongo connection lost'),
        );

        await app.inject({
          method: 'POST',
          url: '/internal/status',
          headers: {
            'content-type': 'application/json',
            'x-internal-secret': INTERNAL_SECRET,
          },
          payload: {
            type: 'video.status',
            payload: { videoSummaryId, userId, status: 'failed' },
          },
        });

        expect(mockContainer.dispatchGuardService.release).toHaveBeenCalledWith(
          videoSummaryId,
          null,
        );
      });

      it('should NOT release the dispatch guard on COMPLETED (TTL handles natural cleanup)', async () => {
        const videoSummaryId = new ObjectId().toHexString();
        const userId = 'completed-user';

        const mockUpdateMany = vi.fn().mockResolvedValue({ modifiedCount: 1 });
        app.mongo.db.collection = vi.fn().mockReturnValue({ updateMany: mockUpdateMany });
        app.broadcast = vi.fn();

        await app.inject({
          method: 'POST',
          url: '/internal/status',
          headers: {
            'content-type': 'application/json',
            'x-internal-secret': INTERNAL_SECRET,
          },
          payload: {
            type: 'video.status',
            payload: { videoSummaryId, userId, status: 'completed' },
          },
        });

        expect(mockContainer.dispatchGuardService.release).not.toHaveBeenCalled();
      });

      it('should NOT invalidate idempotency keys when a video completes successfully', async () => {
        const videoSummaryId = new ObjectId().toHexString();
        const userId = 'success-user-001';

        const mockUpdateMany = vi.fn().mockResolvedValue({ modifiedCount: 1 });
        app.mongo.db.collection = vi.fn().mockReturnValue({ updateMany: mockUpdateMany });
        app.broadcast = vi.fn();

        await app.inject({
          method: 'POST',
          url: '/internal/status',
          headers: {
            'content-type': 'application/json',
            'x-internal-secret': INTERNAL_SECRET,
          },
          payload: {
            type: 'video.status',
            payload: { videoSummaryId, userId, status: 'completed' },
          },
        });

        expect(mockContainer.idempotencyService.invalidateByVideoSummaryId).not.toHaveBeenCalled();
      });

      it('should settle a shared video\'s run once when the completion event has no userId', async () => {
        const videoSummaryId = new ObjectId().toHexString();
        const sharedUserA = new ObjectId();
        const sharedUserB = new ObjectId();

        const mockUpdateMany = vi.fn().mockResolvedValue({ modifiedCount: 2 });
        // The route reads `_id` in the broadcast loop.
        const mockUserVideosFind = vi.fn().mockReturnValue({
          toArray: vi.fn().mockResolvedValue([
            { _id: new ObjectId(), userId: sharedUserA },
            { _id: new ObjectId(), userId: sharedUserB },
          ]),
        });
        app.mongo.db.collection = vi.fn().mockImplementation((name: string) => {
          if (name === 'userVideos') return { updateMany: mockUpdateMany, find: mockUserVideosFind };
          return { updateMany: mockUpdateMany };
        });
        app.broadcast = vi.fn();

        await app.inject({
          method: 'POST',
          url: '/internal/status',
          headers: {
            'content-type': 'application/json',
            'x-internal-secret': INTERNAL_SECRET,
          },
          payload: {
            type: 'video.status',
            // userId intentionally omitted to exercise the fallback branch
            payload: { videoSummaryId, status: 'completed' },
          },
        });

        // One run, one hold, however many viewers attached to it.
        expect(mockContainer.billingEnforcement.runFinished.mock.calls).toEqual([[videoSummaryId, 'completed']]);
      });

      it('should not touch the ledger when a video transitions to processing', async () => {
        const videoSummaryId = new ObjectId().toHexString();
        const mockUpdateMany = vi.fn().mockResolvedValue({ modifiedCount: 1 });
        const mockCollection = vi.fn().mockReturnValue({
          updateMany: mockUpdateMany,
        });
        app.mongo.db.collection = mockCollection;
        app.broadcast = vi.fn();

        await app.inject({
          method: 'POST',
          url: '/internal/status',
          headers: {
            'content-type': 'application/json',
            'x-internal-secret': INTERNAL_SECRET,
          },
          payload: {
            type: 'video.status',
            payload: {
              videoSummaryId,
              userId: 'u1',
              status: 'processing',
            },
          },
        });

        expect(mockContainer.billingEnforcement.runFinished).not.toHaveBeenCalled();
      });

      it('should accept all valid status values', async () => {
        const statuses = ['pending', 'processing', 'completed', 'failed'];
        const videoSummaryId = new ObjectId().toHexString();

        // Mock MongoDB operations
        const mockUpdateMany = vi.fn().mockResolvedValue({ modifiedCount: 1 });
        const mockFind = vi.fn().mockReturnValue({
          toArray: vi.fn().mockResolvedValue([]),
        });
        const mockCollection = vi.fn().mockReturnValue({
          updateMany: mockUpdateMany,
          find: mockFind,
        });
        app.mongo.db.collection = mockCollection;
        app.broadcast = vi.fn();

        for (const status of statuses) {
          const response = await app.inject({
            method: 'POST',
            url: '/internal/status',
            headers: {
              'content-type': 'application/json',
              'x-internal-secret': INTERNAL_SECRET,
            },
            payload: {
              type: 'video.status',
              payload: {
                videoSummaryId,
                status,
              },
            },
          });

          expect(response.statusCode).toBe(200);
        }
      });

      it('should accept optional message and error fields', async () => {
        const videoSummaryId = new ObjectId().toHexString();

        // Mock MongoDB operations
        const mockUpdateMany = vi.fn().mockResolvedValue({ modifiedCount: 1 });
        const mockFind = vi.fn().mockReturnValue({
          toArray: vi.fn().mockResolvedValue([]),
        });
        const mockCollection = vi.fn().mockReturnValue({
          updateMany: mockUpdateMany,
          find: mockFind,
        });
        app.mongo.db.collection = mockCollection;
        app.broadcast = vi.fn();

        const response = await app.inject({
          method: 'POST',
          url: '/internal/status',
          headers: {
            'content-type': 'application/json',
            'x-internal-secret': INTERNAL_SECRET,
          },
          payload: {
            type: 'video.status',
            payload: {
              videoSummaryId,
              status: 'failed',
              message: 'Processing failed',
              error: 'Transcript not available',
            },
          },
        });

        expect(response.statusCode).toBe(200);
      });
    });

    describe('expansion.status events', () => {
      it('should process expansion.status event for section', async () => {
        const videoSummaryId = new ObjectId().toHexString();
        const mockUserVideos = [
          { _id: new ObjectId(), userId: new ObjectId() },
        ];

        // Mock MongoDB operations
        const mockFind = vi.fn().mockReturnValue({
          toArray: vi.fn().mockResolvedValue(mockUserVideos),
        });
        const mockCollection = vi.fn().mockReturnValue({
          find: mockFind,
        });
        app.mongo.db.collection = mockCollection;

        const mockBroadcast = vi.fn();
        app.broadcast = mockBroadcast;

        const response = await app.inject({
          method: 'POST',
          url: '/internal/status',
          headers: {
            'content-type': 'application/json',
            'x-internal-secret': INTERNAL_SECRET,
          },
          payload: {
            type: 'expansion.status',
            payload: {
              videoSummaryId,
              targetType: 'section',
              targetId: 'section-123',
              status: 'completed',
            },
          },
        });

        expect(response.statusCode).toBe(200);
        expect(response.json()).toEqual({ received: true });

        // Verify find was called to get users
        expect(mockCollection).toHaveBeenCalledWith('userVideos');
        expect(mockFind).toHaveBeenCalledWith({
          videoSummaryId: new ObjectId(videoSummaryId),
        });

        // Verify broadcast was called for each user
        expect(mockBroadcast).toHaveBeenCalled();
      });

      it('should process expansion.status event for concept', async () => {
        const videoSummaryId = new ObjectId().toHexString();
        const mockUserVideos = [
          { _id: new ObjectId(), userId: new ObjectId() },
        ];

        // Mock MongoDB operations
        const mockFind = vi.fn().mockReturnValue({
          toArray: vi.fn().mockResolvedValue(mockUserVideos),
        });
        const mockCollection = vi.fn().mockReturnValue({
          find: mockFind,
        });
        app.mongo.db.collection = mockCollection;
        app.broadcast = vi.fn();

        const response = await app.inject({
          method: 'POST',
          url: '/internal/status',
          headers: {
            'content-type': 'application/json',
            'x-internal-secret': INTERNAL_SECRET,
          },
          payload: {
            type: 'expansion.status',
            payload: {
              videoSummaryId,
              targetType: 'concept',
              targetId: 'concept-456',
              status: 'processing',
            },
          },
        });

        expect(response.statusCode).toBe(200);
      });

      it('should broadcast to all users who have the video', async () => {
        const videoSummaryId = new ObjectId().toHexString();
        const user1Id = new ObjectId();
        const user2Id = new ObjectId();
        const mockUserVideos = [
          { _id: new ObjectId(), userId: user1Id, videoSummaryId: new ObjectId(videoSummaryId) },
          { _id: new ObjectId(), userId: user2Id, videoSummaryId: new ObjectId(videoSummaryId) },
        ];

        // Mock MongoDB operations
        const mockFind = vi.fn().mockReturnValue({
          toArray: vi.fn().mockResolvedValue(mockUserVideos),
        });
        const mockCollection = vi.fn().mockReturnValue({
          find: mockFind,
        });
        app.mongo.db.collection = mockCollection;

        const mockBroadcast = vi.fn();
        app.broadcast = mockBroadcast;

        const response = await app.inject({
          method: 'POST',
          url: '/internal/status',
          headers: {
            'content-type': 'application/json',
            'x-internal-secret': INTERNAL_SECRET,
          },
          payload: {
            type: 'expansion.status',
            payload: {
              videoSummaryId,
              targetType: 'section',
              targetId: 'section-123',
              status: 'completed',
            },
          },
        });

        expect(response.statusCode).toBe(200);

        // Verify broadcast was called for each user
        expect(mockBroadcast).toHaveBeenCalledTimes(2);
        expect(mockBroadcast).toHaveBeenCalledWith(
          user1Id.toHexString(),
          expect.objectContaining({ type: 'expansion.status' })
        );
        expect(mockBroadcast).toHaveBeenCalledWith(
          user2Id.toHexString(),
          expect.objectContaining({ type: 'expansion.status' })
        );
      });

      it('should accept optional error field', async () => {
        const videoSummaryId = new ObjectId().toHexString();

        // Mock MongoDB operations
        const mockFind = vi.fn().mockReturnValue({
          toArray: vi.fn().mockResolvedValue([]),
        });
        const mockCollection = vi.fn().mockReturnValue({
          find: mockFind,
        });
        app.mongo.db.collection = mockCollection;
        app.broadcast = vi.fn();

        const response = await app.inject({
          method: 'POST',
          url: '/internal/status',
          headers: {
            'content-type': 'application/json',
            'x-internal-secret': INTERNAL_SECRET,
          },
          payload: {
            type: 'expansion.status',
            payload: {
              videoSummaryId,
              targetType: 'section',
              targetId: 'section-123',
              status: 'failed',
              error: 'Expansion generation failed',
            },
          },
        });

        expect(response.statusCode).toBe(200);
      });
    });

    describe('validation', () => {
      it('should return 400 for invalid event type', async () => {
        const response = await app.inject({
          method: 'POST',
          url: '/internal/status',
          headers: {
            'content-type': 'application/json',
            'x-internal-secret': INTERNAL_SECRET,
          },
          payload: {
            type: 'invalid.type',
            payload: {
              videoSummaryId: '507f1f77bcf86cd799439011',
              status: 'completed',
            },
          },
        });

        expect(response.statusCode).toBe(400);
        expect(response.json()).toHaveProperty('error', 'Bad Request');
      });

      it('should return 400 for invalid status value', async () => {
        const response = await app.inject({
          method: 'POST',
          url: '/internal/status',
          headers: {
            'content-type': 'application/json',
            'x-internal-secret': INTERNAL_SECRET,
          },
          payload: {
            type: 'video.status',
            payload: {
              videoSummaryId: '507f1f77bcf86cd799439011',
              status: 'invalid-status',
            },
          },
        });

        expect(response.statusCode).toBe(400);
      });

      it('should return 400 for missing videoSummaryId', async () => {
        const response = await app.inject({
          method: 'POST',
          url: '/internal/status',
          headers: {
            'content-type': 'application/json',
            'x-internal-secret': INTERNAL_SECRET,
          },
          payload: {
            type: 'video.status',
            payload: {
              status: 'completed',
            },
          },
        });

        expect(response.statusCode).toBe(400);
      });

      it('should return 400 for missing status', async () => {
        const response = await app.inject({
          method: 'POST',
          url: '/internal/status',
          headers: {
            'content-type': 'application/json',
            'x-internal-secret': INTERNAL_SECRET,
          },
          payload: {
            type: 'video.status',
            payload: {
              videoSummaryId: '507f1f77bcf86cd799439011',
            },
          },
        });

        expect(response.statusCode).toBe(400);
      });

      it('should return 400 for invalid targetType in expansion.status', async () => {
        const response = await app.inject({
          method: 'POST',
          url: '/internal/status',
          headers: {
            'content-type': 'application/json',
            'x-internal-secret': INTERNAL_SECRET,
          },
          payload: {
            type: 'expansion.status',
            payload: {
              videoSummaryId: '507f1f77bcf86cd799439011',
              targetType: 'invalid-target',
              targetId: 'test-123',
              status: 'completed',
            },
          },
        });

        expect(response.statusCode).toBe(400);
      });

      it('should return 400 for missing targetId in expansion.status', async () => {
        const response = await app.inject({
          method: 'POST',
          url: '/internal/status',
          headers: {
            'content-type': 'application/json',
            'x-internal-secret': INTERNAL_SECRET,
          },
          payload: {
            type: 'expansion.status',
            payload: {
              videoSummaryId: '507f1f77bcf86cd799439011',
              targetType: 'section',
              status: 'completed',
            },
          },
        });

        expect(response.statusCode).toBe(400);
      });

      it('should return 400 for empty payload', async () => {
        const response = await app.inject({
          method: 'POST',
          url: '/internal/status',
          headers: {
            'content-type': 'application/json',
            'x-internal-secret': INTERNAL_SECRET,
          },
          payload: {},
        });

        expect(response.statusCode).toBe(400);
      });
    });
  });

  describe('POST /internal/reconcile-costs (removed in P3.2)', () => {
    it('should no longer exist: the repair job keeps the ledger true', async () => {
      const response = await app.inject({
        method: 'POST',
        url: '/internal/reconcile-costs',
        headers: { 'x-internal-secret': INTERNAL_SECRET },
      });

      expect(response.statusCode).toBe(404);
    });
  });

  describe('POST /internal/run-deletions', () => {
    it('should return 401 without x-internal-secret', async () => {
      const response = await app.inject({
        method: 'POST',
        url: '/internal/run-deletions',
        payload: {},
      });
      expect(response.statusCode).toBe(401);
    });

    it('should invoke runScheduledDeletions and return its result', async () => {
      mockContainer.userDeletionService.runScheduledDeletions.mockResolvedValue({
        processed: 3,
        succeeded: 2,
        failed: 1,
        failures: [{ userId: 'abc', error: 'boom' }],
      });

      const response = await app.inject({
        method: 'POST',
        url: '/internal/run-deletions',
        headers: {
          'x-internal-secret': INTERNAL_SECRET,
          'content-type': 'application/json',
        },
        payload: { limit: 10 },
      });

      expect(response.statusCode).toBe(200);
      expect(response.json()).toEqual({
        processed: 3,
        succeeded: 2,
        failed: 1,
        failures: [{ userId: 'abc', error: 'boom' }],
      });
      // First arg is a Date, second is the limit; verify limit forwarded.
      const call = mockContainer.userDeletionService.runScheduledDeletions.mock.calls[0];
      expect(call[0]).toBeInstanceOf(Date);
      expect(call[1]).toBe(10);
    });

    it('should reject limit outside the allowed range', async () => {
      const response = await app.inject({
        method: 'POST',
        url: '/internal/run-deletions',
        headers: {
          'x-internal-secret': INTERNAL_SECRET,
          'content-type': 'application/json',
        },
        payload: { limit: 9999 },
      });
      expect(response.statusCode).toBe(400);
    });
  });
});
