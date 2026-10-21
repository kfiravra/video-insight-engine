// ═══════════════════════════════════════════════════
// Output Types — Triage Pipeline
// ═══════════════════════════════════════════════════

import type { ContentTag, TriageResult, VIEResponseMeta, TabEntry } from './vie-response.js';
import { CONTENT_TAG_VALUES } from '@vie/shared/config';

// OutputType is an alias for ContentTag — kept for backward compatibility.
export type OutputType = ContentTag;

export const OUTPUT_TYPE_VALUES: readonly OutputType[] = CONTENT_TAG_VALUES;

export function isValidOutputType(value: string): value is OutputType {
  return CONTENT_TAG_VALUES.includes(value as OutputType);
}

// ─────────────────────────────────────────────────────
// Synthesis
// ─────────────────────────────────────────────────────

export interface SynthesisResult {
  tldr: string;
  /** Each a claim closing with the `[m:ss]` (`[h:mm:ss]`) where it is said, when known. */
  keyTakeaways: string[];
  masterSummary: string;
  seoDescription: string;
  /** The hero rewrite's own fields (output-quality 2.2) — absent on the synthesis
   *  path and on older rows. `brief` is also sent as `masterSummary`. */
  brief?: string;
  /** Who gets what from watching (≤ 20 words). */
  worthItFor?: string;
  /** What someone might come for that the video does not contain (≤ 20 words). */
  skipIf?: string;
  /** Minutes to read the shipped page, counted by code once the tabs are final. */
  readingMinutes?: number;
}

/**
 * The synthesis fields that carry content — an empty string or array never
 * counts as "set", so a failure-path `synthesis_complete` emission can't blank
 * what an earlier one filled. One rule for both consumers that merge the
 * emissions: the web stream state and the API relay's persistence.
 */
export function filledSynthesisFields(synthesis: Partial<SynthesisResult>): Partial<SynthesisResult> {
  const filled: Partial<SynthesisResult> = {};
  if (synthesis.tldr) filled.tldr = synthesis.tldr;
  if (synthesis.keyTakeaways?.length) filled.keyTakeaways = synthesis.keyTakeaways;
  if (synthesis.masterSummary) filled.masterSummary = synthesis.masterSummary;
  if (synthesis.seoDescription) filled.seoDescription = synthesis.seoDescription;
  if (synthesis.brief) filled.brief = synthesis.brief;
  if (synthesis.worthItFor) filled.worthItFor = synthesis.worthItFor;
  if (synthesis.skipIf) filled.skipIf = synthesis.skipIf;
  if (synthesis.readingMinutes) filled.readingMinutes = synthesis.readingMinutes;
  return filled;
}

// ─────────────────────────────────────────────────────
// Enrichment Data
// ─────────────────────────────────────────────────────

// Import shared primitives from vie-response (canonical location)
import type { QuizQuestion, Flashcard } from './vie-response.js';
export type { QuizQuestion, Flashcard };

// The summarizer's enrichment_complete carries the quiz only (pipeline-1min 1d.1).
export interface EnrichmentData {
  quiz?: QuizQuestion[];
}

// ─────────────────────────────────────────────────────
// Video Output (triage-based)
// ─────────────────────────────────────────────────────

export interface VideoOutput {
  triage: TriageResult;
  output: Record<string, unknown> | null;
  synthesis: SynthesisResult | null;
  enrichment: EnrichmentData | null;
  // v2: Assembled output (component-addressed tabs)
  assembledMeta?: VIEResponseMeta | null;
  assembledTabs?: TabEntry[] | null;
}
