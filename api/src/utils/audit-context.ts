import type { FastifyRequest } from 'fastify';

import type { RecordAuditInput } from '../services/audit.service.js';

/** Actor recorded for a request made with the shared admin key and no named admin (reads only). */
export const UNATTRIBUTED_ACTOR_ID = 'system:admin-key';

export type AuditRequestContext = Pick<RecordAuditInput, 'actorId' | 'requestId' | 'ip'>;

/** The `users._id` of the admin behind this request, or null for the shared machine key. */
export function adminActorUserId(req: FastifyRequest): string | null {
  return req.adminActor?.kind === 'user' ? req.adminActor.userId : null;
}

/**
 * The request-derived half of an audit row: who acted (the signed actor
 * `requireAdmin` resolved), which request, from where.
 */
export function auditRequestContext(req: FastifyRequest): AuditRequestContext {
  return {
    actorId: adminActorUserId(req) ?? UNATTRIBUTED_ACTOR_ID,
    requestId: req.id,
    ip: req.ip,
  };
}
