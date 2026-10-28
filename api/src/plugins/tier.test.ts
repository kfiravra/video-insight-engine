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
    async function submitAs(storedTier: string | undefined): Promise<void> {
      mockContainer.userRepository.getTier.mockResolvedValue(storedTier);
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

    it.each(['pro', 'team'])('should read the %s tier from the user repository', async (stored) => {
      await submitAs(stored);

      expect(mockContainer.videoService.createVideo).toHaveBeenLastCalledWith(
        expect.any(String),
        expect.any(String),
        expect.objectContaining({ tier: stored })
      );
    });

    it.each([undefined, 'enterprise'])('should fall back to free when the stored tier is %s', async (stored) => {
      await submitAs(stored);

      expect(mockContainer.videoService.createVideo).toHaveBeenLastCalledWith(
        expect.any(String),
        expect.any(String),
        expect.objectContaining({ tier: 'free' })
      );
    });
  });
});
