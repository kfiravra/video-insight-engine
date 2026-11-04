import { FastifyInstance } from 'fastify';
import { z } from 'zod';
import { isValidInternalSecret } from '../utils/internal-auth.js';
import { objectIdSchema } from '../utils/validation.js';

// The two cost hand-backs of the token ledger, both under INTERNAL_SECRET:
//   POST /internal/run-cost           the summarizer, once a run's total LLM $ is
//                                     known (after `pipeline.timing` persisted):
//                                     settles the run's hold on its real cost
//   POST /internal/assistant/usage    the assistant, after a chat turn, library
//                                     chat or action: debits its real cost
// Kept apart from internal.routes.ts / internal-assistant.routes.ts so the
// status relay and the assistant's library callbacks stay as they are.

const runIdSchema = z.string().min(1).max(200);
const costSchema = z.number().finite().min(0);

const runCostBodySchema = z.object({
  videoSummaryId: objectIdSchema,
  /** The run's billing id as the summarizer read it from the cache row; absent on rows dispatched before it existed. */
  billingRunId: runIdSchema.optional().nullable(),
  costUsd: costSchema,
  llmCalls: z.number().int().min(0).optional(),
}).strict();

const executionUsageBodySchema = z.object({
  /** The api's per-request id (`X-Execution-Id`). */
  executionId: runIdSchema,
  /** The request id the assistant's llm_usage rows carry. */
  requestId: runIdSchema,
  kind: z.enum(['chat', 'library_chat', 'action']),
  costUsd: costSchema,
  calls: z.number().int().min(0).optional(),
}).strict();

export async function internalBillingRoutes(fastify: FastifyInstance): Promise<void> {
  const { billingEnforcement, billingService } = fastify.container;

  // POST /internal/run-cost — settle the run's hold on its real cost. A stamp
  // that no longer names this run (a newer dispatch replaced it) is ignored;
  // the repair job later releases the old hold. Idempotent: a repeat is already_debited.
  fastify.post<{ Body: z.infer<typeof runCostBodySchema> }>('/run-cost', async (req, reply) => {
    if (!isValidInternalSecret(req.headers['x-internal-secret'])) {
      return reply.status(401).send({ error: 'Unauthorized' });
    }
    const { videoSummaryId, billingRunId, costUsd } = runCostBodySchema.parse(req.body);
    const status = await billingEnforcement.runCosted(videoSummaryId, billingRunId ?? null, costUsd);
    return { received: true, status };
  });

  // POST /internal/assistant/usage — debit a chat turn / library chat / action.
  // The user comes from X-User-Id (never the body); idempotent on executionId.
  fastify.post<{ Body: z.infer<typeof executionUsageBodySchema> }>('/assistant/usage', {
    preHandler: [fastify.authenticateInternal],
  }, async (req) => {
    const { executionId, requestId, kind, costUsd } = executionUsageBodySchema.parse(req.body);
    const outcome = await billingService.debitExecution({ userId: req.user.userId, executionId, kind, requestId, costUsd });
    return { received: true, status: outcome.status, tokens: outcome.tokens };
  });
}
