// ═══════════════════════════════════════════════════
// Output Types — Triage Pipeline
// ═══════════════════════════════════════════════════

import type { ContentTag, ScenarioItem, TriageResult, VIEResponseMeta, TabEntry } from './vie-response.js';
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
  keyTakeaways: string[];
  masterSummary: string;
  seoDescription: string;
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
  return filled;
}

// ─────────────────────────────────────────────────────
// Enrichment Data
// ─────────────────────────────────────────────────────

// Import shared primitives from vie-response (canonical location)
import type { QuizQuestion, Flashcard } from './vie-response.js';
export type { QuizQuestion, Flashcard };

export interface CodeCheatSheetItem {
  title: string;
  code: string;
  description: string;
}

export interface EnrichmentData {
  quiz?: QuizQuestion[];
  flashcards?: Flashcard[];
  cheatSheet?: CodeCheatSheetItem[];
  scenarios?: ScenarioItem[];
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
