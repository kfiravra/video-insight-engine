import { SYNTHESIS_HERO_KEYS, carriedSynthesisFields, filledSynthesisFields } from '@vie/types';
import type { SynthesisFields } from '../schemas/synthesis-event.schema.js';

/** Filter + `$set` / `$unset` that merge one `synthesis_complete` emission into the cache doc. */
export interface SynthesisMerge {
  /** Extra filter conditions: a partial lands only while no full synthesis is stored. */
  guard: Record<string, unknown>;
  /** Field-level `$set` paths, so an emission never blanks what an earlier one filled. */
  set: Record<string, unknown>;
  /** Field-level `$unset` paths — only the final emission clears a hero field it no longer carries. */
  unset: Record<string, ''>;
}

function synthesisPaths(fields: object): Record<string, unknown> {
  return Object.fromEntries(Object.entries(fields).map(([key, value]) => [`synthesis.${key}`, value]));
}

/**
 * The corrected superset after the late group replaces the stored copy: every
 * field it carries is set, empties included (the scrub emptied it), and every
 * hero field it does not carry is unset. It lands unguarded — even when its
 * masterSummary scrubbed to "".
 */
function buildFinalMerge(synthesis: SynthesisFields): SynthesisMerge {
  const absent = SYNTHESIS_HERO_KEYS.filter((key) => synthesis[key] === undefined);
  return {
    guard: {},
    set: synthesisPaths(carriedSynthesisFields(synthesis)),
    unset: Object.fromEntries(absent.map((key) => [`synthesis.${key}`, ''])),
  };
}

/**
 * Build the update for one emission, or null when it carries nothing (the
 * failure-path all-empty event). The `final` one replaces (buildFinalMerge).
 * Any other superset carries a masterSummary and always lands — the synthesis
 * one, the hero rewrite's (its brief, plus `brief`, `worthItFor`, `skipIf`); a
 * partial (the memory-done `{tldr, keyTakeaways}`) is guarded so a replay
 * arriving after a superset can't overwrite it. Re-applying any emission is
 * idempotent.
 */
export function buildSynthesisMerge(synthesis: SynthesisFields): SynthesisMerge | null {
  if (synthesis.final) return buildFinalMerge(synthesis);
  const filled = filledSynthesisFields(synthesis);
  if (Object.keys(filled).length === 0) return null;
  const guard = synthesis.masterSummary
    ? {}
    : { 'synthesis.masterSummary': { $in: [null, ''] } };
  return { guard, set: synthesisPaths(filled), unset: {} };
}
