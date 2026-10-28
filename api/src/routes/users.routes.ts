import { FastifyInstance } from 'fastify';
import { z } from 'zod';
import { adminActorUserId, auditRequestContext, type AuditRequestContext } from '../utils/audit-context.js';
import type { Container } from '../container.js';

/**
 * Self-service GDPR Article 17 endpoints.
 *
 *  - DELETE /api/users/me         schedule erasure (30-day grace)
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
    const result = await userDeletionService.requestDeletion(req.user.userId);

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

    const restored = await userDeletionService.cancelDeletion(req.user.userId);
    return reply.send({
      id: restored._id.toString(),
      email: restored.email,
      name: restored.name,
    });
  });
}

/**
 * Admin-only immediate deletion endpoint. Bypasses the 30-day grace window
 * and runs the cascade synchronously, returning the audit row.
 *
 * Auth: `x-admin-key` header matching `ADMIN_API_KEY` env (same pattern as
 * `/api/admin/queue/*`). Both modes write an `adminAudit` row at request time;
 * a scheduled delete also stores its origin on the user so the later cascade
 * is recorded as an admin deletion, not a scheduler one.
 */
const adminDeleteParamsSchema = z.object({
  id: z.string().regex(/^[a-f0-9]{24}$/i, 'id must be a 24-char hex ObjectId'),
});
const adminDeleteQuerySchema = z.object({
  immediate: z.enum(['true', 'false', '1', '0']).optional(),
});
const adminDeleteBodySchema = z
  .object({ reason: z.string().min(1).max(500).optional() })
  .optional();

interface AdminDeleteInput {
  userId: string;
  adminId: string | null;
  reason: string | null;
  auditContext: AuditRequestContext;
}

async function deleteImmediately(container: Container, input: AdminDeleteInput): Promise<object> {
  const { userDeletionService, auditService } = container;
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
  const result = await userDeletionService.requestDeletion(input.userId, {
    origin: { initiatedBy: 'admin', adminId: input.adminId, reason: input.reason },
  });
  const scheduledHardDeleteAt = result.scheduledHardDeleteAt.toISOString();
  await auditService.recordBestEffort({
    ...input.auditContext,
    action: 'users.delete',
    targetType: 'user',
    targetId: input.userId.toLowerCase(),
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
