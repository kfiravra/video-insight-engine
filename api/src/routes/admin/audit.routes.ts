import type { FastifyInstance } from 'fastify';
import type { AdminAuditView } from '@vie/types';

import { auditListQuerySchema } from '../../schemas/admin-billing.schema.js';

const DEFAULT_LIMIT = 50;

export interface AuditListResponse {
  entries: AdminAuditView[];
  /** Pass as `before` for the next (older) page; null when this page was the last. */
  nextBefore: string | null;
}

/**
 * GET /api/admin/audit?targetId=&actorId=&limit=&before= — the admin action
 * log, newest first. Needs `audit:read`. A user's history is
 * `?targetId=<userId>`; `before` is the previous page's `nextBefore`.
 */
export async function adminAuditRoutes(fastify: FastifyInstance): Promise<void> {
  fastify.get('/', {
    preHandler: [fastify.requireAdmin('audit:read')],
  }, async (req): Promise<AuditListResponse> => {
    const query = auditListQuerySchema.parse(req.query);
    const limit = query.limit ?? DEFAULT_LIMIT;
    const entries = await fastify.container.auditService.list({ ...query, limit });
    const last = entries[entries.length - 1];
    return { entries, nextBefore: entries.length === limit && last ? last.createdAt : null };
  });
}
