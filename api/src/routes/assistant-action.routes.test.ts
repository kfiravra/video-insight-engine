import { describe, it, expect, beforeAll, afterAll, beforeEach, vi } from 'vitest';
import { FastifyInstance } from 'fastify';
import { buildTestApp, createMockContainer, getAuthHeader, type MockContainer } from '../test/helpers.js';

describe('assistant action proxy route', () => {
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

  it('should return 401 without an auth token', async () => {
    const response = await app.inject({
      method: 'POST',
      url: '/api/assistant/action',
      payload: { action: 'organize_library' },
    });

    expect(response.statusCode).toBe(401);
    expect(mockContainer.assistantClient.action).not.toHaveBeenCalled();
  });

  it('should return 400 for an action outside the canonical enum', async () => {
    const response = await app.inject({
      method: 'POST',
      url: '/api/assistant/action',
      headers: { authorization: authHeader },
      payload: { action: 'drop_database' },
    });

    expect(response.statusCode).toBe(400);
    expect(response.json().error).toBe('VALIDATION_ERROR');
    expect(mockContainer.assistantClient.action).not.toHaveBeenCalled();
  });

  it('should reject params that exceed the value length cap (amplification guard)', async () => {
    const response = await app.inject({
      method: 'POST',
      url: '/api/assistant/action',
      headers: { authorization: authHeader },
      payload: { action: 'save_note', params: { note: 'x'.repeat(4001) } },
    });

    expect(response.statusCode).toBe(400);
    expect(mockContainer.assistantClient.action).not.toHaveBeenCalled();
  });

  it('should forward a library-scoped action with no video_id', async () => {
    mockContainer.assistantClient.action.mockResolvedValue({
      status: 200,
      body: { type: 'organized', message: 'done', trace_id: 't1' },
    });

    const response = await app.inject({
      method: 'POST',
      url: '/api/assistant/action',
      headers: { authorization: authHeader },
      payload: { action: 'organize_library' },
    });

    expect(response.statusCode).toBe(200);
    expect(mockContainer.assistantClient.action).toHaveBeenCalledWith(
      expect.objectContaining({
        action: 'organize_library',
        userId: 'test-user-id',
        videoId: undefined,
      }),
    );
  });

  it('should forward video_id for a video-scoped action', async () => {
    mockContainer.assistantClient.action.mockResolvedValue({
      status: 200,
      body: { type: 'noted', message: 'ok', trace_id: 't1' },
    });

    await app.inject({
      method: 'POST',
      url: '/api/assistant/action',
      headers: { authorization: authHeader },
      payload: { action: 'save_note', video_id: 'abc123', params: { note: 'hi' } },
    });

    expect(mockContainer.assistantClient.action).toHaveBeenCalledWith(
      expect.objectContaining({ action: 'save_note', videoId: 'abc123' }),
    );
  });

  it('should return 404 without forwarding when the user lacks the video summary', async () => {
    mockContainer.videoRepository.userHasAccessToSummary.mockResolvedValueOnce(false);

    const response = await app.inject({
      method: 'POST',
      url: '/api/assistant/action',
      headers: { authorization: authHeader },
      payload: { action: 'save_note', video_id: '65f0c0ffee0000000000abcd', params: { note: 'hi' } },
    });

    expect({ status: response.statusCode, forwarded: mockContainer.assistantClient.action.mock.calls.length })
      .toEqual({ status: 404, forwarded: 0 });
  });

  it('should check library ownership when video_id is a youtubeId', async () => {
    mockContainer.videoRepository.userOwnsVideo.mockResolvedValueOnce(false);

    const response = await app.inject({
      method: 'POST',
      url: '/api/assistant/action',
      headers: { authorization: authHeader },
      payload: { action: 'save_note', video_id: 'dQw4w9WgXcQ', params: { note: 'hi' } },
    });

    expect(response.statusCode).toBe(404);
  });

  it('should return 502 when the assistant client throws', async () => {
    const { ServiceUnavailableError } = await import('../utils/errors.js');
    mockContainer.assistantClient.action.mockRejectedValue(new ServiceUnavailableError('Assistant'));

    const response = await app.inject({
      method: 'POST',
      url: '/api/assistant/action',
      headers: { authorization: authHeader },
      payload: { action: 'organize_library' },
    });

    expect(response.statusCode).toBe(502);
    expect(response.json().error).toBe('SERVICE_UNAVAILABLE');
  });
});
