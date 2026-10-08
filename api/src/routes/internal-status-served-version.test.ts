/**
 * 1d.8 wiring: the summarizer's terminal `video.status` relay settles which
 * version is served — a completed run is promoted, a failed run hands its
 * library entries back. The rules themselves are covered against a real Mongo
 * in services/__tests__/video-served-version.integration.test.ts.
 */
import { describe, it, expect, beforeAll, afterAll, beforeEach, vi } from 'vitest';
import { FastifyInstance } from 'fastify';
import { ObjectId } from 'mongodb';
import { buildTestApp, createMockContainer, type MockContainer } from '../test/helpers.js';

const INTERNAL_SECRET = 'dev-internal-secret-change-me';

describe('POST /internal/status — served version', () => {
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
    app.mongo.db.collection = vi.fn().mockReturnValue({
      updateMany: vi.fn().mockResolvedValue({ modifiedCount: 1 }),
    });
    app.broadcast = vi.fn();
  });

  async function relayStatus(videoSummaryId: string, status: string): Promise<number> {
    const response = await app.inject({
      method: 'POST',
      url: '/internal/status',
      headers: { 'content-type': 'application/json', 'x-internal-secret': INTERNAL_SECRET },
      payload: { type: 'video.status', payload: { videoSummaryId, userId: 'user-1', status } },
    });
    return response.statusCode;
  }

  it('should promote the row when its run completes', async () => {
    const videoSummaryId = new ObjectId().toHexString();

    await relayStatus(videoSummaryId, 'completed');

    expect(mockContainer.videoRepository.promoteCompletedVersion).toHaveBeenCalledWith(videoSummaryId);
  });

  it('should restore the served version when the run fails', async () => {
    const videoSummaryId = new ObjectId().toHexString();

    await relayStatus(videoSummaryId, 'failed');

    expect(mockContainer.videoRepository.restoreServedVersion).toHaveBeenCalledWith(videoSummaryId);
  });

  it('should not settle the served version on a progress status', async () => {
    await relayStatus(new ObjectId().toHexString(), 'processing');

    expect(mockContainer.videoRepository.promoteCompletedVersion).not.toHaveBeenCalled();
  });

  it('should still acknowledge the status when settling the served version throws', async () => {
    mockContainer.videoRepository.promoteCompletedVersion.mockRejectedValueOnce(new Error('mongo blip'));

    const statusCode = await relayStatus(new ObjectId().toHexString(), 'completed');

    expect(statusCode).toBe(200);
  });
});
