import { describe, it, expect, beforeAll, afterAll, beforeEach, vi } from 'vitest';
import { FastifyInstance } from 'fastify';
import { buildTestApp, createMockContainer, getAuthHeader, type MockContainer } from '../test/helpers.js';

describe('share routes', () => {
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

  describe('POST /api/share/:videoSummaryId', () => {
    const validVideoSummaryId = '507f1f77bcf86cd799439011';

    it('should return 201 with share slug when authenticated', async () => {
      const mockResult = { slug: 'aBcDeFgHiJ', url: '/s/aBcDeFgHiJ' };
      mockContainer.shareService.createShare.mockResolvedValue(mockResult);

      const response = await app.inject({
        method: 'POST',
        url: `/api/share/${validVideoSummaryId}`,
        headers: { authorization: authHeader },
      });

      expect(response.statusCode).toBe(201);
      expect(response.json()).toEqual(mockResult);
      expect(mockContainer.shareService.createShare).toHaveBeenCalledWith(
        'test-user-id',
        validVideoSummaryId
      );
    });

    it('should return 401 without auth token', async () => {
      const response = await app.inject({
        method: 'POST',
        url: `/api/share/${validVideoSummaryId}`,
      });

      expect(response.statusCode).toBe(401);
      expect(mockContainer.shareService.createShare).not.toHaveBeenCalled();
    });

    it('should return 400 for invalid ObjectId format', async () => {
      const response = await app.inject({
        method: 'POST',
        url: '/api/share/not-a-valid-objectid',
        headers: { authorization: authHeader },
      });

      expect(response.statusCode).toBe(400);
      expect(mockContainer.shareService.createShare).not.toHaveBeenCalled();
    });

    it('should return 404 when video summary not found', async () => {
      const { VideoSummaryNotFoundError } = await import('../utils/errors.js');
      mockContainer.shareService.createShare.mockRejectedValue(new VideoSummaryNotFoundError());

      const response = await app.inject({
        method: 'POST',
        url: `/api/share/${validVideoSummaryId}`,
        headers: { authorization: authHeader },
      });

      expect(response.statusCode).toBe(404);
    });

    it('should return 409 when video is already shared', async () => {
      const { AlreadySharedError } = await import('../utils/errors.js');
      mockContainer.shareService.createShare.mockRejectedValue(new AlreadySharedError('aBcDeFgHiJ'));

      const response = await app.inject({
        method: 'POST',
        url: `/api/share/${validVideoSummaryId}`,
        headers: { authorization: authHeader },
      });

      expect(response.statusCode).toBe(409);
    });
  });

  describe('GET /api/share/:slug', () => {
    const validSlug = 'aBcDeFgHiJ';

    it('should return public summary without auth', async () => {
      const mockSummary = {
        title: 'Test Video',
        slug: validSlug,
        chapters: [{ title: 'Chapter 1', summary: 'Summary text' }],
        likes: 5,
        views: 42,
      };
      mockContainer.shareService.getPublicSummary.mockResolvedValue(mockSummary);

      const response = await app.inject({
        method: 'GET',
        url: `/api/share/${validSlug}`,
      });

      expect(response.statusCode).toBe(200);
      expect(response.json()).toEqual(mockSummary);
      expect(mockContainer.shareService.getPublicSummary).toHaveBeenCalledWith(validSlug, expect.any(String));
    });

    it('should return 400 for invalid slug format - too short', async () => {
      const response = await app.inject({
        method: 'GET',
        url: '/api/share/abc',
      });

      expect(response.statusCode).toBe(400);
      expect(mockContainer.shareService.getPublicSummary).not.toHaveBeenCalled();
    });

    it('should return 400 for invalid slug format - too long', async () => {
      const response = await app.inject({
        method: 'GET',
        url: '/api/share/aBcDeFgHiJkL',
      });

      expect(response.statusCode).toBe(400);
      expect(mockContainer.shareService.getPublicSummary).not.toHaveBeenCalled();
    });

    it('should return 400 for invalid slug format - special characters', async () => {
      const response = await app.inject({
        method: 'GET',
        url: '/api/share/aBcD!FgHiJ',
      });

      expect(response.statusCode).toBe(400);
      expect(mockContainer.shareService.getPublicSummary).not.toHaveBeenCalled();
    });

    it('should accept slug with hyphens and underscores', async () => {
      const slugWithSpecials = 'aB-D_FgHiJ';
      mockContainer.shareService.getPublicSummary.mockResolvedValue({ title: 'Test' });

      const response = await app.inject({
        method: 'GET',
        url: `/api/share/${slugWithSpecials}`,
      });

      expect(response.statusCode).toBe(200);
      expect(mockContainer.shareService.getPublicSummary).toHaveBeenCalledWith(slugWithSpecials, expect.any(String));
    });

    it('should return 404 when share not found', async () => {
      const { ShareNotFoundError } = await import('../utils/errors.js');
      mockContainer.shareService.getPublicSummary.mockRejectedValue(new ShareNotFoundError());

      const response = await app.inject({
        method: 'GET',
        url: `/api/share/${validSlug}`,
      });

      expect(response.statusCode).toBe(404);
    });
  });

  describe('POST /api/share/:slug/like', () => {
    const validSlug = 'aBcDeFgHiJ';

    it('should return like count without auth', async () => {
      const mockResult = { likes: 6 };
      mockContainer.shareService.likeShare.mockResolvedValue(mockResult);

      const response = await app.inject({
        method: 'POST',
        url: `/api/share/${validSlug}/like`,
      });

      expect(response.statusCode).toBe(200);
      expect(response.json()).toEqual(mockResult);
      expect(mockContainer.shareService.likeShare).toHaveBeenCalledWith(
        validSlug,
        expect.any(String)
      );
    });

    it('should pass client IP to likeShare', async () => {
      mockContainer.shareService.likeShare.mockResolvedValue({ likes: 1 });

      await app.inject({
        method: 'POST',
        url: `/api/share/${validSlug}/like`,
        remoteAddress: '192.168.1.100',
      });

      expect(mockContainer.shareService.likeShare).toHaveBeenCalledWith(
        validSlug,
        expect.any(String)
      );
    });

    it('should return 400 for invalid slug format', async () => {
      const response = await app.inject({
        method: 'POST',
        url: '/api/share/bad/like',
      });

      expect(response.statusCode).toBe(400);
      expect(mockContainer.shareService.likeShare).not.toHaveBeenCalled();
    });

    it('should return 400 for slug with invalid characters', async () => {
      const response = await app.inject({
        method: 'POST',
        url: '/api/share/ab@deFgHiJ/like',
      });

      expect(response.statusCode).toBe(400);
      expect(mockContainer.shareService.likeShare).not.toHaveBeenCalled();
    });

    it('should return 404 when share not found', async () => {
      const { ShareNotFoundError } = await import('../utils/errors.js');
      mockContainer.shareService.likeShare.mockRejectedValue(new ShareNotFoundError());

      const response = await app.inject({
        method: 'POST',
        url: `/api/share/${validSlug}/like`,
      });

      expect(response.statusCode).toBe(404);
    });
  });
});
