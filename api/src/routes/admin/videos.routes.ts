import { FastifyInstance } from 'fastify';
import { z } from 'zod';
import { isValidAdminKey, parseAdminIdHeader } from '../../utils/admin-auth.js';
import { youtubeIdSchema } from '../../utils/validation.js';

/**
 * Admin endpoint that removes a video from every store for every user:
 * Mongo summary + references, Qdrant vectors, S3 objects, Redis keys.
 * Guarded by the static admin key; `x-admin-id` is operator metadata for
 * the audit row, not auth.
 */

const adminVideoParamsSchema = z.object({ youtubeId: youtubeIdSchema });
const adminVideoBodySchema = z
  .object({ reason: z.string().min(1).max(500).optional() })
  .optional();

export async function adminVideosRoutes(fastify: FastifyInstance): Promise<void> {
  const { videoCascadeService } = fastify.container;

  // DELETE /api/admin/videos/:youtubeId
  // A valid admin key (the reconcile script purging orphans in bulk) gets a
  // higher ceiling; key guesses share the 30/h of the other admin routes.
  fastify.delete('/:youtubeId', {
    config: {
      rateLimit: {
        timeWindow: '1 hour',
        keyGenerator: (req) => (isValidAdminKey(req.headers['x-admin-key']) ? 'admin:valid' : `ip:${req.ip}`),
        max: (_req, key) => (key === 'admin:valid' ? 300 : 30),
      },
    },
  }, async (req, reply) => {
    if (!isValidAdminKey(req.headers['x-admin-key'])) {
      return reply.code(401).send({
        error: 'UNAUTHORIZED',
        message: 'Admin key required',
      });
    }

    const params = adminVideoParamsSchema.parse(req.params);
    const body = adminVideoBodySchema.parse(req.body);
    const adminIdHeader = parseAdminIdHeader(req.headers['x-admin-id']);
    if (!adminIdHeader.ok) {
      return reply.code(400).send({
        error: 'VALIDATION_ERROR',
        message: 'Invalid x-admin-id header',
      });
    }

    const result = await videoCascadeService.deleteVideo({
      scope: 'global',
      youtubeId: params.youtubeId,
      actor: {
        initiatedBy: 'admin',
        adminId: adminIdHeader.adminId,
        reason: body?.reason ?? null,
      },
    });
    return reply.code(200).send(result);
  });
}
