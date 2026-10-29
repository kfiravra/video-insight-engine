import { describe, it, expect, beforeAll, afterAll, beforeEach, vi } from 'vitest';
import { FastifyInstance } from 'fastify';
import { ObjectId } from 'mongodb';
import {
  adminActorRequest,
  buildTestApp,
  createMockContainer,
  TEST_ADMIN_USER_ID,
  type MockContainer,
} from '../test/helpers.js';

function userDoc(id: string, fields: Record<string, unknown> = {}) {
  return { _id: { toString: () => id }, email: `${id}@example.com`, deletedAt: null, ...fields };
}

function adminSummary(id: string, deletedAt: Date | null = null) {
  return { id, email: `${id}@example.com`, roles: ['admin'], roleSource: 'env', deletedAt };
}

/** `DELETE /api/admin/users/:id`: who may be deleted, and what the request may carry. */
describe('admin user deletion route', () => {
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
    mockContainer.userRepository.findById.mockResolvedValue(userDoc(new ObjectId().toString()));
  });

  // ─── Deleting an admin ───────────────────────────────────────────────────

  describe('DELETE /api/admin/users/:id for an admin account', () => {
    const adminId = new ObjectId().toString();
    const otherAdminId = new ObjectId().toString();
    const request = (mode: 'immediate' | 'scheduled', body?: Record<string, unknown>) => {
      const url = `/api/admin/users/${adminId}${mode === 'immediate' ? '?immediate=true' : ''}`;
      const signed = adminActorRequest('DELETE', url, body);
      return app.inject({ method: 'DELETE', url, headers: signed.headers, payload: signed.payload });
    };

    beforeEach(() => {
      mockContainer.userRepository.hasPermission.mockResolvedValue(true);
      mockContainer.adminReplayGuard.claim.mockResolvedValue(true);
      mockContainer.userDeletionService.requestDeletion.mockResolvedValue({
        userId: adminId,
        scheduledHardDeleteAt: new Date('2026-06-19T12:00:00Z'),
        graceDays: 30,
      });
      mockContainer.userDeletionService.executeHardDelete.mockResolvedValue({
        _id: new ObjectId(),
        originalUserId: adminId,
        counts: {},
        completedAt: new Date(),
        durationMs: 1,
        warnings: [],
      });
      mockContainer.userRepository.findById.mockResolvedValue(userDoc(adminId, { roles: ['admin'], roleSource: 'env' }));
    });

    it.each(['immediate', 'scheduled'] as const)('should refuse to delete the last active admin (%s)', async (mode) => {
      mockContainer.userRepository.listAdmins.mockResolvedValue([adminSummary(adminId)]);

      const res = await request(mode);

      expect([res.statusCode, res.json().error]).toEqual([409, 'LAST_ADMIN']);
      expect(mockContainer.userDeletionService.executeHardDelete).not.toHaveBeenCalled();
      expect(mockContainer.userDeletionService.requestDeletion).not.toHaveBeenCalled();
    });

    it('should not audit a deletion it refused', async () => {
      mockContainer.userRepository.listAdmins.mockResolvedValue([adminSummary(adminId)]);

      await request('immediate');

      expect(mockContainer.auditService.recordBestEffort).not.toHaveBeenCalled();
    });

    it.each(['immediate', 'scheduled'] as const)('should delete an admin while another active admin remains (%s)', async (mode) => {
      mockContainer.userRepository.listAdmins.mockResolvedValue([adminSummary(adminId), adminSummary(otherAdminId)]);

      const res = await request(mode);

      expect(res.statusCode).toBe(mode === 'immediate' ? 200 : 202);
    });

    it('should let an admin delete themselves when another active admin remains', async () => {
      // The signing admin is the target: allowed because the other admin remains.
      mockContainer.userRepository.findById.mockResolvedValue(userDoc(TEST_ADMIN_USER_ID, { roles: ['admin'] }));
      mockContainer.userRepository.listAdmins.mockResolvedValue([adminSummary(TEST_ADMIN_USER_ID), adminSummary(otherAdminId)]);
      const url = `/api/admin/users/${TEST_ADMIN_USER_ID}`;
      const signed = adminActorRequest('DELETE', url);

      const res = await app.inject({ method: 'DELETE', url, headers: signed.headers });

      expect(res.statusCode).toBe(202);
    });

    it('should refuse an admin deleting themselves when they are the last one', async () => {
      mockContainer.userRepository.findById.mockResolvedValue(userDoc(TEST_ADMIN_USER_ID, { roles: ['admin'] }));
      mockContainer.userRepository.listAdmins.mockResolvedValue([adminSummary(TEST_ADMIN_USER_ID)]);
      const url = `/api/admin/users/${TEST_ADMIN_USER_ID}`;
      const signed = adminActorRequest('DELETE', url);

      const res = await app.inject({ method: 'DELETE', url, headers: signed.headers });

      expect(res.statusCode).toBe(409);
    });

    it('should not count a soft-deleted admin as the one that remains', async () => {
      mockContainer.userRepository.listAdmins.mockResolvedValue([adminSummary(adminId), adminSummary(otherAdminId, new Date())]);

      const res = await request('scheduled');

      expect(res.statusCode).toBe(409);
    });

    it('should not block deleting an admin account that is already soft-deleted', async () => {
      mockContainer.userRepository.findById.mockResolvedValue(userDoc(adminId, { roles: ['admin'], deletedAt: new Date() }));
      mockContainer.userRepository.listAdmins.mockResolvedValue([adminSummary(adminId, new Date())]);

      const res = await request('immediate');

      expect(res.statusCode).toBe(200);
    });

    it('should recognise a legacy role:admin account as an admin', async () => {
      mockContainer.userRepository.findById.mockResolvedValue(userDoc(adminId, { role: 'admin' }));
      mockContainer.userRepository.listAdmins.mockResolvedValue([adminSummary(adminId)]);

      const res = await request('immediate');

      expect(res.statusCode).toBe(409);
    });

    it('should not look for other admins when the target is not an admin', async () => {
      mockContainer.userRepository.findById.mockResolvedValue(userDoc(adminId));

      await request('immediate');

      expect(mockContainer.userRepository.listAdmins).not.toHaveBeenCalled();
    });

    it('should record the admin account\'s roles in the audit row\'s before, without an email', async () => {
      mockContainer.userRepository.listAdmins.mockResolvedValue([adminSummary(adminId), adminSummary(otherAdminId)]);

      await request('immediate');

      const [row] = mockContainer.auditService.recordBestEffort.mock.calls[0] as [{ before: Record<string, unknown> }];
      expect(row.before).toMatchObject({ roles: ['admin'], roleSource: 'env' });
      expect(JSON.stringify(row.before)).not.toContain('@');
    });
  });

  // ─── Request body ───────────────────────────────────────────────────────

  describe('DELETE /api/admin/users/:id body', () => {
    const targetId = new ObjectId().toString();
    const del = (body: Record<string, unknown>) => {
      const url = `/api/admin/users/${targetId}`;
      const signed = adminActorRequest('DELETE', url, body);
      return app.inject({ method: 'DELETE', url, headers: signed.headers, payload: signed.payload });
    };

    beforeEach(() => {
      mockContainer.userRepository.hasPermission.mockResolvedValue(true);
      mockContainer.adminReplayGuard.claim.mockResolvedValue(true);
      mockContainer.userDeletionService.requestDeletion.mockResolvedValue({
        userId: targetId,
        scheduledHardDeleteAt: new Date('2026-06-19T12:00:00Z'),
        graceDays: 30,
      });
    });

    it('should reject a field it does not know, such as a self-attested adminId', async () => {
      expect((await del({ reason: 'x', adminId: TEST_ADMIN_USER_ID })).statusCode).toBe(400);
    });

    it('should reject a blank reason', async () => {
      expect((await del({ reason: '   ' })).statusCode).toBe(400);
    });

    it('should trim the reason it records', async () => {
      await del({ reason: '  duplicate account  ' });

      expect(mockContainer.userDeletionService.requestDeletion).toHaveBeenCalledWith(targetId, {
        origin: { initiatedBy: 'admin', adminId: TEST_ADMIN_USER_ID, reason: 'duplicate account' },
      });
    });
  });

  // ─── An admin deleting their own account through the self-service route ──

  describe('DELETE /api/users/me by an admin', () => {
    // A new user id per test: the route allows 5 deletions an hour per user.
    let me: string;
    const deleteMe = async () => {
      const token = app.jwt.sign({ userId: me, email: `${me}@example.com`, type: 'access' });
      return app.inject({
        method: 'DELETE',
        url: '/api/users/me',
        headers: { authorization: `Bearer ${token}`, 'content-type': 'application/json' },
        payload: {},
      });
    };

    beforeEach(() => {
      me = new ObjectId().toString();
      mockContainer.userDeletionService.requestDeletion.mockResolvedValue({
        userId: me,
        scheduledHardDeleteAt: new Date('2026-06-19T12:00:00Z'),
        graceDays: 30,
      });
    });

    it('should refuse the last active admin with 409 LAST_ADMIN, deleting nothing', async () => {
      mockContainer.userRepository.findById.mockResolvedValue(userDoc(me, { roles: ['admin'], roleSource: 'env' }));
      mockContainer.userRepository.listAdmins.mockResolvedValue([adminSummary(me)]);

      const res = await deleteMe();

      expect([res.statusCode, res.json().error]).toEqual([409, 'LAST_ADMIN']);
      expect(mockContainer.userDeletionService.requestDeletion).not.toHaveBeenCalled();
    });

    it('should recognise a legacy role:admin account as the last admin', async () => {
      mockContainer.userRepository.findById.mockResolvedValue(userDoc(me, { role: 'admin' }));
      mockContainer.userRepository.listAdmins.mockResolvedValue([adminSummary(me)]);

      expect((await deleteMe()).statusCode).toBe(409);
    });

    it('should not count a soft-deleted admin as the one that remains', async () => {
      mockContainer.userRepository.findById.mockResolvedValue(userDoc(me, { roles: ['admin'] }));
      mockContainer.userRepository.listAdmins.mockResolvedValue([
        adminSummary(me),
        adminSummary(new ObjectId().toString(), new Date()),
      ]);

      expect((await deleteMe()).statusCode).toBe(409);
    });

    it('should let an admin delete their account while another active admin remains', async () => {
      mockContainer.userRepository.findById.mockResolvedValue(userDoc(me, { roles: ['admin'] }));
      mockContainer.userRepository.listAdmins.mockResolvedValue([adminSummary(me), adminSummary(new ObjectId().toString())]);

      const res = await deleteMe();

      expect(res.statusCode).toBe(202);
      expect(mockContainer.userDeletionService.requestDeletion).toHaveBeenCalledWith(me, {
        origin: { initiatedBy: 'self', adminId: null, reason: null },
      });
    });

    it('should not look for other admins when the user is not an admin', async () => {
      mockContainer.userRepository.findById.mockResolvedValue(userDoc(me));

      const res = await deleteMe();

      expect(res.statusCode).toBe(202);
      expect(mockContainer.userRepository.listAdmins).not.toHaveBeenCalled();
    });

    it('should leave an already soft-deleted admin to the deletion service, not the guard', async () => {
      mockContainer.userRepository.findById.mockResolvedValue(userDoc(me, { roles: ['admin'], deletedAt: new Date() }));
      mockContainer.userRepository.listAdmins.mockResolvedValue([adminSummary(me, new Date())]);

      await deleteMe();

      expect(mockContainer.userDeletionService.requestDeletion).toHaveBeenCalled();
      expect(mockContainer.userRepository.listAdmins).not.toHaveBeenCalled();
    });
  });
});
