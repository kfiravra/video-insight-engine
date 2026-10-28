import type { FastifyRequest } from 'fastify';

import type { RecordAuditInput } from '../services/audit.service.js';

/** Actor recorded when an admin route is called with the shared key and no operator id. */
export const UNATTRIBUTED_ACTOR_ID = 'system:admin-key';

export type AuditRequestContext = Pick<RecordAuditInput, 'actorId' | 'requestId' | 'ip'>;

/**
 * The request-derived half of an audit row: who acted, which request, from
 * where. `adminId` is the operator id parsed from `x-admin-id` today; once the
 * signed actor header lands this is the one call to point at `request.adminActor`.
 */
export function auditRequestContext(req: FastifyRequest, adminId: string | null): AuditRequestContext {
  return {
    actorId: adminId ?? UNATTRIBUTED_ACTOR_ID,
    requestId: req.id,
    ip: req.ip,
  };
}
