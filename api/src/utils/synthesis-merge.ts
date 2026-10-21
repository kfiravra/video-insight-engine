import { filledSynthesisFields } from '@vie/types';
import type { SynthesisFields } from '../schemas/synthesis-event.schema.js';

/** Filter + `$set` that merge one `synthesis_complete` emission into the cache doc. */
export interface SynthesisMerge {
  /** Extra filter conditions: a partial lands only while no full synthesis is stored. */
  guard: Record<string, unknown>;
  /** Field-level `$set` paths, so an emission never blanks what an earlier one filled. */
  set: Record<string, unknown>;
}

/**
 * Build the update for one emission, or null when it carries nothing (the
 * failure-path all-empty event). A superset carries a masterSummary and always
 * lands — the synthesis one, the hero rewrite's (its brief, plus `brief`,
 * `worthItFor`, `skipIf` and, on the corrected one after the late group,
 * `readingMinutes`); a partial (the memory-done `{tldr, keyTakeaways}`) is
 * guarded so a replay arriving after a superset can't overwrite it.
 * Re-applying any emission is idempotent.
 */
export function buildSynthesisMerge(synthesis: SynthesisFields): SynthesisMerge | null {
  const filled = Object.entries(filledSynthesisFields(synthesis));
  if (filled.length === 0) return null;
  const set = Object.fromEntries(filled.map(([key, value]) => [`synthesis.${key}`, value]));
  const guard = synthesis.masterSummary
    ? {}
    : { 'synthesis.masterSummary': { $in: [null, ''] } };
  return { guard, set };
}
