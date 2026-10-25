import { z } from 'zod';
import type { SSESynthesisCompleteEvent } from '@vie/types';

/**
 * `synthesis_complete` as relayed from the summarizer. Emitted up to three times
 * per run — early `{tldr, keyTakeaways}` at memory-done, the full superset at
 * synthesis-done (the hero rewrite's adds `brief` / `worthItFor` / `skipIf`),
 * then a corrected superset after the late group, marked `final` (the hero
 * rewrite's carries `readingMinutes`) — so every content field is optional.
 * Unknown keys are stripped: only these fields are persisted. An out-of-contract
 * hero field (or `final`) is dropped, never the emission — the summarizer only
 * logs contract drift in production, and one bad optional field must not lose
 * the tldr / takeaways / brief beside it. The `satisfies` clause ties the
 * schema's output to the shared event type so the two can't drift apart.
 */
export const synthesisCompleteEventSchema = z.object({
  event: z.literal('synthesis_complete'),
  tldr: z.string().optional(),
  keyTakeaways: z.array(z.string()).optional(),
  masterSummary: z.string().optional(),
  seoDescription: z.string().optional(),
  brief: z.string().optional().catch(undefined),
  worthItFor: z.string().optional().catch(undefined),
  skipIf: z.string().optional().catch(undefined),
  readingMinutes: z.number().int().positive().optional().catch(undefined),
  final: z.boolean().optional().catch(undefined),
}) satisfies z.ZodType<SSESynthesisCompleteEvent, z.ZodTypeDef, unknown>;

export type SynthesisFields = Omit<SSESynthesisCompleteEvent, 'event'>;
