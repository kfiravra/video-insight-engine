import { describe, it, expect, beforeAll, afterAll, beforeEach, vi } from 'vitest';
import { FastifyInstance } from 'fastify';
import { ObjectId } from 'mongodb';
import { buildTestApp, createMockContainer, type MockContainer } from '../test/helpers.js';

// The default internal secret from config (services/config.ts default).
const INTERNAL_SECRET = 'dev-internal-secret-change-me';
const USER_A = 'aaaaaaaaaaaaaaaaaaaaaaaa';
const USER_B = 'bbbbbbbbbbbbbbbbbbbbbbbb';
const FOLDER_ID = '507f1f77bcf86cd799439011';

function internalHeaders(userId: string, secret = INTERNAL_SECRET): Record<string, string> {
  return {
    'content-type': 'application/json',
    'x-internal-secret': secret,
    'x-user-id': userId,
  };
}

describe('internal assistant routes', () => {
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

  describe('authenticateInternal', () => {
    it('should return 401 when x-internal-secret header is missing', async () => {
      const response = await app.inject({
        method: 'GET',
        url: '/internal/assistant/folders',
        headers: { 'x-user-id': USER_A },
      });

      expect(response.statusCode).toBe(401);
    });

    it('should return 401 when x-internal-secret is invalid', async () => {
      const response = await app.inject({
        method: 'GET',
        url: '/internal/assistant/folders',
        headers: internalHeaders(USER_A, 'wrong-secret'),
      });

      expect(response.statusCode).toBe(401);
    });

    it('should return 401 when x-user-id header is missing', async () => {
      const response = await app.inject({
        method: 'GET',
        url: '/internal/assistant/folders',
        headers: { 'x-internal-secret': INTERNAL_SECRET },
      });

      expect(response.statusCode).toBe(401);
    });

    it('should pass with a valid secret and x-user-id', async () => {
      mockContainer.folderService.list.mockResolvedValue([]);

      const response = await app.inject({
        method: 'GET',
        url: '/internal/assistant/folders',
        headers: internalHeaders(USER_A),
      });

      expect(response.statusCode).toBe(200);
      expect(mockContainer.folderService.list).toHaveBeenCalledWith(USER_A);
    });
  });

  describe('GET /internal/assistant/folders', () => {
    it('should return folders in the success envelope scoped to x-user-id', async () => {
      const folders = [{ id: 'f1', name: 'Work' }];
      mockContainer.folderService.list.mockResolvedValue(folders);

      const response = await app.inject({
        method: 'GET',
        url: '/internal/assistant/folders',
        headers: internalHeaders(USER_A),
      });

      expect(response.statusCode).toBe(200);
      expect(response.json()).toEqual({ success: true, data: folders });
      expect(mockContainer.folderService.list).toHaveBeenCalledWith(USER_A);
    });
  });

  describe('POST /internal/assistant/folders', () => {
    it('should create a folder using x-user-id, not a body-supplied userId', async () => {
      const created = { id: 'f1', name: 'New' };
      mockContainer.folderService.create.mockResolvedValue(created);

      const response = await app.inject({
        method: 'POST',
        url: '/internal/assistant/folders',
        headers: internalHeaders(USER_A),
        // A malicious body userId must be ignored — the header is the source of truth.
        payload: { name: 'New', userId: USER_B },
      });

      expect(response.statusCode).toBe(201);
      expect(response.json()).toEqual({ success: true, data: created });
      expect(mockContainer.folderService.create).toHaveBeenCalledWith({
        userId: USER_A,
        name: 'New',
      });
    });

    it('should return 400 when name is missing', async () => {
      const response = await app.inject({
        method: 'POST',
        url: '/internal/assistant/folders',
        headers: internalHeaders(USER_A),
        payload: {},
      });

      expect(response.statusCode).toBe(400);
      expect(mockContainer.folderService.create).not.toHaveBeenCalled();
    });
  });

  describe('PATCH /internal/assistant/folders/:id', () => {
    it('should update only with the header user, isolating user A from user B', async () => {
      const updated = { id: FOLDER_ID, name: 'Renamed' };
      mockContainer.folderService.update.mockResolvedValue(updated);

      // User B attempts to mutate a folder while claiming to be user A in the body.
      const response = await app.inject({
        method: 'PATCH',
        url: `/internal/assistant/folders/${FOLDER_ID}`,
        headers: internalHeaders(USER_B),
        payload: { name: 'Renamed', userId: USER_A },
      });

      expect(response.statusCode).toBe(200);
      // Service is scoped to USER_B (the header), never USER_A.
      expect(mockContainer.folderService.update).toHaveBeenCalledWith(
        USER_B,
        FOLDER_ID,
        { name: 'Renamed' },
      );
    });
  });

  describe('DELETE /internal/assistant/folders/:id', () => {
    it('should delete with deleteContent=true coerced to a boolean', async () => {
      mockContainer.folderService.delete.mockResolvedValue(undefined);

      const response = await app.inject({
        method: 'DELETE',
        url: `/internal/assistant/folders/${FOLDER_ID}?deleteContent=true`,
        headers: internalHeaders(USER_A),
      });

      expect(response.statusCode).toBe(200);
      expect(response.json()).toEqual({ success: true, data: { id: FOLDER_ID } });
      expect(mockContainer.folderService.delete).toHaveBeenCalledWith(USER_A, FOLDER_ID, true);
    });

    it('should default deleteContent to false when omitted', async () => {
      mockContainer.folderService.delete.mockResolvedValue(undefined);

      const response = await app.inject({
        method: 'DELETE',
        url: `/internal/assistant/folders/${FOLDER_ID}`,
        headers: internalHeaders(USER_A),
      });

      expect(response.statusCode).toBe(200);
      expect(mockContainer.folderService.delete).toHaveBeenCalledWith(USER_A, FOLDER_ID, false);
    });
  });

  describe('GET /internal/assistant/videos', () => {
    it('should map owned videos to {videoSummaryId,youtubeId,title,folderId}', async () => {
      const summaryId = new ObjectId('507f1f77bcf86cd799439012');
      const folderId = new ObjectId(FOLDER_ID);
      mockContainer.videoRepository.getUserVideos.mockResolvedValue([
        {
          _id: new ObjectId('507f1f77bcf86cd799439021'),
          videoSummaryId: summaryId,
          youtubeId: 'dQw4w9WgXcQ',
          folderId,
          cache: { title: 'Cached Title' },
        },
        {
          _id: new ObjectId('507f1f77bcf86cd799439022'),
          videoSummaryId: summaryId,
          youtubeId: 'abc123',
          folderId: null,
          title: 'Fallback Title',
        },
      ]);

      const response = await app.inject({
        method: 'GET',
        url: '/internal/assistant/videos',
        headers: internalHeaders(USER_A),
      });

      expect(response.statusCode).toBe(200);
      expect(response.json()).toEqual({
        success: true,
        data: [
          {
            id: '507f1f77bcf86cd799439021',
            videoSummaryId: summaryId.toString(),
            youtubeId: 'dQw4w9WgXcQ',
            title: 'Cached Title',
            folderId: FOLDER_ID,
          },
          {
            id: '507f1f77bcf86cd799439022',
            videoSummaryId: summaryId.toString(),
            youtubeId: 'abc123',
            title: 'Fallback Title',
            folderId: null,
          },
        ],
      });
      expect(mockContainer.videoRepository.getUserVideos).toHaveBeenCalledWith(
        USER_A,
        undefined,
        { limit: 200 },
      );
    });

    it('should map up to the cap without error when the library is at the limit', async () => {
      const summaryId = new ObjectId('507f1f77bcf86cd799439012');
      const atCap = Array.from({ length: 200 }, (_, i) => ({
        _id: new ObjectId(),
        videoSummaryId: summaryId,
        youtubeId: `yt${i}`,
        folderId: null,
      }));
      mockContainer.videoRepository.getUserVideos.mockResolvedValue(atCap);

      const response = await app.inject({
        method: 'GET',
        url: '/internal/assistant/videos',
        headers: internalHeaders(USER_A),
      });

      expect(response.statusCode).toBe(200);
      expect(response.json().data).toHaveLength(200);
    });
  });

  describe('PATCH /internal/assistant/videos/:id/move', () => {
    it('should move a video for the header user with a null folderId', async () => {
      mockContainer.videoService.moveToFolder.mockResolvedValue({ success: true });

      const response = await app.inject({
        method: 'PATCH',
        url: `/internal/assistant/videos/${FOLDER_ID}/move`,
        headers: internalHeaders(USER_A),
        payload: { folderId: null },
      });

      expect(response.statusCode).toBe(200);
      expect(response.json()).toEqual({ success: true, data: { success: true } });
      expect(mockContainer.videoService.moveToFolder).toHaveBeenCalledWith(USER_A, FOLDER_ID, null);
    });
  });

  describe('POST /internal/assistant/generate', () => {
    it('should call createVideo for the header user with a free-video run queued as free', async () => {
      mockContainer.videoService.createVideo.mockResolvedValue({
        video: { id: 'v1', youtubeId: 'dQw4w9WgXcQ', status: 'pending' },
        cached: false,
      });

      const response = await app.inject({
        method: 'POST',
        url: '/internal/assistant/generate',
        headers: internalHeaders(USER_A),
        payload: { url: 'https://youtu.be/dQw4w9WgXcQ', folderId: FOLDER_ID },
      });

      expect(response.statusCode).toBe(200);
      expect(mockContainer.videoService.createVideo).toHaveBeenCalledWith(
        USER_A,
        'https://youtu.be/dQw4w9WgXcQ',
        expect.objectContaining({ queuePlan: 'free', folderId: FOLDER_ID }),
      );
    });

    it('should queue a run paid with plan minutes as paid', async () => {
      mockContainer.billingEnforcement.admit.mockResolvedValueOnce({ requestId: 'r', minutes: 10, split: { free: 0, plan: 10, credit: 0, lots: [] } });
      mockContainer.videoService.createVideo.mockResolvedValue({ video: {}, cached: false });

      await app.inject({
        method: 'POST',
        url: '/internal/assistant/generate',
        headers: internalHeaders(USER_A),
        payload: { url: 'https://youtu.be/dQw4w9WgXcQ' },
      });

      expect(mockContainer.videoService.createVideo.mock.calls[0][2]).toMatchObject({ queuePlan: 'paid' });
    });

    it('should return 400 when url is missing', async () => {
      const response = await app.inject({
        method: 'POST',
        url: '/internal/assistant/generate',
        headers: internalHeaders(USER_A),
        payload: {},
      });

      expect(response.statusCode).toBe(400);
      expect(mockContainer.videoService.createVideo).not.toHaveBeenCalled();
    });

    it('should refuse a blocked account and never call createVideo', async () => {
      // Regression test for the internal bypass: before the submission-service
      // extraction, this route called createVideo directly and the assistant
      // could submit pipeline runs past the account's gate.
      const { AccountBlockedError } = await import('../utils/errors.js');
      mockContainer.billingEnforcement.admit.mockRejectedValueOnce(new AccountBlockedError());

      const response = await app.inject({
        method: 'POST',
        url: '/internal/assistant/generate',
        headers: internalHeaders(USER_A),
        payload: { url: 'https://youtu.be/dQw4w9WgXcQ' },
      });

      expect([response.statusCode, response.json().error]).toEqual([403, 'ACCOUNT_BLOCKED']);
      expect(mockContainer.billingEnforcement.admit).toHaveBeenCalledWith(expect.objectContaining({ userId: USER_A, youtubeId: 'dQw4w9WgXcQ' }));
      expect(mockContainer.videoService.createVideo).not.toHaveBeenCalled();
      // The reserved idempotency hash is unwound so a later retry isn't locked out.
      expect(mockContainer.idempotencyService.invalidateByHash).toHaveBeenCalledWith('test-hash');
    });

    it('should answer 402 when the minutes cannot pay and nothing is cached to serve', async () => {
      const { InsufficientMinutesError, RunNotAllowedError } = await import('../utils/errors.js');
      mockContainer.billingEnforcement.admit.mockRejectedValueOnce(new InsufficientMinutesError(10, 0, null));
      mockContainer.videoService.createVideo.mockRejectedValueOnce(new RunNotAllowedError());

      const response = await app.inject({
        method: 'POST',
        url: '/internal/assistant/generate',
        headers: internalHeaders(USER_A),
        payload: { url: 'https://youtu.be/dQw4w9WgXcQ' },
      });

      expect([response.statusCode, response.json().error]).toEqual([402, 'INSUFFICIENT_MINUTES']);
      expect(mockContainer.videoService.createVideo.mock.calls[0][2]).toMatchObject({ serveOnly: true });
    });

    it('should give the hold back and unwind the hash when createVideo throws', async () => {
      mockContainer.videoService.createVideo.mockRejectedValue(new Error('downstream-blew-up'));

      const response = await app.inject({
        method: 'POST',
        url: '/internal/assistant/generate',
        headers: internalHeaders(USER_A),
        payload: { url: 'https://youtu.be/dQw4w9WgXcQ' },
      });

      expect(response.statusCode).toBe(500);
      const runId = mockContainer.videoService.createVideo.mock.calls[0][2].billingRunId;
      expect(mockContainer.billingEnforcement.submitFailed).toHaveBeenCalledWith(runId);
      expect(mockContainer.idempotencyService.invalidateByHash).toHaveBeenCalledWith('test-hash');
    });

    it('should give the hold back when the video is served from cache', async () => {
      mockContainer.videoService.createVideo.mockResolvedValue({
        video: { id: 'v1', videoSummaryId: 'sum1', status: 'completed' },
        cached: true,
      });

      const response = await app.inject({
        method: 'POST',
        url: '/internal/assistant/generate',
        headers: internalHeaders(USER_A),
        payload: { url: 'https://youtu.be/dQw4w9WgXcQ' },
      });

      expect(response.statusCode).toBe(200);
      const runId = mockContainer.videoService.createVideo.mock.calls[0][2].billingRunId;
      expect(mockContainer.billingEnforcement.submitResolved).toHaveBeenCalledWith(runId, 'cached');
    });

    it('should return 409 when the same submission is already in flight', async () => {
      mockContainer.idempotencyService.reserveHash.mockResolvedValue({
        created: false,
        doc: {
          _id: { toString: () => 'idem-pending' },
          hash: 'test-hash',
          status: 'pending',
          userId: { toString: () => USER_A },
          youtubeId: 'dQw4w9WgXcQ',
        },
      });

      const response = await app.inject({
        method: 'POST',
        url: '/internal/assistant/generate',
        headers: internalHeaders(USER_A),
        payload: { url: 'https://youtu.be/dQw4w9WgXcQ' },
      });

      expect(response.statusCode).toBe(409);
      expect(response.json().error).toBe('IDEMPOTENCY_IN_FLIGHT');
      expect(mockContainer.billingEnforcement.admit).not.toHaveBeenCalled();
      expect(mockContainer.videoService.createVideo).not.toHaveBeenCalled();
    });
  });
});
