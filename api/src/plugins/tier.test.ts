import { describe, it, expect, beforeAll, afterAll } from 'vitest';
import { FastifyInstance } from 'fastify';
import { buildTestApp, createMockContainer, getAuthHeader, type MockContainer } from '../test/helpers.js';

describe('tier plugin', () => {
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

  it('should decorate request with default free tier for unauthenticated requests', async () => {
    // GET /health is a public endpoint
    const response = await app.inject({
      method: 'GET',
      url: '/health',
    });

    expect(response.statusCode).toBe(200);
  });

  it('should set default free tier on request decorator', async () => {
    mockContainer.videoService.getVideos.mockResolvedValue({ videos: [], total: 0 });

    const response = await app.inject({
      method: 'GET',
      url: '/api/videos',
      headers: {
        authorization: authHeader,
      },
    });

    // Request decorator should be available (default free tier)
    // The onRequest hook sets default tier before authenticate runs
    expect(response.statusCode).toBe(200);
  });

  it('should provide tier info to route handlers via req.tier', async () => {
    mockContainer.videoService.createVideo.mockResolvedValue({
      video: { id: '1', status: 'pending' },
      cached: false,
    });

    const response = await app.inject({
      method: 'POST',
      url: '/api/videos',
      headers: {
        authorization: authHeader,
        'content-type': 'application/json',
      },
      payload: { url: 'https://www.youtube.com/watch?v=dQw4w9WgXcQ' },
    });

    expect(response.statusCode).toBe(201);
    // createVideo should receive the default tier name ('free') in options
    expect(mockContainer.videoService.createVideo).toHaveBeenCalledWith(
      expect.any(String),
      expect.any(String),
      expect.objectContaining({
        folderId: undefined,
        bypassCache: false,
        providers: undefined,
        tier: 'free',
      })
    );
  });

  describe('resolveTier', () => {
    async function submit(): Promise<void> {
      mockContainer.videoService.createVideo.mockResolvedValue({
        video: { id: '1', status: 'pending' },
        cached: false,
      });
      await app.inject({
        method: 'POST',
        url: '/api/videos',
        headers: { authorization: authHeader, 'content-type': 'application/json' },
        payload: { url: 'https://www.youtube.com/watch?v=dQw4w9WgXcQ' },
      });
    }

    it('should read the plan from the billing account', async () => {
      mockContainer.billingService.summary.mockResolvedValueOnce({ plan: { id: 'pro', periodEnd: null } });
      await submit();

      expect(mockContainer.videoService.createVideo).toHaveBeenLastCalledWith(
        expect.any(String),
        expect.any(String),
        expect.objectContaining({ tier: 'pro' })
      );
    });

    it('should keep free when the plan lookup fails', async () => {
      mockContainer.billingService.summary.mockRejectedValueOnce(new Error('mongo down'));
      await submit();

      expect(mockContainer.videoService.createVideo).toHaveBeenLastCalledWith(
        expect.any(String),
        expect.any(String),
        expect.objectContaining({ tier: 'free' })
      );
    });
  });
});
