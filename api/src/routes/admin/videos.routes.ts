import { FastifyInstance } from 'fastify';
import { z } from 'zod';
import { isValidAdminKey } from '../../utils/admin-auth.js';
import { adminActorUserId, auditRequestContext } from '../../utils/audit-context.js';
import { youtubeIdSchema } from '../../utils/validation.js';

/**
 * Admin endpoint that removes a video from every store for every user:
 * Mongo summary + references, Qdrant vectors, S3 objects, Redis keys.
 * Needs the `videos:purge` permission: a named admin (signed `x-admin-actor`)
 * who still holds it. The shared key alone cannot purge. The admin is recorded
 * on both audit rows (`videoDeletions` and `adminAudit`).
 */

const adminVideoParamsSchema = z.object({ youtubeId: youtubeIdSchema });
const adminVideoBodySchema = z
  .object({ reason: z.string().trim().min(1).max(500).optional() })
  .strict()
  .optional();

export async function adminVideosRoutes(fastify: FastifyInstance): Promise<void> {
  const { videoCascadeService, videoRepository, auditService } = fastify.container;

  // DELETE /api/admin/videos/:youtubeId
  // A valid admin key (the reconcile script purging orphans in bulk) gets a
  // higher ceiling; key guesses share the 30/h of the other admin routes.
  fastify.delete('/:youtubeId', {
    preHandler: [fastify.requireAdmin('videos:purge')],
    config: {
      rateLimit: {
        timeWindow: '1 hour',
        keyGenerator: (req) => (isValidAdminKey(req.headers['x-admin-key']) ? 'admin:valid' : `ip:${req.ip}`),
        max: (_req, key) => (key === 'admin:valid' ? 300 : 30),
      },
    },
  }, async (req, reply) => {
    const params = adminVideoParamsSchema.parse(req.params);
    const body = adminVideoBodySchema.parse(req.body);
    // What exists now, for the audit row: the purge deletes the evidence.
    const existing = await videoRepository.findCacheKeysByYoutubeId(params.youtubeId);

    const result = await videoCascadeService.deleteVideo({
      scope: 'global',
      youtubeId: params.youtubeId,
      actor: {
        initiatedBy: 'admin',
        adminId: adminActorUserId(req),
        reason: body?.reason ?? null,
      },
    });
    // Only a purge that ran is audited; a summarizer failure above aborts
    // before any row is touched and surfaces as a 502.
    await auditService.recordBestEffort({
      ...auditRequestContext(req),
      action: 'videos.purge',
      targetType: 'video',
      targetId: params.youtubeId,
      before: { summaryIds: existing.map((row) => row._id.toString()), shared: existing.some((row) => Boolean(row.shareSlug)) },
      after: { summaryIds: result.summaryIds, counts: result.counts, partial: result.partial },
      reason: body?.reason,
    });
    return reply.code(200).send(result);
  });
}
