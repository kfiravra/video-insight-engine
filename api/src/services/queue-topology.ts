/**
 * RabbitMQ topology for the video processing pipeline.
 *
 * These names + queue arguments MUST match what the Python worker declares
 * (services/summarizer/src/worker/topology.py). RabbitMQ rejects assertions
 * that disagree with an existing object, so a mismatch surfaces immediately
 * at boot — useful invariant for cross-language contracts.
 */

import { z } from 'zod';
import { PLANS } from '@vie/types';
import type { PlanId } from '@vie/types';

export const QUEUE_TOPOLOGY = {
  exchange: 'vie.pipeline',
  queue: 'vie.pipeline.jobs',
  routingKey: 'video.process',
  dlx: 'vie.pipeline.dlx',
  dlq: 'vie.pipeline.dlq',
  dlqRoutingKey: 'video.process.dead',
} as const;

/** Within the server-side x-max-priority cap. A Pro account's run jumps ahead of a free account's. */
export const PRIORITY_FREE = PLANS.free.queuePriority;
export const PRIORITY_PAID = PLANS.pro.queuePriority;
export const MAX_PRIORITY = 10;

/** 1 hour TTL — long enough for backed-up queues, short enough to surface DLQ stalls. */
export const MESSAGE_TTL_MS = 60 * 60 * 1000;

export const queueArguments = {
  'x-max-priority': MAX_PRIORITY,
  'x-message-ttl': MESSAGE_TTL_MS,
  'x-dead-letter-exchange': QUEUE_TOPOLOGY.dlx,
  'x-dead-letter-routing-key': QUEUE_TOPOLOGY.dlqRoutingKey,
} as const;

const providerSchema = z.enum(['anthropic', 'openai', 'gemini']);

/**
 * The queue plan of a run (billing-tokens D18): `paid` for a Pro account,
 * `free` otherwise, and for a run that took no hold (serve-only). The wire
 * values are unchanged since billing-roles P3.4, so the worker's topology and
 * payload schema stay byte-identical.
 */
export const QUEUE_PLANS = ['free', 'paid'] as const;
export type QueuePlan = (typeof QUEUE_PLANS)[number];

/**
 * Wire-format job payload.
 *
 * Keep ISO strings (not Date) so the consumer's Pydantic model can parse without
 * a JSON reviver. `plan` defaults to `free` so every message has a defined
 * priority class (it replaced `tier` in P3.4; the worker ignores a leftover
 * `tier` on a message published before the deploy).
 */
export const videoJobPayloadSchema = z.object({
  videoSummaryId: z.string().min(1),
  youtubeId: z.string().regex(/^[a-zA-Z0-9_-]{11}$/),
  url: z.string().url(),
  userId: z.string().nullable().default(null),
  plan: z.enum(QUEUE_PLANS).default('free'),
  priority: z.number().int().min(1).max(MAX_PRIORITY),
  providers: z
    .object({
      default: providerSchema,
      fast: providerSchema.optional(),
      fallback: providerSchema.nullable().optional(),
    })
    .nullable()
    .default(null),
  bypassCache: z.boolean().default(false),
  requestId: z.string().min(1),
  /** This attempt's run id (P0b): the trace id is derived from it; the summarizer mints a fresh one on a retry. */
  runId: z.string().min(1).optional(),
  pipelineVersion: z.string().min(1).optional(),
  /** P1 (D8): the chat session that asked for the run; no producer yet. */
  chatSessionId: z.string().min(1).optional(),
  attempt: z.number().int().min(1).default(1),
  createdAt: z.string().datetime(),
});

export type VideoJobPayload = z.infer<typeof videoJobPayloadSchema>;

/** The queue plan of a run from the account's plan: Pro accounts' runs queue as `paid`. */
export function queuePlanFor(planId: PlanId): QueuePlan {
  return planId === 'pro' ? 'paid' : 'free';
}

/** Pick priority by queue plan — keep this in one place so all publish sites agree. */
export function priorityForPlan(plan: QueuePlan): number {
  return plan === 'paid' ? PRIORITY_PAID : PRIORITY_FREE;
}
