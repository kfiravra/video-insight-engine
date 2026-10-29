import { describe, it, expect, beforeAll, afterAll, beforeEach, vi } from 'vitest';
import { FastifyInstance } from 'fastify';
import { buildTestApp, createMockContainer, getAuthHeader, type MockContainer } from '../test/helpers.js';

describe('playlists routes', () => {
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

  describe('POST /api/playlists/preview', () => {
    it('should return playlist preview', async () => {
      const mockPreview = {
        id: 'PLtest123',
        title: 'Test Playlist',
        channelTitle: 'Test Channel',
        videoCount: 10,
        videos: [
          { youtubeId: 'abc123', title: 'Video 1' },
          { youtubeId: 'def456', title: 'Video 2' },
        ],
      };
      mockContainer.playlistService.preview.mockResolvedValue(mockPreview);

      const response = await app.inject({
        method: 'POST',
        url: '/api/playlists/preview',
        headers: {
          authorization: authHeader,
          'content-type': 'application/json',
        },
        payload: {
          url: 'https://www.youtube.com/playlist?list=PLtest123',
        },
      });

      expect(response.statusCode).toBe(200);
      expect(mockContainer.playlistService.preview).toHaveBeenCalledWith(
        'https://www.youtube.com/playlist?list=PLtest123',
        100 // default maxVideos
      );
      expect(response.json()).toEqual({ playlist: mockPreview });
    });

    it('should accept custom maxVideos', async () => {
      const mockPreview = { id: 'PLtest123', title: 'Test', videoCount: 5 };
      mockContainer.playlistService.preview.mockResolvedValue(mockPreview);

      const response = await app.inject({
        method: 'POST',
        url: '/api/playlists/preview',
        headers: {
          authorization: authHeader,
          'content-type': 'application/json',
        },
        payload: {
          url: 'https://www.youtube.com/playlist?list=PLtest123',
          maxVideos: 50,
        },
      });

      expect(response.statusCode).toBe(200);
      expect(mockContainer.playlistService.preview).toHaveBeenCalledWith(
        'https://www.youtube.com/playlist?list=PLtest123',
        50
      );
    });

    it('should return 400 for invalid URL', async () => {
      const response = await app.inject({
        method: 'POST',
        url: '/api/playlists/preview',
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

    it('should return 400 when maxVideos exceeds limit', async () => {
      const response = await app.inject({
        method: 'POST',
        url: '/api/playlists/preview',
        headers: {
          authorization: authHeader,
          'content-type': 'application/json',
        },
        payload: {
          url: 'https://www.youtube.com/playlist?list=PLtest123',
          maxVideos: 201, // exceeds 200 max
        },
      });

      expect(response.statusCode).toBe(400);
    });

    it('should return 400 when maxVideos is less than 1', async () => {
      const response = await app.inject({
        method: 'POST',
        url: '/api/playlists/preview',
        headers: {
          authorization: authHeader,
          'content-type': 'application/json',
        },
        payload: {
          url: 'https://www.youtube.com/playlist?list=PLtest123',
          maxVideos: 0,
        },
      });

      expect(response.statusCode).toBe(400);
    });

    it('should return 401 without auth token', async () => {
      const response = await app.inject({
        method: 'POST',
        url: '/api/playlists/preview',
        headers: { 'content-type': 'application/json' },
        payload: {
          url: 'https://www.youtube.com/playlist?list=PLtest123',
        },
      });

      expect(response.statusCode).toBe(401);
    });

    it('should return 404 when playlist not found', async () => {
      const { PlaylistNotFoundError } = await import('../utils/errors.js');
      mockContainer.playlistService.preview.mockRejectedValue(new PlaylistNotFoundError());

      const response = await app.inject({
        method: 'POST',
        url: '/api/playlists/preview',
        headers: {
          authorization: authHeader,
          'content-type': 'application/json',
        },
        payload: {
          url: 'https://www.youtube.com/playlist?list=PLnotfound',
        },
      });

      expect(response.statusCode).toBe(404);
    });

    it('should return 400 for invalid playlist URL', async () => {
      const { InvalidPlaylistUrlError } = await import('../utils/errors.js');
      mockContainer.playlistService.preview.mockRejectedValue(new InvalidPlaylistUrlError());

      const response = await app.inject({
        method: 'POST',
        url: '/api/playlists/preview',
        headers: {
          authorization: authHeader,
          'content-type': 'application/json',
        },
        payload: {
          url: 'https://www.youtube.com/watch?v=abc123', // not a playlist
        },
      });

      expect(response.statusCode).toBe(400);
    });
  });

  describe('POST /api/playlists/import', () => {
    // NOTE: Auth/validation error tests run first to avoid rate limit issues
    // (Route has 5 requests/24h limit, unauthenticated requests count against IP limit)

    it('should return 401 without auth token', async () => {
      const response = await app.inject({
        method: 'POST',
        url: '/api/playlists/import',
        headers: { 'content-type': 'application/json' },
        payload: {
          url: 'https://www.youtube.com/playlist?list=PLtest123',
        },
      });

      expect(response.statusCode).toBe(401);
    });

    it('should return 400 for invalid URL', async () => {
      const response = await app.inject({
        method: 'POST',
        url: '/api/playlists/import',
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

    it('should return 400 for invalid provider', async () => {
      const response = await app.inject({
        method: 'POST',
        url: '/api/playlists/import',
        headers: {
          authorization: authHeader,
          'content-type': 'application/json',
        },
        payload: {
          url: 'https://www.youtube.com/playlist?list=PLtest123',
          providers: {
            default: 'invalid-provider',
          },
        },
      });

      expect(response.statusCode).toBe(400);
    });

    it('should return 400 when playlist is too large', async () => {
      const { PlaylistTooLargeError } = await import('../utils/errors.js');
      mockContainer.playlistService.import.mockRejectedValue(new PlaylistTooLargeError(200));

      const response = await app.inject({
        method: 'POST',
        url: '/api/playlists/import',
        headers: {
          authorization: authHeader,
          'content-type': 'application/json',
        },
        payload: {
          url: 'https://www.youtube.com/playlist?list=PLlarge',
        },
      });

      expect(response.statusCode).toBe(400);
    });

    it('should return 502 when extraction fails', async () => {
      const { PlaylistExtractionError } = await import('../utils/errors.js');
      mockContainer.playlistService.import.mockRejectedValue(
        new PlaylistExtractionError('Failed to fetch playlist data')
      );

      const response = await app.inject({
        method: 'POST',
        url: '/api/playlists/import',
        headers: {
          authorization: authHeader,
          'content-type': 'application/json',
        },
        payload: {
          url: 'https://www.youtube.com/playlist?list=PLerror',
        },
      });

      expect(response.statusCode).toBe(502);
    });

    // Success case tests follow - these count against the rate limit
    it('should import a playlist', async () => {
      const mockResult = {
        playlistId: 'PLtest123',
        title: 'Test Playlist',
        videoCount: 10,
        importedCount: 10,
        skippedCount: 0,
      };
      mockContainer.playlistService.import.mockResolvedValue(mockResult);

      const response = await app.inject({
        method: 'POST',
        url: '/api/playlists/import',
        headers: {
          authorization: authHeader,
          'content-type': 'application/json',
        },
        payload: {
          url: 'https://www.youtube.com/playlist?list=PLtest123',
        },
      });

      expect(response.statusCode).toBe(201);
      expect(mockContainer.playlistService.import).toHaveBeenCalledWith(
        'test-user-id',
        'https://www.youtube.com/playlist?list=PLtest123',
        undefined, // folderId
        100, // default maxVideos
        undefined, // providers
      );
      expect(response.json()).toEqual(mockResult);
    });

    it('should accept optional folderId', async () => {
      const mockResult = { playlistId: 'PLtest123', importedCount: 5 };
      mockContainer.playlistService.import.mockResolvedValue(mockResult);

      const response = await app.inject({
        method: 'POST',
        url: '/api/playlists/import',
        headers: {
          authorization: authHeader,
          'content-type': 'application/json',
        },
        payload: {
          url: 'https://www.youtube.com/playlist?list=PLtest123',
          folderId: '507f1f77bcf86cd799439011',
        },
      });

      expect(response.statusCode).toBe(201);
      expect(mockContainer.playlistService.import).toHaveBeenCalledWith(
        'test-user-id',
        'https://www.youtube.com/playlist?list=PLtest123',
        '507f1f77bcf86cd799439011',
        100,
        undefined,
      );
    });

    it('should accept optional maxVideos', async () => {
      const mockResult = { playlistId: 'PLtest123', importedCount: 25 };
      mockContainer.playlistService.import.mockResolvedValue(mockResult);

      const response = await app.inject({
        method: 'POST',
        url: '/api/playlists/import',
        headers: {
          authorization: authHeader,
          'content-type': 'application/json',
        },
        payload: {
          url: 'https://www.youtube.com/playlist?list=PLtest123',
          maxVideos: 25,
        },
      });

      expect(response.statusCode).toBe(201);
      expect(mockContainer.playlistService.import).toHaveBeenCalledWith(
        'test-user-id',
        'https://www.youtube.com/playlist?list=PLtest123',
        undefined,
        25,
        undefined,
      );
    });

    it('should accept optional providers config', async () => {
      const mockResult = { playlistId: 'PLtest123', importedCount: 10 };
      mockContainer.playlistService.import.mockResolvedValue(mockResult);

      const response = await app.inject({
        method: 'POST',
        url: '/api/playlists/import',
        headers: {
          authorization: authHeader,
          'content-type': 'application/json',
        },
        payload: {
          url: 'https://www.youtube.com/playlist?list=PLtest123',
          providers: {
            default: 'anthropic',
            fast: 'openai',
            fallback: 'gemini',
          },
        },
      });

      expect(response.statusCode).toBe(201);
      expect(mockContainer.playlistService.import).toHaveBeenCalledWith(
        'test-user-id',
        'https://www.youtube.com/playlist?list=PLtest123',
        undefined,
        100,
        {
          default: 'anthropic',
          fast: 'openai',
          fallback: 'gemini',
        },
      );
    });

  });

  describe('GET /api/playlists/:playlistId/videos', () => {
    it('should return videos in a playlist', async () => {
      const mockVideos = [
        { id: 'v1', youtubeId: 'abc123', title: 'Video 1', status: 'completed' },
        { id: 'v2', youtubeId: 'def456', title: 'Video 2', status: 'processing' },
      ];
      mockContainer.playlistService.getPlaylistVideos.mockResolvedValue(mockVideos);

      const response = await app.inject({
        method: 'GET',
        url: '/api/playlists/PLtest123/videos',
        headers: { authorization: authHeader },
      });

      expect(response.statusCode).toBe(200);
      expect(mockContainer.playlistService.getPlaylistVideos).toHaveBeenCalledWith(
        'test-user-id',
        'PLtest123'
      );
      expect(response.json()).toEqual({ videos: mockVideos });
    });

    it('should return empty array when no videos found', async () => {
      mockContainer.playlistService.getPlaylistVideos.mockResolvedValue([]);

      const response = await app.inject({
        method: 'GET',
        url: '/api/playlists/PLempty/videos',
        headers: { authorization: authHeader },
      });

      expect(response.statusCode).toBe(200);
      expect(response.json()).toEqual({ videos: [] });
    });

    it('should return 401 without auth token', async () => {
      const response = await app.inject({
        method: 'GET',
        url: '/api/playlists/PLtest123/videos',
      });

      expect(response.statusCode).toBe(401);
    });

    it('should return 400 for invalid playlist ID format', async () => {
      const response = await app.inject({
        method: 'GET',
        url: '/api/playlists/invalid@id/videos',
        headers: { authorization: authHeader },
      });

      expect(response.statusCode).toBe(400);
    });

    it('should accept valid playlist ID formats', async () => {
      mockContainer.playlistService.getPlaylistVideos.mockResolvedValue([]);

      // Test with alphanumeric, underscore, and hyphen (all valid)
      const response = await app.inject({
        method: 'GET',
        url: '/api/playlists/PL_test-123/videos',
        headers: { authorization: authHeader },
      });

      expect(response.statusCode).toBe(200);
      expect(mockContainer.playlistService.getPlaylistVideos).toHaveBeenCalledWith(
        'test-user-id',
        'PL_test-123'
      );
    });

    it('should return 404 when playlist not found', async () => {
      const { PlaylistNotFoundError } = await import('../utils/errors.js');
      mockContainer.playlistService.getPlaylistVideos.mockRejectedValue(new PlaylistNotFoundError());

      const response = await app.inject({
        method: 'GET',
        url: '/api/playlists/PLnotfound/videos',
        headers: { authorization: authHeader },
      });

      expect(response.statusCode).toBe(404);
    });
  });
});
