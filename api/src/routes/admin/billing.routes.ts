import { createHash } from 'node:crypto';
import { ObjectId } from 'mongodb';
import type { FastifyInstance, FastifyRequest } from 'fastify';
import type { AccountSnapshot } from '@vie/types';

import type { Container } from '../../container.js';
import { StaleAccountVersionError } from '../../repositories/billing.repository.js';
import {
  accountDetailQuerySchema,
  accountParamsSchema,
  adjustBodySchema,
  createRecurringGrantBodySchema,
  deleteRecurringGrantBodySchema,
  grantCreditsBodySchema,
  idempotencyKeySchema,
  recurringGrantParamsSchema,
  setCapsBodySchema,
  setPlanBodySchema,
} from '../../schemas/admin-billing.schema.js';
import type { AdminMutationContext, MutationResult } from '../../services/billing/billing.service.js';
import { adminActorUserId, auditRequestContext } from '../../utils/audit-context.js';
import { AdminPermissionError, ConflictError, IdempotencyKeyReusedError } from '../../utils/errors.js';
import { hashRequest } from '../../utils/request-hash.js';

/**
 * Admin endpoints over the minutes ledger (`BillingService`, Contract 1):
 *
 *   GET    /accounts/:userId                          billing:read
 *   POST   /accounts/:userId/plan                     plan:set
 *   POST   /accounts/:userId/credits                  billing:grant
 *   POST   /accounts/:userId/caps                     caps:set
 *   POST   /accounts/:userId/adjust                   billing:grant
 *   POST   /accounts/:userId/recurring-grants         billing:grant
 *   DELETE /accounts/:userId/recurring-grants/:grantId billing:grant
 *
 * Every mutation needs a `reason` (1–500 characters) and a named admin (the
 * signed `x-admin-actor`; the shared key can only read). The route owns the
 * audit: it pre-generates the audit id, hands it to the service so the ledger
 * entries carry it, then writes the `adminAudit` row with the same id. A failed
 * audit write is logged at error with that id and does not fail the request,
 * because the ledger change has already committed.
 *
 * An optional `Idempotency-Key` header makes a retry safe: the audit id is
 * derived from the key, the admin, the action and the target, so a repeated
 * request replays the same ledger keys and changes nothing. The audit row also
 * stores a hash of the whole request, and the same key arriving with a
 * different request is refused with 422 `IDEMPOTENCY_KEY_REUSED` before
 * anything runs: otherwise the second request would be dropped as a replay
 * (or, for caps, applied with no audit row).
 */

const IDEMPOTENCY_HEADER = 'idempotency-key';

interface AuditedMutation<T extends MutationResult<AccountSnapshot>> {
  action: string;
  userId: string;
  reason: string;
  /** The validated request body; hashed to recognise the same request, and a different one under the same key. */
  input: unknown;
  /** What distinguishes two targets of the same action under one key (e.g. the grant id). */
  scope?: string;
  run: (ctx: AdminMutationContext) => Promise<T>;
  /**
   * Facts recorded next to the account snapshot: created ids and the request's
   * own parameters. Write `run` before `details` at call sites: TypeScript
   * infers T from the first function it reads.
   */
  details?: (result: NoInfer<T>) => Record<string, unknown>;
}

/** Random per request, or stable for a given key so a retry reuses the ledger's idempotency keys. */
function resolveAuditId(req: FastifyRequest, actorId: string, action: string, userId: string, scope = ''): string {
  const header = req.headers[IDEMPOTENCY_HEADER];
  if (header === undefined) return new ObjectId().toHexString();
  const key = idempotencyKeySchema.parse(header);
  const digest = createHash('sha256').update([actorId, action, userId.toLowerCase(), scope, key].join('|')).digest('hex');
  return digest.slice(0, 24);
}

/** Heavy contention on one account can exhaust the transaction retries; that is a retry for the caller, not a 500. */
async function guardContention<T>(work: () => Promise<T>): Promise<T> {
  try {
    return await work();
  } catch (err) {
    if (err instanceof StaleAccountVersionError) {
      throw new ConflictError('ACCOUNT_BUSY', 'The account is being changed by another request; retry in a moment');
    }
    throw err;
  }
}

/** A key already recorded for another request is a client bug; do not run the new one as if it were a retry. */
async function refuseReusedKey(container: Container, req: FastifyRequest, auditId: string, requestHash: string): Promise<void> {
  if (req.headers[IDEMPOTENCY_HEADER] === undefined) return;
  const recorded = await container.auditService.findRequestHash(auditId);
  if (recorded !== null && recorded !== requestHash) throw new IdempotencyKeyReusedError();
}

async function auditedMutation<T extends MutationResult<AccountSnapshot>>(
  container: Container,
  req: FastifyRequest,
  spec: AuditedMutation<T>,
): Promise<T & { auditId: string }> {
  const actorId = adminActorUserId(req);
  if (actorId === null) throw new AdminPermissionError('A named admin is required');
  const { action, userId, reason, scope, input, details, run } = spec;
  const auditId = resolveAuditId(req, actorId, action, userId, scope);
  const requestHash = hashRequest({ action, userId: userId.toLowerCase(), scope: scope ?? null, input });
  await refuseReusedKey(container, req, auditId, requestHash);

  const result = await guardContention(() => run({ actorId, reason, auditId }));

  await container.auditService.recordBestEffort({
    ...auditRequestContext(req),
    auditId,
    action,
    targetType: 'user',
    targetId: userId.toLowerCase(),
    before: result.before,
    after: { ...result.after, ...details?.(result) },
    reason,
    requestHash,
  });
  return { ...result, auditId };
}

export async function adminBillingRoutes(fastify: FastifyInstance): Promise<void> {
  registerAccountRead(fastify);
  registerPlanAndCredits(fastify);
  registerCapsAndAdjust(fastify);
  registerRecurringGrants(fastify);
}

function registerAccountRead(fastify: FastifyInstance): void {
  fastify.get('/accounts/:userId', {
    preHandler: [fastify.requireAdmin('billing:read')],
  }, async (req) => {
    const { userId } = accountParamsSchema.parse(req.params);
    const { ledgerLimit, ledgerBefore } = accountDetailQuerySchema.parse(req.query);
    return fastify.container.billingService.getAccountDetail(userId, { ledgerLimit, ledgerBefore });
  });
}

function registerPlanAndCredits(fastify: FastifyInstance): void {
  fastify.post('/accounts/:userId/plan', {
    preHandler: [fastify.requireAdmin('plan:set')],
  }, async (req) => {
    const { userId } = accountParamsSchema.parse(req.params);
    const body = setPlanBodySchema.parse(req.body);
    const { reason, ...plan } = body;
    return auditedMutation(fastify.container, req, {
      action: 'billing.plan.set',
      userId,
      reason,
      input: body,
      run: (ctx) => fastify.container.billingService.setPlan(userId, plan, ctx),
    });
  });

  fastify.post('/accounts/:userId/credits', {
    preHandler: [fastify.requireAdmin('billing:grant')],
  }, async (req) => {
    const { userId } = accountParamsSchema.parse(req.params);
    const body = grantCreditsBodySchema.parse(req.body);
    const { reason, ...grant } = body;
    return auditedMutation(fastify.container, req, {
      action: 'billing.credits.grant',
      userId,
      reason,
      input: body,
      run: (ctx) => fastify.container.billingService.grantLot(userId, grant, ctx),
      details: (result) => ({ lotId: result.lotId, minutes: grant.minutes, expiresAt: grant.expiresAt ?? null }),
    });
  });
}

function registerCapsAndAdjust(fastify: FastifyInstance): void {
  fastify.post('/accounts/:userId/caps', {
    preHandler: [fastify.requireAdmin('caps:set')],
  }, async (req) => {
    const { userId } = accountParamsSchema.parse(req.params);
    const body = setCapsBodySchema.parse(req.body);
    const { reason, ...caps } = body;
    return auditedMutation(fastify.container, req, {
      action: 'billing.caps.set',
      userId,
      reason,
      input: body,
      run: (ctx) => fastify.container.billingService.setCaps(userId, caps, ctx),
    });
  });

  fastify.post('/accounts/:userId/adjust', {
    preHandler: [fastify.requireAdmin('billing:grant')],
  }, async (req) => {
    const { userId } = accountParamsSchema.parse(req.params);
    const body = adjustBodySchema.parse(req.body);
    const { reason, ...adjustment } = body;
    return auditedMutation(fastify.container, req, {
      action: 'billing.adjust',
      userId,
      reason,
      input: body,
      run: (ctx) => fastify.container.billingService.adjust(userId, adjustment, ctx),
      details: () => ({ bucket: adjustment.bucket, minutes: adjustment.minutes }),
    });
  });
}

function registerRecurringGrants(fastify: FastifyInstance): void {
  fastify.post('/accounts/:userId/recurring-grants', {
    preHandler: [fastify.requireAdmin('billing:grant')],
  }, async (req) => {
    const { userId } = accountParamsSchema.parse(req.params);
    const body = createRecurringGrantBodySchema.parse(req.body);
    const { reason, ...grant } = body;
    return auditedMutation(fastify.container, req, {
      action: 'billing.recurring.create',
      userId,
      reason,
      input: body,
      run: (ctx) => fastify.container.billingService.createRecurringGrant(userId, grant, ctx),
      details: (result) => ({ grantId: result.grantId, grant }),
    });
  });

  fastify.delete('/accounts/:userId/recurring-grants/:grantId', {
    preHandler: [fastify.requireAdmin('billing:grant')],
  }, async (req) => {
    const { userId, grantId } = recurringGrantParamsSchema.parse(req.params);
    const body = deleteRecurringGrantBodySchema.parse(req.body);
    const { reason } = body;
    return auditedMutation(fastify.container, req, {
      action: 'billing.recurring.delete',
      userId,
      reason,
      input: body,
      scope: grantId,
      run: (ctx) => fastify.container.billingService.deleteRecurringGrant(userId, grantId, ctx),
      details: () => ({ grantId }),
    });
  });
}
