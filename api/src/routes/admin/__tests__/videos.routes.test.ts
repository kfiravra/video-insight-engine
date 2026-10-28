import { describe, it, expect, beforeAll, afterAll, beforeEach, vi } from 'vitest';
import { FastifyInstance } from 'fastify';
import { ObjectId } from 'mongodb';
import {
  adminActorRequest,
  buildTestApp,
  createMockContainer,
  TEST_ADMIN_USER_ID,
  type MockContainer,
} from '../../../test/helpers.js';
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
    mockContainer.userRepository.hasPermission.mockResolvedValue(true);
    mockContainer.adminReplayGuard.claim.mockResolvedValue(true);
  });

  /** A purge signed the way vie-admin signs it: method, exact target and body bound. */
  async function purge(body?: Record<string, unknown>, id = YT, options: { nowSeconds?: number } = {}) {
    const url = `/api/admin/videos/${id}`;
    const signed = adminActorRequest('DELETE', url, body, options);
    return app.inject({ method: 'DELETE', url, headers: signed.headers, payload: signed.payload });
  }

  it('should reject without the admin key', async () => {
    const response = await app.inject({ method: 'DELETE', url: `/api/admin/videos/${YT}` });

    expect(response.statusCode).toBe(401);
    expect(mockContainer.videoCascadeService.deleteVideo).not.toHaveBeenCalled();
  });

  it('should refuse the shared key alone: a purge needs a named admin', async () => {
    const response = await app.inject({
      method: 'DELETE',
      url: `/api/admin/videos/${YT}`,
      headers: { 'x-admin-key': config.ADMIN_API_KEY },
    });

    expect(response.statusCode).toBe(403);
    expect(mockContainer.videoCascadeService.deleteVideo).not.toHaveBeenCalled();
  });

  it('should refuse an admin who no longer holds videos:purge', async () => {
    mockContainer.userRepository.hasPermission.mockResolvedValue(false);

    const response = await purge();

    expect(response.statusCode).toBe(403);
    expect(mockContainer.videoCascadeService.deleteVideo).not.toHaveBeenCalled();
  });

  it('should check the signed admin against the videos:purge permission', async () => {
    await purge();

    expect(mockContainer.userRepository.hasPermission).toHaveBeenCalledWith(TEST_ADMIN_USER_ID, 'videos:purge');
  });

  it('should reject a stale actor header', async () => {
    const response = await purge(undefined, YT, { nowSeconds: Math.floor(Date.now() / 1000) - 301 });

    expect(response.statusCode).toBe(401);
    expect(mockContainer.videoCascadeService.deleteVideo).not.toHaveBeenCalled();
  });

  it('should reject a malformed youtube id', async () => {
    const response = await purge(undefined, 'not-a-youtube-id');

    expect(response.statusCode).toBe(400);
    expect(mockContainer.videoCascadeService.deleteVideo).not.toHaveBeenCalled();
  });

  it('should run the global cascade with the signed admin and reason', async () => {
    const response = await purge({ reason: 'duplicate upload' });

    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual(cascadeResult);
    expect(mockContainer.videoCascadeService.deleteVideo).toHaveBeenCalledWith({
      scope: 'global',
      youtubeId: YT,
      actor: { initiatedBy: 'admin', adminId: TEST_ADMIN_USER_ID, reason: 'duplicate upload' },
    });
  });

  it('should ignore an x-admin-id header: the actor is only ever the signed one', async () => {
    const url = `/api/admin/videos/${YT}`;
    const signed = adminActorRequest('DELETE', url);

    await app.inject({ method: 'DELETE', url, headers: { ...signed.headers, 'x-admin-id': 'someone-else' } });

    expect(mockContainer.videoCascadeService.deleteVideo).toHaveBeenCalledWith(
      expect.objectContaining({ actor: expect.objectContaining({ adminId: TEST_ADMIN_USER_ID }) }),
    );
  });

  it('should audit the purge with the signed admin, video and reason', async () => {
    await purge({ reason: 'duplicate upload' });

    expect(mockContainer.auditService.recordBestEffort).toHaveBeenCalledWith({
      actorId: TEST_ADMIN_USER_ID,
      requestId: expect.any(String),
      ip: expect.any(String),
      action: 'videos.purge',
      targetType: 'video',
      targetId: YT,
      before: { summaryIds: [], shared: false },
      after: { summaryIds: cascadeResult.summaryIds, counts: cascadeResult.counts, partial: undefined },
      reason: 'duplicate upload',
    });
  });

  it('should record in before what the purge is about to delete, including whether it was shared', async () => {
    const summaryId = new ObjectId();
    mockContainer.videoRepository.findCacheKeysByYoutubeId.mockResolvedValue([
      { _id: summaryId, shareSlug: 'abc123' },
      { _id: new ObjectId() },
    ]);

    await purge();

    const [row] = mockContainer.auditService.recordBestEffort.mock.calls[0] as [{ before: { summaryIds: string[]; shared: boolean } }];
    expect(row.before.shared).toBe(true);
    expect(row.before.summaryIds).toContain(summaryId.toString());
  });

  it('should read the video before the cascade deletes it', async () => {
    const order: string[] = [];
    mockContainer.videoRepository.findCacheKeysByYoutubeId.mockImplementation(async () => {
      order.push('read');
      return [];
    });
    mockContainer.videoCascadeService.deleteVideo.mockImplementation(async () => {
      order.push('delete');
      return cascadeResult;
    });

    await purge();

    expect(order).toEqual(['read', 'delete']);
  });

  it('should reject a field it does not know', async () => {
    const response = await purge({ reason: 'x', adminId: 'someone-else' });

    expect(response.statusCode).toBe(400);
    expect(mockContainer.videoCascadeService.deleteVideo).not.toHaveBeenCalled();
  });

  it('should reject a blank reason', async () => {
    expect((await purge({ reason: '   ' })).statusCode).toBe(400);
  });

  it('should trim the reason it records', async () => {
    await purge({ reason: '  duplicate upload  ' });

    expect(mockContainer.videoCascadeService.deleteVideo).toHaveBeenCalledWith(
      expect.objectContaining({ actor: expect.objectContaining({ reason: 'duplicate upload' }) }),
    );
  });

  it('should refuse a purge whose signature was made for another video', async () => {
    const other = adminActorRequest('DELETE', '/api/admin/videos/aaaaaaaaaaa');

    const response = await app.inject({ method: 'DELETE', url: `/api/admin/videos/${YT}`, headers: other.headers });

    expect(response.statusCode).toBe(401);
    expect(mockContainer.videoCascadeService.deleteVideo).not.toHaveBeenCalled();
  });

  it('should refuse a purge replaying a signature taken from a GET', async () => {
    const read = adminActorRequest('GET', `/api/admin/videos/${YT}`);

    const response = await app.inject({ method: 'DELETE', url: `/api/admin/videos/${YT}`, headers: read.headers });

    expect(response.statusCode).toBe(401);
    expect(mockContainer.videoCascadeService.deleteVideo).not.toHaveBeenCalled();
  });

  it('should not audit a request the admin key rejects', async () => {
    await app.inject({ method: 'DELETE', url: `/api/admin/videos/${YT}` });

    expect(mockContainer.auditService.recordBestEffort).not.toHaveBeenCalled();
  });

  it('should not audit a purge that failed before touching any row', async () => {
    mockContainer.videoCascadeService.deleteVideo.mockRejectedValue(new SummarizerPurgeError('down'));

    await purge();

    expect(mockContainer.auditService.recordBestEffort).not.toHaveBeenCalled();
  });

  it('should answer 502 when the summarizer purge fails', async () => {
    mockContainer.videoCascadeService.deleteVideo.mockRejectedValue(new SummarizerPurgeError('down'));

    const response = await purge();

    expect(response.statusCode).toBe(502);
    expect(response.json()).toMatchObject({ error: 'SUMMARIZER_PURGE_FAILED' });
  });
});
