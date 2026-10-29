import { describe, it, expect, beforeAll, afterAll, beforeEach, afterEach, vi } from 'vitest';
import type { FastifyInstance, LightMyRequestResponse } from 'fastify';
import { config } from '../config.js';
import { buildTestApp, createMockContainer, getAuthHeader, type MockContainer } from '../test/helpers.js';

// Every demo visitor shares ONE account, so changes to its folders, its
// profile, and anything that forces a paid re-run are refused (403
// DEMO_RESTRICTED). Deleting and moving its own videos is allowed like any
// user: a delete is user scope, so the shared summary stays. The guard keys on
// the user id, so a normal user must be unaffected on every route.

const DEMO_EMAIL = 'demo@example.com';
const DEMO_ID = 'dddddddddddddddddddddddd';
const OTHER_ID = 'test-user-id';
const OBJECT_ID = '507f1f77bcf86cd799439011';
const PLAIN_SUBMIT = { url: 'https://www.youtube.com/watch?v=dQw4w9WgXcQ' };
const PROVIDERS = { default: 'openai' } as const;

function expectRestricted(response: LightMyRequestResponse): void {
  expect(response.statusCode).toBe(403);
  expect(response.json()).toMatchObject({ error: 'DEMO_RESTRICTED' });
}

describe('demo account restrictions', () => {
  let app: FastifyInstance;
  let mockContainer: MockContainer;
  let demoAuth: string;
  let userAuth: string;
  const originalEmail = config.DEMO_USER_EMAIL;

  function internalHeaders(userId: string): Record<string, string> {
    return {
      'content-type': 'application/json',
      'x-internal-secret': config.INTERNAL_SECRET,
      'x-user-id': userId,
    };
  }

  function asDemo(extra: Record<string, string> = {}): Record<string, string> {
    return { authorization: demoAuth, 'content-type': 'application/json', ...extra };
  }

  function asUser(extra: Record<string, string> = {}): Record<string, string> {
    return { authorization: userAuth, 'content-type': 'application/json', ...extra };
  }

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
    mockContainer.userRepository.findByEmail.mockResolvedValue({
      _id: { toString: () => DEMO_ID },
      email: DEMO_EMAIL,
    });
    mockContainer.videoService.createVideo.mockResolvedValue({
      video: { id: '1', status: 'pending' },
      cached: false,
    });
  });

  afterEach(() => {
    config.DEMO_USER_EMAIL = originalEmail;
  });

  describe('library mutations', () => {
    it('should let the demo account delete its own video in user scope', async () => {
      const response = await app.inject({ method: 'DELETE', url: `/api/videos/${OBJECT_ID}`, headers: asDemo() });

      expect(response.statusCode).toBe(204);
      expect(mockContainer.videoCascadeService.deleteVideo).toHaveBeenCalledWith({
        scope: 'user',
        userId: DEMO_ID,
        userVideoId: OBJECT_ID,
      });
    });

    it('should let the demo account move its own video', async () => {
      mockContainer.videoService.moveToFolder.mockResolvedValue({ id: OBJECT_ID, folderId: null });

      const response = await app.inject({
        method: 'PATCH',
        url: `/api/videos/${OBJECT_ID}/move`,
        headers: asDemo(),
        payload: { folderId: null },
      });

      expect(response.statusCode).toBe(200);
      expect(mockContainer.videoService.moveToFolder).toHaveBeenCalledWith(DEMO_ID, OBJECT_ID, null);
    });

    it('should refuse PATCH /api/folders/:id for the demo account', async () => {
      const response = await app.inject({
        method: 'PATCH',
        url: `/api/folders/${OBJECT_ID}`,
        headers: asDemo(),
        payload: { name: 'Renamed' },
      });

      expectRestricted(response);
      expect(mockContainer.folderService.update).not.toHaveBeenCalled();
    });

    it('should refuse DELETE /api/folders/:id for the demo account', async () => {
      const response = await app.inject({ method: 'DELETE', url: `/api/folders/${OBJECT_ID}`, headers: asDemo() });

      expectRestricted(response);
      expect(mockContainer.folderService.delete).not.toHaveBeenCalled();
    });

    it('should refuse PATCH /api/videos/:id/override-category for the demo account', async () => {
      const response = await app.inject({
        method: 'PATCH',
        url: `/api/videos/${OBJECT_ID}/override-category`,
        headers: asDemo(),
        payload: { category: 'cooking' },
      });

      expectRestricted(response);
      expect(mockContainer.videoService.overrideCategory).not.toHaveBeenCalled();
    });

    it('should still let the demo account read its library and create folders', async () => {
      mockContainer.folderService.create.mockResolvedValue({ id: 'f1', name: 'Mine' });

      const list = await app.inject({ method: 'GET', url: '/api/videos', headers: asDemo() });
      const create = await app.inject({
        method: 'POST',
        url: '/api/folders',
        headers: asDemo(),
        payload: { name: 'Mine' },
      });

      expect([list.statusCode, create.statusCode]).toEqual([200, 201]);
    });
  });

  describe('POST /api/videos', () => {
    it('should refuse bypassCache for the demo account before holding any minutes', async () => {
      const response = await app.inject({
        method: 'POST',
        url: '/api/videos',
        headers: asDemo(),
        payload: { ...PLAIN_SUBMIT, bypassCache: true },
      });

      expectRestricted(response);
      expect(mockContainer.billingEnforcement.admit).not.toHaveBeenCalled();
      expect(mockContainer.videoService.createVideo).not.toHaveBeenCalled();
    });

    it('should refuse a providers override for the demo account', async () => {
      const response = await app.inject({
        method: 'POST',
        url: '/api/videos',
        headers: asDemo(),
        payload: { ...PLAIN_SUBMIT, providers: PROVIDERS },
      });

      expectRestricted(response);
      expect(mockContainer.videoService.createVideo).not.toHaveBeenCalled();
    });

    it('should refuse a cold run for the demo account', async () => {
      const response = await app.inject({
        method: 'POST',
        url: '/api/videos',
        headers: asDemo(),
        payload: { ...PLAIN_SUBMIT, cold: true },
      });

      expectRestricted(response);
      expect(mockContainer.videoService.createVideo).not.toHaveBeenCalled();
    });

    it('should accept a plain submit from the demo account', async () => {
      const response = await app.inject({
        method: 'POST',
        url: '/api/videos',
        headers: asDemo(),
        payload: PLAIN_SUBMIT,
      });

      expect(response.statusCode).toBe(201);
      expect(mockContainer.videoService.createVideo).toHaveBeenCalledTimes(1);
    });

    it('should accept an explicit bypassCache: false from the demo account', async () => {
      const response = await app.inject({
        method: 'POST',
        url: '/api/videos',
        headers: asDemo(),
        payload: { ...PLAIN_SUBMIT, bypassCache: false },
      });

      expect(response.statusCode).toBe(201);
    });

    it('should still accept bypassCache from a normal user', async () => {
      const response = await app.inject({
        method: 'POST',
        url: '/api/videos',
        headers: asUser(),
        payload: { ...PLAIN_SUBMIT, bypassCache: true },
      });

      expect(response.statusCode).toBe(201);
      expect(mockContainer.videoService.createVideo).toHaveBeenCalledTimes(1);
    });
  });

  describe('POST /api/playlists/import', () => {
    const playlist = { url: 'https://www.youtube.com/playlist?list=PLabcdef123' };

    it('should refuse a providers override for the demo account', async () => {
      const response = await app.inject({
        method: 'POST',
        url: '/api/playlists/import',
        headers: asDemo(),
        payload: { ...playlist, providers: PROVIDERS },
      });

      expectRestricted(response);
      expect(mockContainer.playlistService.import).not.toHaveBeenCalled();
    });

    it('should accept a plain import from the demo account', async () => {
      mockContainer.playlistService.import.mockResolvedValue({ imported: 1 });

      const response = await app.inject({
        method: 'POST',
        url: '/api/playlists/import',
        headers: asDemo(),
        payload: playlist,
      });

      expect(response.statusCode).toBe(201);
    });
  });

  describe('POST /api/assistant/action', () => {
    beforeEach(() => {
      mockContainer.assistantClient.action.mockResolvedValue({ status: 200, body: { success: true } });
    });

    it.each(['rename_folder', 'move_folder', 'delete_folder'])(
      'should refuse %s for the demo account without calling the assistant',
      async (action) => {
        const response = await app.inject({
          method: 'POST',
          url: '/api/assistant/action',
          headers: asDemo(),
          payload: { action },
        });

        expectRestricted(response);
        expect(mockContainer.assistantClient.action).not.toHaveBeenCalled();
      },
    );

    it.each(['move_video', 'organize_library', 'create_folder', 'generate_video', 'explain'])(
      'should let the demo account run the %s action',
      async (action) => {
        const response = await app.inject({
          method: 'POST',
          url: '/api/assistant/action',
          headers: asDemo(),
          payload: { action },
        });

        expect(response.statusCode).toBe(200);
        expect(mockContainer.assistantClient.action).toHaveBeenCalledTimes(1);
      },
    );

    it('should let a normal user run delete_folder', async () => {
      const response = await app.inject({
        method: 'POST',
        url: '/api/assistant/action',
        headers: asUser(),
        payload: { action: 'delete_folder' },
      });

      expect(response.statusCode).toBe(200);
      expect(mockContainer.assistantClient.action).toHaveBeenCalledTimes(1);
    });
  });

  describe('internal assistant routes', () => {
    it('should refuse PATCH /internal/assistant/folders/:id for the demo user id', async () => {
      const response = await app.inject({
        method: 'PATCH',
        url: `/internal/assistant/folders/${OBJECT_ID}`,
        headers: internalHeaders(DEMO_ID),
        payload: { name: 'Renamed' },
      });

      expectRestricted(response);
      expect(mockContainer.folderService.update).not.toHaveBeenCalled();
    });

    it('should refuse DELETE /internal/assistant/folders/:id for the demo user id', async () => {
      const response = await app.inject({
        method: 'DELETE',
        url: `/internal/assistant/folders/${OBJECT_ID}`,
        headers: internalHeaders(DEMO_ID),
      });

      expectRestricted(response);
      expect(mockContainer.folderService.delete).not.toHaveBeenCalled();
    });

    it('should let the demo user id move a video through PATCH /internal/assistant/videos/:id/move', async () => {
      mockContainer.videoService.moveToFolder.mockResolvedValue({ id: OBJECT_ID, folderId: null });

      const response = await app.inject({
        method: 'PATCH',
        url: `/internal/assistant/videos/${OBJECT_ID}/move`,
        headers: internalHeaders(DEMO_ID),
        payload: { folderId: null },
      });

      expect(response.statusCode).toBe(200);
      expect(mockContainer.videoService.moveToFolder).toHaveBeenCalledWith(DEMO_ID, OBJECT_ID, null);
    });

    it('should let a normal user id mutate through the internal routes', async () => {
      mockContainer.folderService.delete.mockResolvedValue(undefined);

      const response = await app.inject({
        method: 'DELETE',
        url: `/internal/assistant/folders/${OBJECT_ID}`,
        headers: internalHeaders(OTHER_ID),
      });

      expect(response.statusCode).toBe(200);
      expect(mockContainer.folderService.delete).toHaveBeenCalledTimes(1);
    });

    it('should let the demo user id create a folder and generate a video', async () => {
      mockContainer.folderService.create.mockResolvedValue({ id: 'f1', name: 'Mine' });

      const create = await app.inject({
        method: 'POST',
        url: '/internal/assistant/folders',
        headers: internalHeaders(DEMO_ID),
        payload: { name: 'Mine' },
      });
      const generate = await app.inject({
        method: 'POST',
        url: '/internal/assistant/generate',
        headers: internalHeaders(DEMO_ID),
        payload: { url: PLAIN_SUBMIT.url },
      });

      expect([create.statusCode, generate.statusCode]).toEqual([201, 200]);
    });
  });

  describe('PATCH /api/users/me/preferences', () => {
    it('should refuse changing displayName for the demo account', async () => {
      const response = await app.inject({
        method: 'PATCH',
        url: '/api/users/me/preferences',
        headers: asDemo(),
        payload: { displayName: 'Squatter' },
      });

      expectRestricted(response);
      expect(mockContainer.userRepository.update).not.toHaveBeenCalled();
    });

    it('should refuse changing username for the demo account', async () => {
      const response = await app.inject({
        method: 'PATCH',
        url: '/api/users/me/preferences',
        headers: asDemo(),
        payload: { username: 'squatter' },
      });

      expectRestricted(response);
      expect(mockContainer.userRepository.update).not.toHaveBeenCalled();
    });

    it('should let the demo account change its theme', async () => {
      const response = await app.inject({
        method: 'PATCH',
        url: '/api/users/me/preferences',
        headers: asDemo(),
        payload: { theme: 'dark' },
      });

      expect(response.statusCode).toBe(200);
      expect(mockContainer.userRepository.update).toHaveBeenCalledWith(DEMO_ID, { 'preferences.theme': 'dark' });
    });

    it('should let a normal user change displayName', async () => {
      const response = await app.inject({
        method: 'PATCH',
        url: '/api/users/me/preferences',
        headers: asUser(),
        payload: { displayName: 'Real Name' },
      });

      expect(response.statusCode).toBe(200);
    });
  });

  describe('normal users and demo mode off', () => {
    it('should let a normal user delete a video while demo mode is on', async () => {
      const response = await app.inject({ method: 'DELETE', url: `/api/videos/${OBJECT_ID}`, headers: asUser() });

      expect(response.statusCode).toBe(204);
      expect(mockContainer.videoCascadeService.deleteVideo).toHaveBeenCalledTimes(1);
    });

    it('should let a normal user rename a folder while demo mode is on', async () => {
      mockContainer.folderService.update.mockResolvedValue({ id: OBJECT_ID, name: 'Renamed' });

      const response = await app.inject({
        method: 'PATCH',
        url: `/api/folders/${OBJECT_ID}`,
        headers: asUser(),
        payload: { name: 'Renamed' },
      });

      expect(response.statusCode).toBe(200);
    });

    it('should not restrict the former demo id when DEMO_USER_EMAIL is blank', async () => {
      config.DEMO_USER_EMAIL = '';
      mockContainer.userRepository.findByEmail.mockClear();
      mockContainer.folderService.delete.mockResolvedValue(undefined);

      const response = await app.inject({ method: 'DELETE', url: `/api/folders/${OBJECT_ID}`, headers: asDemo() });

      expect(response.statusCode).toBe(204);
      expect(mockContainer.userRepository.findByEmail).not.toHaveBeenCalled();
    });
  });
});
