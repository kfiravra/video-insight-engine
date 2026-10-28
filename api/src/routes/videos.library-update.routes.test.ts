/**
 * D-LIB on the wire: `updateToLatest` on POST /api/videos (a paid re-run, so
 * never from the shared demo account), and `updateAvailable` on the answers
 * for a copy an older pipeline wrote: the POST existing-copy and replay
 * answers and GET /api/videos/:id. The demo account is never offered the
 * update, so it never sees the flag.
 */
import { describe, it, expect, beforeAll, afterAll, beforeEach, afterEach, vi } from 'vitest';
import type { FastifyInstance } from 'fastify';
import { config } from '../config.js';
import { buildTestApp, createMockContainer, getAuthHeader, type MockContainer } from '../test/helpers.js';

const DEMO_EMAIL = 'demo@example.com';
const DEMO_ID = 'dddddddddddddddddddddddd';
const VIDEO_ID = '507f1f77bcf86cd799439011';
const SUMMARY_ID = '507f191e810c19729de860ea';
const URL = 'https://www.youtube.com/watch?v=dQw4w9WgXcQ';
const EXISTING_COPY = {
  video: { id: VIDEO_ID, videoSummaryId: SUMMARY_ID, youtubeId: 'dQw4w9WgXcQ', status: 'completed' },
  cached: true,
  alreadyExists: true,
  updateAvailable: true,
};

describe('update to latest version (D-LIB) — HTTP', () => {
  let app: FastifyInstance;
  let mockContainer: MockContainer;
  let userAuth: string;
  let demoAuth: string;
  const originalEmail = config.DEMO_USER_EMAIL;

  const submit = (authorization: string, payload: Record<string, unknown> = {}) => app.inject({
    method: 'POST', url: '/api/videos', headers: { authorization, 'content-type': 'application/json' }, payload: { url: URL, ...payload },
  });
  const openVideo = (authorization: string) => app.inject({ method: 'GET', url: `/api/videos/${VIDEO_ID}`, headers: { authorization } });
  /** An earlier submit's completed idempotency key, on a copy an older pipeline wrote. */
  const replayOfOutdatedCopy = () => {
    mockContainer.idempotencyService.reserveHash.mockResolvedValueOnce({
      created: false,
      doc: { hash: 'test-hash', status: 'completed', youtubeId: 'dQw4w9WgXcQ', userVideoId: VIDEO_ID, videoSummaryId: SUMMARY_ID },
    });
    mockContainer.videoRepository.findUserVideo.mockResolvedValueOnce({ _id: VIDEO_ID, videoSummaryId: SUMMARY_ID, youtubeId: 'dQw4w9WgXcQ' });
    mockContainer.videoRepository.findCacheById.mockResolvedValueOnce({ status: 'completed', pipelineVersion: 'v0-old' });
  };

  beforeAll(async () => {
    mockContainer = createMockContainer();
    app = await buildTestApp(mockContainer);
    await app.ready();
    userAuth = await getAuthHeader(app);
    demoAuth = `Bearer ${app.jwt.sign({ userId: DEMO_ID, email: DEMO_EMAIL, type: 'access' })}`;
  });

  afterAll(async () => {
    await app.close();
  });

  beforeEach(() => {
    vi.clearAllMocks();
    config.DEMO_USER_EMAIL = DEMO_EMAIL;
    mockContainer.userRepository.findByEmail.mockResolvedValue({ _id: { toString: () => DEMO_ID }, email: DEMO_EMAIL });
    mockContainer.idempotencyService.reserveHash.mockResolvedValue({ created: true, doc: { status: 'pending' } });
    mockContainer.videoService.createVideo.mockResolvedValue(EXISTING_COPY);
    mockContainer.videoService.getVideo.mockResolvedValue({ id: VIDEO_ID, status: 'completed', updateAvailable: true });
  });

  afterEach(() => {
    config.DEMO_USER_EMAIL = originalEmail;
  });

  describe('POST /api/videos with updateToLatest', () => {
    it('should pass updateToLatest to createVideo and skip the idempotency gate', async () => {
      await submit(userAuth, { updateToLatest: true });

      expect(mockContainer.videoService.createVideo.mock.calls[0][2]).toMatchObject({ updateToLatest: true });
      expect(mockContainer.idempotencyService.reserveHash).not.toHaveBeenCalled();
    });

    it('should default updateToLatest to false', async () => {
      await submit(userAuth);

      expect(mockContainer.videoService.createVideo.mock.calls[0][2]).toMatchObject({ updateToLatest: false });
    });

    it('should return 400 when updateToLatest is not a boolean', async () => {
      expect((await submit(userAuth, { updateToLatest: 'yes' })).statusCode).toBe(400);
    });

    it('should refuse the demo account before holding any minutes', async () => {
      const response = await submit(demoAuth, { updateToLatest: true });

      expect([response.statusCode, response.json().error]).toEqual([403, 'DEMO_RESTRICTED']);
      expect(mockContainer.billingEnforcement.admit).not.toHaveBeenCalled();
    });
  });

  describe('updateAvailable', () => {
    it('should carry updateAvailable on the existing-copy answer', async () => {
      expect((await submit(userAuth)).json()).toMatchObject({ cached: true, alreadyExists: true, updateAvailable: true });
    });

    it('should carry updateAvailable on an idempotency replay of an outdated copy', async () => {
      replayOfOutdatedCopy();

      expect((await submit(userAuth)).json()).toMatchObject({ duplicate: true, updateAvailable: true });
    });

    it('should carry updateAvailable on GET /api/videos/:id', async () => {
      expect((await openVideo(userAuth)).json().updateAvailable).toBe(true);
    });

    it('should omit it from the demo account\'s existing-copy answer', async () => {
      const body = (await submit(demoAuth)).json();

      expect([body.alreadyExists, 'updateAvailable' in body]).toEqual([true, false]);
    });

    it('should omit it from the demo account\'s idempotency replay', async () => {
      replayOfOutdatedCopy();

      const body = (await submit(demoAuth)).json();

      expect([body.duplicate, 'updateAvailable' in body]).toEqual([true, false]);
    });

    it('should omit it from the demo account\'s GET /api/videos/:id', async () => {
      const response = await openVideo(demoAuth);

      expect([response.statusCode, 'updateAvailable' in response.json()]).toEqual([200, false]);
    });
  });
});
