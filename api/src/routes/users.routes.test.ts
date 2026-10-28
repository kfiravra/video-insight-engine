import { describe, it, expect, beforeAll, afterAll, beforeEach, vi } from 'vitest';
import { FastifyInstance } from 'fastify';
import { ObjectId } from 'mongodb';
import { buildTestApp, createMockContainer, getAuthHeader, type MockContainer, testUser } from '../test/helpers.js';
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

    it('should run immediate cascade when immediate=true', async () => {
      const completedAt = new Date('2026-05-20T12:30:00Z');
      mockContainer.userDeletionService.executeHardDelete.mockResolvedValue({
        _id: new ObjectId(),
        originalUserId: targetId,
        emailHash: 'a'.repeat(64),
        initiatedBy: 'admin',
        adminId: 'op-1',
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
        completedAt,
        durationMs: 5000,
        warnings: [],
      });

      const res = await app.inject({
        method: 'DELETE',
        url: `/api/admin/users/${targetId}?immediate=true`,
        headers: {
          'x-admin-key': config.ADMIN_API_KEY,
          'x-admin-id': 'op-1',
          'content-type': 'application/json',
        },
        payload: { reason: 'GDPR request 4567' },
      });

      expect(res.statusCode).toBe(200);
      const body = res.json();
      expect(body.userId).toBe(targetId);
      expect(body.counts.userVideos).toBe(3);
      expect(body.warnings).toEqual([]);
      expect(mockContainer.userDeletionService.executeHardDelete).toHaveBeenCalledWith(
        targetId,
        { initiatedBy: 'admin', adminId: 'op-1', reason: 'GDPR request 4567' },
      );
    });

    it('should audit an immediate deletion with the operator, target and reason', async () => {
      mockContainer.userDeletionService.executeHardDelete.mockResolvedValue({
        _id: new ObjectId(),
        originalUserId: targetId,
        counts: {},
        completedAt: new Date('2026-05-20T12:30:00Z'),
        durationMs: 5000,
        warnings: [],
      });

      await app.inject({
        method: 'DELETE',
        url: `/api/admin/users/${targetId}?immediate=true`,
        headers: { 'x-admin-key': config.ADMIN_API_KEY, 'x-admin-id': 'op-1', 'content-type': 'application/json' },
        payload: { reason: 'GDPR request 4567' },
      });

      expect(mockContainer.auditService.recordBestEffort).toHaveBeenCalledWith({
        actorId: 'op-1',
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
      mockContainer.userDeletionService.executeHardDelete.mockResolvedValue({
        _id: new ObjectId(),
        originalUserId: targetId,
        counts: {},
        completedAt: new Date(),
        durationMs: 1,
        warnings: [],
      });

      await app.inject({
        method: 'DELETE',
        url: `/api/admin/users/${targetId.toUpperCase()}?immediate=true`,
        headers: { 'x-admin-key': config.ADMIN_API_KEY },
      });

      expect(mockContainer.auditService.recordBestEffort).toHaveBeenCalledWith(
        expect.objectContaining({ targetId }),
      );
    });

    it('should not audit a request the admin key rejects', async () => {
      await app.inject({
        method: 'DELETE',
        url: `/api/admin/users/${targetId}?immediate=true`,
        headers: { 'x-admin-key': 'wrong-key' },
      });

      expect(mockContainer.auditService.recordBestEffort).not.toHaveBeenCalled();
    });

    it('should fall back to soft delete + 30d when immediate is not set', async () => {
      mockContainer.userDeletionService.requestDeletion.mockResolvedValue({
        userId: targetId,
        scheduledHardDeleteAt: new Date('2026-06-19T12:00:00Z'),
        graceDays: 30,
      });

      const res = await app.inject({
        method: 'DELETE',
        url: `/api/admin/users/${targetId}`,
        headers: {
          'x-admin-key': config.ADMIN_API_KEY,
          'content-type': 'application/json',
        },
        payload: {},
      });

      expect(res.statusCode).toBe(202);
      expect(mockContainer.userDeletionService.executeHardDelete).not.toHaveBeenCalled();
      expect(mockContainer.userDeletionService.requestDeletion).toHaveBeenCalledWith(targetId, {
        origin: { initiatedBy: 'admin', adminId: null, reason: null },
      });
    });

    it('should hand the operator and reason to the soft delete as its origin', async () => {
      mockContainer.userDeletionService.requestDeletion.mockResolvedValue({
        userId: targetId,
        scheduledHardDeleteAt: new Date('2026-06-19T12:00:00Z'),
        graceDays: 30,
      });

      await app.inject({
        method: 'DELETE',
        url: `/api/admin/users/${targetId}`,
        headers: { 'x-admin-key': config.ADMIN_API_KEY, 'x-admin-id': 'op-2', 'content-type': 'application/json' },
        payload: { reason: 'duplicate account' },
      });

      expect(mockContainer.userDeletionService.requestDeletion).toHaveBeenCalledWith(targetId, {
        origin: { initiatedBy: 'admin', adminId: 'op-2', reason: 'duplicate account' },
      });
    });

    it('should audit the soft delete at request time with the operator', async () => {
      mockContainer.userDeletionService.requestDeletion.mockResolvedValue({
        userId: targetId,
        scheduledHardDeleteAt: new Date('2026-06-19T12:00:00Z'),
        graceDays: 30,
      });

      await app.inject({
        method: 'DELETE',
        url: `/api/admin/users/${targetId}`,
        headers: { 'x-admin-key': config.ADMIN_API_KEY, 'x-admin-id': 'op-2', 'content-type': 'application/json' },
        payload: { reason: 'duplicate account' },
      });

      expect(mockContainer.auditService.recordBestEffort).toHaveBeenCalledWith({
        actorId: 'op-2',
        requestId: expect.any(String),
        ip: expect.any(String),
        action: 'users.delete',
        targetType: 'user',
        targetId,
        after: { mode: 'scheduled', scheduledHardDeleteAt: '2026-06-19T12:00:00.000Z' },
        reason: 'duplicate account',
      });
    });

    it('should attribute a soft delete sent with the bare admin key to system:admin-key', async () => {
      mockContainer.userDeletionService.requestDeletion.mockResolvedValue({
        userId: targetId,
        scheduledHardDeleteAt: new Date('2026-06-19T12:00:00Z'),
        graceDays: 30,
      });

      await app.inject({
        method: 'DELETE',
        url: `/api/admin/users/${targetId}`,
        headers: { 'x-admin-key': config.ADMIN_API_KEY },
      });

      expect(mockContainer.auditService.recordBestEffort).toHaveBeenCalledWith(
        expect.objectContaining({ actorId: 'system:admin-key' }),
      );
    });

    it('should not audit a soft delete the service refuses', async () => {
      mockContainer.userDeletionService.requestDeletion.mockRejectedValue(new UserNotFoundError());

      const res = await app.inject({
        method: 'DELETE',
        url: `/api/admin/users/${targetId}`,
        headers: { 'x-admin-key': config.ADMIN_API_KEY },
      });

      expect(res.statusCode).toBe(404);
      expect(mockContainer.auditService.recordBestEffort).not.toHaveBeenCalled();
    });
  });
});
