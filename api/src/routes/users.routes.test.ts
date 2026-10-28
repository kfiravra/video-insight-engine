import { describe, it, expect, beforeAll, afterAll, beforeEach, vi } from 'vitest';
import { FastifyInstance } from 'fastify';
import { ObjectId } from 'mongodb';
import {
  adminActorRequest,
  buildTestApp,
  createMockContainer,
  getAuthHeader,
  TEST_ADMIN_USER_ID,
  type MockContainer,
  testUser,
} from '../test/helpers.js';
import { config } from '../config.js';
import { UserNotFoundError } from '../utils/errors.js';

describe('user deletion routes', () => {
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
    // Re-install the default that survives clearAllMocks-resets-calls because
    // some tests below replace this with a soft-deleted user.
    mockContainer.userRepository.findById.mockResolvedValue({
      _id: { toString: () => testUser.userId },
      email: testUser.email,
      deletedAt: null,
    });
  });

  // ─── DELETE /api/users/me ────────────────────────────────────────────────

  describe('DELETE /api/users/me', () => {
    it('should schedule a soft delete and return 202', async () => {
      const scheduled = new Date('2026-06-19T12:00:00Z');
      mockContainer.userDeletionService.requestDeletion.mockResolvedValue({
        userId: testUser.userId,
        scheduledHardDeleteAt: scheduled,
        graceDays: 30,
      });

      const res = await app.inject({
        method: 'DELETE',
        url: '/api/users/me',
        headers: { authorization: authHeader, 'content-type': 'application/json' },
        payload: {},
      });

      expect(res.statusCode).toBe(202);
      const body = res.json();
      expect(body.userId).toBe(testUser.userId);
      expect(body.scheduledHardDeleteAt).toBe(scheduled.toISOString());
      expect(body.graceDays).toBe(30);
      expect(mockContainer.userDeletionService.requestDeletion).toHaveBeenCalledWith(testUser.userId);
    });

    it('should clear the refresh cookie so the browser session ends', async () => {
      mockContainer.userDeletionService.requestDeletion.mockResolvedValue({
        userId: testUser.userId,
        scheduledHardDeleteAt: new Date(),
        graceDays: 30,
      });

      const res = await app.inject({
        method: 'DELETE',
        url: '/api/users/me',
        headers: { authorization: authHeader, 'content-type': 'application/json' },
        payload: {},
      });

      const cookies = res.headers['set-cookie'];
      const cookieStr = Array.isArray(cookies) ? cookies.join(';') : (cookies ?? '');
      expect(cookieStr).toMatch(/refreshToken=;/);
    });

    it('should require auth (no header → 401)', async () => {
      const res = await app.inject({
        method: 'DELETE',
        url: '/api/users/me',
        payload: {},
      });
      expect(res.statusCode).toBe(401);
    });
  });

  // ─── POST /api/users/me/restore ─────────────────────────────────────────

  describe('POST /api/users/me/restore', () => {
    it('should restore a soft-deleted user without going through the soft-delete-blocking authenticate hook', async () => {
      mockContainer.userDeletionService.cancelDeletion.mockResolvedValue({
        _id: { toString: () => testUser.userId },
        email: testUser.email,
        name: 'Test User',
        deletedAt: null,
      });

      const res = await app.inject({
        method: 'POST',
        url: '/api/users/me/restore',
        headers: { authorization: authHeader, 'content-type': 'application/json' },
        payload: {},
      });

      expect(res.statusCode).toBe(200);
      expect(res.json()).toMatchObject({
        id: testUser.userId,
        email: testUser.email,
      });
      expect(mockContainer.userDeletionService.cancelDeletion).toHaveBeenCalledWith(testUser.userId);
    });

    it('should reject restore without a valid JWT', async () => {
      const res = await app.inject({
        method: 'POST',
        url: '/api/users/me/restore',
        payload: {},
      });
      expect(res.statusCode).toBe(401);
    });
  });

  // ─── DELETE /api/admin/users/:id ────────────────────────────────────────

  describe('DELETE /api/admin/users/:id', () => {
    const targetId = new ObjectId().toString();
    const softDeleteResult = {
      userId: targetId,
      scheduledHardDeleteAt: new Date('2026-06-19T12:00:00Z'),
      graceDays: 30,
    };

    function hardDeleteResult() {
      return {
        _id: new ObjectId(),
        originalUserId: targetId,
        emailHash: 'a'.repeat(64),
        initiatedBy: 'admin',
        adminId: TEST_ADMIN_USER_ID,
        reason: 'GDPR request 4567',
        counts: {
          userVideos: 3,
          folders: 2,
          userCosts: 1,
          userCostAdjustments: 0,
          idempotencyKeys: 1,
          assistantNotes: 5,
          qdrantPoints: 0,
          s3Objects: 0,
        },
        startedAt: new Date('2026-05-20T12:29:55Z'),
        completedAt: new Date('2026-05-20T12:30:00Z'),
        durationMs: 5000,
        warnings: [],
      };
    }

    beforeEach(() => {
      mockContainer.userRepository.hasPermission.mockResolvedValue(true);
      mockContainer.userDeletionService.requestDeletion.mockResolvedValue(softDeleteResult);
      mockContainer.userDeletionService.executeHardDelete.mockResolvedValue(hardDeleteResult());
    });

    async function deleteUser(url: string, request: { headers: Record<string, string>; payload?: string }) {
      return app.inject({ method: 'DELETE', url, headers: request.headers, payload: request.payload });
    }

    it('should reject without x-admin-key (401)', async () => {
      const res = await app.inject({
        method: 'DELETE',
        url: `/api/admin/users/${targetId}?immediate=true`,
        payload: {},
      });
      expect(res.statusCode).toBe(401);
    });

    it('should reject with wrong x-admin-key (401)', async () => {
      const res = await app.inject({
        method: 'DELETE',
        url: `/api/admin/users/${targetId}?immediate=true`,
        headers: { 'x-admin-key': 'wrong-key', 'content-type': 'application/json' },
        payload: {},
      });
      expect(res.statusCode).toBe(401);
    });

    it('should refuse the shared key alone: deleting a user needs a named admin (403)', async () => {
      const res = await app.inject({
        method: 'DELETE',
        url: `/api/admin/users/${targetId}`,
        headers: { 'x-admin-key': config.ADMIN_API_KEY },
      });

      expect(res.statusCode).toBe(403);
      expect(mockContainer.userDeletionService.requestDeletion).not.toHaveBeenCalled();
    });

    it('should refuse an admin who no longer holds users:delete (403)', async () => {
      mockContainer.userRepository.hasPermission.mockResolvedValue(false);

      const res = await deleteUser(`/api/admin/users/${targetId}?immediate=true`, adminActorRequest());

      expect(res.statusCode).toBe(403);
      expect(mockContainer.userDeletionService.executeHardDelete).not.toHaveBeenCalled();
    });

    it('should check the actor against the users:delete permission', async () => {
      await deleteUser(`/api/admin/users/${targetId}`, adminActorRequest());

      expect(mockContainer.userRepository.hasPermission).toHaveBeenCalledWith(TEST_ADMIN_USER_ID, 'users:delete');
    });

    it('should run immediate cascade when immediate=true', async () => {
      const res = await deleteUser(
        `/api/admin/users/${targetId}?immediate=true`,
        adminActorRequest({ reason: 'GDPR request 4567' }),
      );

      expect(res.statusCode).toBe(200);
      const body = res.json();
      expect(body.userId).toBe(targetId);
      expect(body.counts.userVideos).toBe(3);
      expect(body.warnings).toEqual([]);
      expect(mockContainer.userDeletionService.executeHardDelete).toHaveBeenCalledWith(targetId, {
        initiatedBy: 'admin',
        adminId: TEST_ADMIN_USER_ID,
        reason: 'GDPR request 4567',
      });
    });

    it('should audit an immediate deletion with the signed admin, target and reason', async () => {
      await deleteUser(
        `/api/admin/users/${targetId}?immediate=true`,
        adminActorRequest({ reason: 'GDPR request 4567' }),
      );

      expect(mockContainer.auditService.recordBestEffort).toHaveBeenCalledWith({
        actorId: TEST_ADMIN_USER_ID,
        requestId: expect.any(String),
        ip: expect.any(String),
        action: 'users.delete',
        targetType: 'user',
        targetId,
        after: { mode: 'immediate', userDeletionId: expect.any(String) },
        reason: 'GDPR request 4567',
      });
    });

    it('should audit under the canonical lowercase id when the request id is uppercase', async () => {
      await deleteUser(`/api/admin/users/${targetId.toUpperCase()}?immediate=true`, adminActorRequest());

      expect(mockContainer.auditService.recordBestEffort).toHaveBeenCalledWith(
        expect.objectContaining({ targetId }),
      );
    });

    it('should not audit a request that failed authentication', async () => {
      await app.inject({
        method: 'DELETE',
        url: `/api/admin/users/${targetId}?immediate=true`,
        headers: { 'x-admin-key': 'wrong-key' },
      });

      expect(mockContainer.auditService.recordBestEffort).not.toHaveBeenCalled();
    });

    it('should fall back to soft delete + 30d when immediate is not set', async () => {
      const res = await deleteUser(`/api/admin/users/${targetId}`, adminActorRequest({}));

      expect(res.statusCode).toBe(202);
      expect(mockContainer.userDeletionService.executeHardDelete).not.toHaveBeenCalled();
      expect(mockContainer.userDeletionService.requestDeletion).toHaveBeenCalledWith(targetId, {
        origin: { initiatedBy: 'admin', adminId: TEST_ADMIN_USER_ID, reason: null },
      });
    });

    it('should hand the signed admin and reason to the soft delete as its origin', async () => {
      await deleteUser(`/api/admin/users/${targetId}`, adminActorRequest({ reason: 'duplicate account' }));

      expect(mockContainer.userDeletionService.requestDeletion).toHaveBeenCalledWith(targetId, {
        origin: { initiatedBy: 'admin', adminId: TEST_ADMIN_USER_ID, reason: 'duplicate account' },
      });
    });

    it('should audit the soft delete at request time with the signed admin', async () => {
      await deleteUser(`/api/admin/users/${targetId}`, adminActorRequest({ reason: 'duplicate account' }));

      expect(mockContainer.auditService.recordBestEffort).toHaveBeenCalledWith({
        actorId: TEST_ADMIN_USER_ID,
        requestId: expect.any(String),
        ip: expect.any(String),
        action: 'users.delete',
        targetType: 'user',
        targetId,
        after: { mode: 'scheduled', scheduledHardDeleteAt: '2026-06-19T12:00:00.000Z' },
        reason: 'duplicate account',
      });
    });

    it('should not audit a soft delete the service refuses', async () => {
      mockContainer.userDeletionService.requestDeletion.mockRejectedValue(new UserNotFoundError());

      const res = await deleteUser(`/api/admin/users/${targetId}`, adminActorRequest());

      expect(res.statusCode).toBe(404);
      expect(mockContainer.auditService.recordBestEffort).not.toHaveBeenCalled();
    });
  });
});
