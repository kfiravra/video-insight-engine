import type { FastifyInstance } from 'fastify';

import type { AlertConfigChange } from '../../services/alert-config.service.js';
import { alertConfigBodySchema } from '../../schemas/admin-alerts.schema.js';
import { auditRequestContext } from '../../utils/audit-context.js';

export interface AlertConfigResponse extends AlertConfigChange {
  auditId: string | null;
}

/**
 * POST /api/admin/alerts/config: replace the operator alert thresholds. Needs
 * `alerts:write` (a named admin; the shared key cannot) and a `reason`. The
 * change and the `adminAudit` row (before and after thresholds) are one
 * request; as everywhere, a failed audit write is logged and does not undo the
 * change. vie-admin's alert evaluator reads the collection this writes.
 */
export async function adminAlertsRoutes(fastify: FastifyInstance): Promise<void> {
  fastify.post('/config', {
    preHandler: [fastify.requireAdmin('alerts:write')],
  }, async (req): Promise<AlertConfigResponse> => {
    const { reason, ...next } = alertConfigBodySchema.parse(req.body);
    const { alertConfigService, auditService } = fastify.container;
    const change = await alertConfigService.replace(next);
    const auditId = await auditService.recordBestEffort({
      ...auditRequestContext(req),
      action: 'alerts.config.set',
      targetType: 'alert',
      targetId: 'default',
      before: change.before,
      after: change.after,
      reason,
    });
    return { ...change, auditId };
  });
}
