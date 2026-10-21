import { z } from 'zod';
import type { SSESynthesisCompleteEvent } from '@vie/types';

/**
 * `synthesis_complete` as relayed from the summarizer. Emitted up to three times
 * per run — early `{tldr, keyTakeaways}` at memory-done, the full superset at
 * synthesis-done (the hero rewrite's adds `brief` / `worthItFor` / `skipIf`),
 * then a corrected superset after the late group (the hero rewrite's carries
 * `readingMinutes`) — so every content field is optional. Unknown keys are
 * stripped: only these fields are persisted. The `satisfies` clause ties the
 * schema to the shared event type so the two can't drift apart.
 */
export const synthesisCompleteEventSchema = z.object({
  event: z.literal('synthesis_complete'),
  tldr: z.string().optional(),
  keyTakeaways: z.array(z.string()).optional(),
  masterSummary: z.string().optional(),
  seoDescription: z.string().optional(),
  brief: z.string().optional(),
  worthItFor: z.string().optional(),
  skipIf: z.string().optional(),
  readingMinutes: z.number().int().positive().optional(),
}) satisfies z.ZodType<SSESynthesisCompleteEvent>;

export type SynthesisFields = Omit<SSESynthesisCompleteEvent, 'event'>;
