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
import type { BucketSplit } from '@vie/types';

export const QUEUE_TOPOLOGY = {
  exchange: 'vie.pipeline',
  queue: 'vie.pipeline.jobs',
  routingKey: 'video.process',
  dlx: 'vie.pipeline.dlx',
  dlq: 'vie.pipeline.dlq',
  dlqRoutingKey: 'video.process.dead',
} as const;

/** Within the server-side x-max-priority cap. A run paid with plan or credit minutes jumps ahead of a free one. */
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
 * What pays for the run, as the queue sees it (billing-roles P3.4): `paid`
 * when its minutes hold takes plan or credit minutes, `free` for the free
 * video (or a run that took no hold). Not the account's plan: a Pro account's
 * free video queues as `free`, a free account's credit minutes as `paid`.
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
  attempt: z.number().int().min(1).default(1),
  createdAt: z.string().datetime(),
});

export type VideoJobPayload = z.infer<typeof videoJobPayloadSchema>;

/** The queue plan of a run from its hold's split: any plan or credit minutes make it `paid`. */
export function queuePlanOf(split: Pick<BucketSplit, 'plan' | 'credit'>): QueuePlan {
  return split.plan > 0 || split.credit > 0 ? 'paid' : 'free';
}

/** Pick priority by queue plan — keep this in one place so all publish sites agree. */
export function priorityForPlan(plan: QueuePlan): number {
  return plan === 'paid' ? PRIORITY_PAID : PRIORITY_FREE;
}
