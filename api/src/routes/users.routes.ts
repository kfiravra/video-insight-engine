import type { FastifyInstance, FastifyRequest } from 'fastify';
import { z } from 'zod';
import { effectiveRoles, type UserDocument } from '../repositories/user.repository.js';
import { adminActorUserId, auditRequestContext, type AuditRequestContext } from '../utils/audit-context.js';
import { ConflictError } from '../utils/errors.js';
import type { Container } from '../container.js';

/**
 * Refuse to delete the last active admin: nobody would be left to sign in to the
 * panel, and boot seeding never creates users to repair it. The same rule guards
 * `DELETE /api/users/me` and `DELETE /api/admin/users/:id`. A soft-deleted
 * account is not an active admin, so a repeated request still reaches the
 * deletion service and gets its own 409 `ACCOUNT_ALREADY_DELETED`.
 */
async function assertNotLastActiveAdmin(container: Container, user: UserDocument): Promise<void> {
  if (user.deletedAt || !effectiveRoles(user).includes('admin')) return;
  const others = (await container.userRepository.listAdmins()).filter(
    (admin) => !admin.deletedAt && admin.id !== user._id.toString(),
  );
  if (others.length === 0) {
    throw new ConflictError('LAST_ADMIN', 'This is the last active admin account; make another admin first');
  }
}

/**
 * Self-service GDPR Article 17 endpoints.
 *
 *  - DELETE /api/users/me         schedule erasure (30-day grace); the last active
 *                                 admin is refused (409 `LAST_ADMIN`), as on the admin route
 *  - POST   /api/users/me/restore cancel a pending deletion
 *
 * The cascade itself runs from `userDeletionService` — see
 * `services/user-deletion.service.ts` for the saga.
 */
export async function userMeRoutes(fastify: FastifyInstance): Promise<void> {
  const { userDeletionService } = fastify.container;

  fastify.delete('/', {
    preHandler: [fastify.authenticate],
    config: {
      rateLimit: { max: 5, timeWindow: '1 hour' },
      // Skip the JWT plugin's soft-delete short-circuit so a re-request
      // surfaces the more specific 409 `ACCOUNT_ALREADY_DELETED` from the
      // service instead of the generic 403 `ACCOUNT_DELETION_PENDING`.
      skipSoftDeleteCheck: true,
    },
  }, async (req, reply) => {
    const user = await fastify.container.userRepository.findById(req.user.userId);
    // An unknown id is left to the deletion service, which answers 404.
    if (user) await assertNotLastActiveAdmin(fastify.container, user);

    // The origin is kept so the scheduled cascade is recorded as the user's own
    // request ('self') instead of the scheduler's.
    const result = await userDeletionService.requestDeletion(req.user.userId, {
      origin: { initiatedBy: 'self', adminId: null, reason: null },
    });

    // Clear the refresh cookie so the browser drops the session immediately.
    // The access token remains cryptographically valid for its TTL, but the
    // JWT plugin's soft-delete check on every request rejects it.
    reply.clearCookie('refreshToken', { path: '/api/auth/refresh' });

    return reply.code(202).send({
      userId: result.userId,
      scheduledHardDeleteAt: result.scheduledHardDeleteAt.toISOString(),
      graceDays: result.graceDays,
      message: `Account scheduled for deletion in ${result.graceDays} days. Contact support to cancel.`,
    });
  });

  fastify.post('/restore', {
    config: {
      rateLimit: { max: 5, timeWindow: '1 hour' },
    },
  }, async (req, reply) => {
    // Restore cannot use the standard `authenticate` decorator because that
    // hook rejects soft-deleted users. Verify the JWT manually here and
    // bypass the soft-delete check so users can recover within the grace
    // window.
    try {
      await req.jwtVerify();
    } catch {
      return reply.code(401).send({
        error: 'UNAUTHORIZED',
        message: 'Invalid or expired token',
      });
    }

    const { userId } = req.user;
    const pending = await fastify.container.userRepository.findById(userId);
    const restored = await userDeletionService.cancelDeletion(userId);
    await auditAdminDeletionCancelled(fastify.container, req, userId, pending);
    return reply.send({
      id: restored._id.toString(),
      email: restored.email,
      name: restored.name,
    });
  });
}

/**
 * A user restoring an account an admin had scheduled for deletion undoes an
 * admin action, so it leaves an `adminAudit` row (actor: the user themselves).
 * A restore of the user's own request needs none. Best effort, like every audit
 * write: the restore has already happened.
 */
async function auditAdminDeletionCancelled(
  container: Container,
  req: FastifyRequest,
  userId: string,
  pending: UserDocument | null,
): Promise<void> {
  if (pending?.deletionOrigin?.initiatedBy !== 'admin') return;
  await container.auditService.recordBestEffort({
    actorId: userId,
    requestId: req.id,
    ip: req.ip,
    action: 'users.delete.cancel',
    targetType: 'user',
    targetId: userId,
    before: {
      deletedAt: pending.deletedAt ?? null,
      hardDeleteAt: pending.hardDeleteAt ?? null,
      scheduledBy: pending.deletionOrigin.adminId,
    },
    after: { deletedAt: null, hardDeleteAt: null },
  });
}

/**
 * `DELETE /api/admin/users/:id`: delete an account, either now
 * (`?immediate=true`, the whole GDPR cascade, synchronously) or scheduled
 * (the same 30-day soft delete a user gets, with the admin stored as its
 * origin so the later cascade is recorded as theirs, not the scheduler's).
 *
 * Needs the `users:delete` permission: a named admin with a signed
 * `x-admin-actor`; the shared key alone cannot delete. Either mode writes an
 * `adminAudit` row at request time with the account's state before it. The
 * last active admin cannot be deleted (409 `LAST_ADMIN`); an admin may delete
 * themselves only while another active admin remains, here and on
 * `DELETE /api/users/me`.
 */
const adminDeleteParamsSchema = z.object({
  id: z.string().regex(/^[a-f0-9]{24}$/i, 'id must be a 24-char hex ObjectId'),
});
const adminDeleteQuerySchema = z.object({
  immediate: z.enum(['true', 'false', '1', '0']).optional(),
});
const adminDeleteBodySchema = z
  .object({ reason: z.string().trim().min(1).max(500).optional() })
  .strict()
  .optional();

interface AdminDeleteInput {
  userId: string;
  adminId: string | null;
  reason: string | null;
  auditContext: AuditRequestContext;
}

/** The account as it stood before the delete, without anything personal (no email, no name). */
function accountSnapshot(user: UserDocument): Record<string, unknown> {
  return {
    roles: effectiveRoles(user),
    roleSource: user.roleSource ?? null,
    tier: user.tier ?? 'free',
    deletedAt: user.deletedAt ?? null,
    createdAt: user.createdAt,
  };
}

/**
 * Load the account, refusing the last active admin, and return its snapshot for
 * the audit row. An unknown id is left to the deletion service, which answers 404.
 */
async function loadDeletionTarget(container: Container, userId: string): Promise<Record<string, unknown> | undefined> {
  const user = await container.userRepository.findById(userId);
  if (!user) return undefined;
  await assertNotLastActiveAdmin(container, user);
  return accountSnapshot(user);
}

async function deleteImmediately(container: Container, input: AdminDeleteInput): Promise<object> {
  const { userDeletionService, auditService } = container;
  const before = await loadDeletionTarget(container, input.userId);
  const audit = await userDeletionService.executeHardDelete(input.userId, {
    initiatedBy: 'admin',
    adminId: input.adminId,
    reason: input.reason,
  });
  await auditService.recordBestEffort({
    ...input.auditContext,
    action: 'users.delete',
    targetType: 'user',
    targetId: audit.originalUserId,
    before,
    after: { mode: 'immediate', userDeletionId: audit._id.toString() },
    reason: input.reason ?? undefined,
  });
  return {
    userId: audit.originalUserId,
    completedAt: audit.completedAt.toISOString(),
    durationMs: audit.durationMs,
    counts: audit.counts,
    warnings: audit.warnings ?? [],
  };
}

/**
 * Same soft delete as the user-initiated flow, with the admin stored as its
 * origin so the scheduler's later cascade names them instead of 'scheduler'.
 */
async function scheduleDeletion(container: Container, input: AdminDeleteInput): Promise<object> {
  const { userDeletionService, auditService } = container;
  const before = await loadDeletionTarget(container, input.userId);
  const result = await userDeletionService.requestDeletion(input.userId, {
    origin: { initiatedBy: 'admin', adminId: input.adminId, reason: input.reason },
  });
  const scheduledHardDeleteAt = result.scheduledHardDeleteAt.toISOString();
  await auditService.recordBestEffort({
    ...input.auditContext,
    action: 'users.delete',
    targetType: 'user',
    targetId: input.userId.toLowerCase(),
    before,
    after: { mode: 'scheduled', scheduledHardDeleteAt },
    reason: input.reason ?? undefined,
  });
  return { userId: result.userId, scheduledHardDeleteAt, graceDays: result.graceDays };
}

export async function adminUsersRoutes(fastify: FastifyInstance): Promise<void> {
  fastify.delete('/:id', {
    preHandler: [fastify.requireAdmin('users:delete')],
    config: {
      rateLimit: { max: 30, timeWindow: '1 hour' },
    },
  }, async (req, reply) => {
    const params = adminDeleteParamsSchema.parse(req.params);
    const query = adminDeleteQuerySchema.parse(req.query);
    const body = adminDeleteBodySchema.parse(req.body);

    const immediate = query.immediate === 'true' || query.immediate === '1';

    const input: AdminDeleteInput = {
      userId: params.id,
      adminId: adminActorUserId(req),
      reason: body?.reason ?? null,
      auditContext: auditRequestContext(req),
    };

    if (immediate) {
      return reply.send(await deleteImmediately(fastify.container, input));
    }
    return reply.code(202).send(await scheduleDeletion(fastify.container, input));
  });
}
