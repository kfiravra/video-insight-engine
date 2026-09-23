import { FastifyInstance } from 'fastify';
import { z } from 'zod';
import { isValidAdminKey, parseAdminIdHeader } from '../utils/admin-auth.js';

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
 * `/api/admin/queue/*`). Tier-aware admin role is a future enhancement.
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

export async function adminUsersRoutes(fastify: FastifyInstance): Promise<void> {
  const { userDeletionService } = fastify.container;

  fastify.delete('/:id', {
    config: {
      rateLimit: { max: 30, timeWindow: '1 hour' },
    },
  }, async (req, reply) => {
    if (!isValidAdminKey(req.headers['x-admin-key'])) {
      return reply.code(401).send({
        error: 'UNAUTHORIZED',
        message: 'Admin key required',
      });
    }

    const params = adminDeleteParamsSchema.parse(req.params);
    const query = adminDeleteQuerySchema.parse(req.query);
    const body = adminDeleteBodySchema.parse(req.body);

    const immediate = query.immediate === 'true' || query.immediate === '1';

    const adminIdHeader = parseAdminIdHeader(req.headers['x-admin-id']);
    if (!adminIdHeader.ok) {
      return reply.code(400).send({
        error: 'VALIDATION_ERROR',
        message: 'Invalid x-admin-id header',
      });
    }
    const adminId = adminIdHeader.adminId;

    if (immediate) {
      const audit = await userDeletionService.executeHardDelete(params.id, {
        initiatedBy: 'admin',
        adminId,
        reason: body?.reason ?? null,
      });
      return reply.send({
        userId: audit.originalUserId,
        completedAt: audit.completedAt.toISOString(),
        durationMs: audit.durationMs,
        counts: audit.counts,
        warnings: audit.warnings ?? [],
      });
    }

    // Non-immediate: same flow as the user-initiated delete but recorded as
    // initiated by an admin (the audit row tracks `initiatedBy` once the
    // cascade actually fires from the scheduler).
    const result = await userDeletionService.requestDeletion(params.id);
    return reply.code(202).send({
      userId: result.userId,
      scheduledHardDeleteAt: result.scheduledHardDeleteAt.toISOString(),
      graceDays: result.graceDays,
    });
  });
}
