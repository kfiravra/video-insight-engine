import type { SynthesisFields } from '../schemas/synthesis-event.schema.js';

/** Filter + `$set` that merge one `synthesis_complete` emission into the cache doc. */
export interface SynthesisMerge {
  /** Extra filter conditions: a partial lands only while no full synthesis is stored. */
  guard: Record<string, unknown>;
  /** Field-level `$set` paths, so an emission never blanks what an earlier one filled. */
  set: Record<string, unknown>;
}

/** Only the fields that carry content — an empty string or array never counts as "set". */
function filledFields(synthesis: SynthesisFields): SynthesisFields {
  const filled: SynthesisFields = {};
  if (synthesis.tldr) filled.tldr = synthesis.tldr;
  if (synthesis.keyTakeaways?.length) filled.keyTakeaways = synthesis.keyTakeaways;
  if (synthesis.masterSummary) filled.masterSummary = synthesis.masterSummary;
  if (synthesis.seoDescription) filled.seoDescription = synthesis.seoDescription;
  return filled;
}

/**
 * Build the update for one emission, or null when it carries nothing (the
 * failure-path all-empty event). The full superset carries a masterSummary and
 * always lands; a partial (the memory-done `{tldr, keyTakeaways}`) is guarded
 * so a replay arriving after the superset can't overwrite it. Re-applying
 * either emission is idempotent.
 */
export function buildSynthesisMerge(synthesis: SynthesisFields): SynthesisMerge | null {
  const filled = Object.entries(filledFields(synthesis));
  if (filled.length === 0) return null;
  const set = Object.fromEntries(filled.map(([key, value]) => [`synthesis.${key}`, value]));
  const guard = synthesis.masterSummary
    ? {}
    : { 'synthesis.masterSummary': { $in: [null, ''] } };
  return { guard, set };
}
