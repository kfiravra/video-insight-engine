import { describe, it, expect, beforeAll, afterAll, beforeEach, vi } from 'vitest';
import { FastifyInstance } from 'fastify';
import { buildTestApp, createMockContainer, type MockContainer } from '../../../test/helpers.js';
import { config } from '../../../config.js';
import { SummarizerPurgeError } from '../../../utils/errors.js';

const YT = 'dQw4w9WgXcQ';
const cascadeResult = {
  scope: 'global',
  youtubeId: YT,
  summaryIds: ['6a7b2f6abacdb32871507996'],
  counts: { videoSummaryCache: 1 },
  warnings: [],
};

describe('admin videos routes', () => {
  let app: FastifyInstance;
  let mockContainer: MockContainer;
  const adminHeaders = { 'x-admin-key': config.ADMIN_API_KEY };

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
    mockContainer.videoCascadeService.deleteVideo.mockResolvedValue(cascadeResult);
  });

  it('should reject without the admin key', async () => {
    const response = await app.inject({ method: 'DELETE', url: `/api/admin/videos/${YT}` });

    expect(response.statusCode).toBe(401);
    expect(mockContainer.videoCascadeService.deleteVideo).not.toHaveBeenCalled();
  });

  it('should reject a malformed youtube id', async () => {
    const response = await app.inject({
      method: 'DELETE',
      url: '/api/admin/videos/not-a-youtube-id',
      headers: adminHeaders,
    });

    expect(response.statusCode).toBe(400);
    expect(mockContainer.videoCascadeService.deleteVideo).not.toHaveBeenCalled();
  });

  it('should reject a malformed x-admin-id header', async () => {
    const response = await app.inject({
      method: 'DELETE',
      url: `/api/admin/videos/${YT}`,
      headers: { ...adminHeaders, 'x-admin-id': 'not valid!' },
    });

    expect(response.statusCode).toBe(400);
    expect(mockContainer.videoCascadeService.deleteVideo).not.toHaveBeenCalled();
  });

  it('should run the global cascade with the operator id and reason', async () => {
    const response = await app.inject({
      method: 'DELETE',
      url: `/api/admin/videos/${YT}`,
      headers: { ...adminHeaders, 'x-admin-id': 'kfir', 'content-type': 'application/json' },
      payload: { reason: 'duplicate upload' },
    });

    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual(cascadeResult);
    expect(mockContainer.videoCascadeService.deleteVideo).toHaveBeenCalledWith({
      scope: 'global',
      youtubeId: YT,
      actor: { initiatedBy: 'admin', adminId: 'kfir', reason: 'duplicate upload' },
    });
  });

  it('should answer 502 when the summarizer purge fails', async () => {
    mockContainer.videoCascadeService.deleteVideo.mockRejectedValue(new SummarizerPurgeError('down'));

    const response = await app.inject({
      method: 'DELETE',
      url: `/api/admin/videos/${YT}`,
      headers: adminHeaders,
    });

    expect(response.statusCode).toBe(502);
    expect(response.json()).toMatchObject({ error: 'SUMMARIZER_PURGE_FAILED' });
  });
});
