/**
 * SSE Event Validators - Runtime validation for streaming events.
 *
 * Issue #11: Validates SSE event data before type casting to prevent
 * runtime errors from malformed server responses.
 */

import { z } from 'zod';
import { sseLogger } from './sse-logger';
import { incrementTelemetryCounter } from '@/features/video-output/lib/telemetry';
import {
  VIDEO_CATEGORY_VALUES,
  filledSynthesisFields,
  type SSESynthesisCompleteEvent,
  type SynthesisResult,
  type DescriptionLink,
  type Resource,
  type RelatedVideo,
  type SocialLink,
  type VideoContext,
} from '@vie/types';

// ─────────────────────────────────────────────────────
// Zod Schemas
// ─────────────────────────────────────────────────────

export const descriptionLinkSchema = z.object({
  url: z.string(),
  type: z.string(),
  label: z.string(),
});

export const resourceSchema = z.object({
  name: z.string(),
  url: z.string(),
});

export const relatedVideoSchema = z.object({
  title: z.string(),
  url: z.string(),
});

export const socialLinkSchema = z.object({
  platform: z.string(),
  url: z.string(),
});

// ─────────────────────────────────────────────────────
// Validation Functions
// ─────────────────────────────────────────────────────

/** Log an invalid event AND bump the prod-visible drift counter — the log is
 *  dev-oriented, the counter is what makes envelope drift observable in prod. */
function reportInvalidEvent(label: string, message: string): void {
  incrementTelemetryCounter('sse_event_invalid');
  sseLogger.warn(label, message);
}

/**
 * Validate description analysis from SSE event.
 */
export function validateDescriptionAnalysis(data: unknown): {
  links: DescriptionLink[];
  resources: Resource[];
  relatedVideos: RelatedVideo[];
  socialLinks: SocialLink[];
} | null {
  const schema = z.object({
    links: z.array(descriptionLinkSchema).default([]),
    resources: z.array(resourceSchema).default([]),
    relatedVideos: z.array(relatedVideoSchema).default([]),
    socialLinks: z.array(socialLinkSchema).default([]),
  });

  const result = schema.safeParse(data);
  if (!result.success) {
    reportInvalidEvent('Invalid description analysis:', result.error.message);
    return null;
  }
  return result.data as {
    links: DescriptionLink[];
    resources: Resource[];
    relatedVideos: RelatedVideo[];
    socialLinks: SocialLink[];
  };
}

// ─────────────────────────────────────────────────────
// Event-Level Validators (for full SSE event objects)
// ─────────────────────────────────────────────────────

// Video context schema for category-based rendering
export const videoContextSchema = z.object({
  category: z.enum(VIDEO_CATEGORY_VALUES),
  youtubeCategory: z.string(),
  tags: z.array(z.string()),
  displayTags: z.array(z.string()),
});

interface VideoMetadata {
  title?: string;
  channel?: string;
  thumbnailUrl?: string;
  duration?: number;
  context?: VideoContext;
}

const metadataEventSchema = z.object({
  event: z.literal('metadata'),
  title: z.string().optional(),
  channel: z.string().optional(),
  thumbnailUrl: z.string().optional(),
  duration: z.number().optional(),
  context: videoContextSchema.optional(),
});

/**
 * Validate metadata event from SSE.
 * Returns validated metadata or default values if validation fails.
 */
export function validateMetadataEvent(data: unknown): VideoMetadata {
  const result = metadataEventSchema.safeParse(data);
  if (!result.success) {
    reportInvalidEvent('Invalid metadata event:', result.error.message);
    return {};
  }
  return {
    title: result.data.title,
    channel: result.data.channel,
    thumbnailUrl: result.data.thumbnailUrl,
    duration: result.data.duration,
    context: result.data.context as VideoContext | undefined,
  };
}

// Every content field is optional: the memory-done partial carries only
// tldr + keyTakeaways, a failure path can carry nothing. `satisfies` ties the
// schema to the shared event type so the two can't drift apart.
const synthesisCompleteEventSchema = z.object({
  event: z.literal('synthesis_complete'),
  tldr: z.string().optional(),
  keyTakeaways: z.array(z.string()).optional(),
  masterSummary: z.string().optional(),
  seoDescription: z.string().optional(),
}) satisfies z.ZodType<SSESynthesisCompleteEvent>;

/**
 * Validate synthesis_complete event from SSE.
 * Returns only the fields that carry content ({} if validation fails), ready
 * to merge over what earlier emissions filled.
 */
export function validateSynthesisComplete(data: unknown): Partial<SynthesisResult> {
  const result = synthesisCompleteEventSchema.safeParse(data);
  if (!result.success) {
    reportInvalidEvent('Invalid synthesis_complete event:', result.error.message);
    return {};
  }
  return filledSynthesisFields(result.data);
}

const doneEventSchema = z.object({
  event: z.literal('done'),
  processingTimeMs: z.number().nullable().optional(),
  // Partial-result flag from the summarizer terminal event (dropped
  // extraction batches / critical coverage). Optional: legacy/cached done
  // events don't carry it.
  degraded: z.boolean().optional(),
});

interface DoneEventResult {
  processingTimeMs: number | null;
  degraded: boolean;
}

/**
 * Validate done event from SSE.
 * Returns processing time (null when absent) and the degraded flag
 * (false when absent or when validation fails).
 */
export function validateDoneEvent(data: unknown): DoneEventResult {
  const result = doneEventSchema.safeParse(data);
  if (!result.success) {
    reportInvalidEvent('Invalid done event:', result.error.message);
    return { processingTimeMs: null, degraded: false };
  }
  return {
    processingTimeMs: result.data.processingTimeMs ?? null,
    degraded: result.data.degraded ?? false,
  };
}

const errorEventSchema = z.object({
  event: z.literal('error'),
  message: z.string().default('Unknown error'),
  code: z.string().optional(),
});

interface ErrorEventResult {
  message: string;
  code?: string;
}

/**
 * Validate error event from SSE.
 * Returns error details with defaults if validation fails.
 */
export function validateErrorEvent(data: unknown): ErrorEventResult {
  const result = errorEventSchema.safeParse(data);
  if (!result.success) {
    reportInvalidEvent('Invalid error event:', result.error.message);
    return { message: 'Unknown error' };
  }
  return {
    message: result.data.message,
    code: result.data.code,
  };
}

// ─────────────────────────────────────────────────────
// Phase Event Validation
// ─────────────────────────────────────────────────────

/**
 * Valid stream phases for SSE streaming.
 * Must cover every value the summarizer actually emits — see
 * services/summarizer/src/services/transcription/transcript_fetcher.py and
 * services/pipeline/phases/translation.py. Rejected values are silently
 * dropped (plus a telemetry bump), which freezes the UI on a stale phase.
 */
export const VALID_SSE_PHASES = [
  'metadata',
  'metadata_fallback',
  'transcript',
  'transcript_cached',
  'audio_transcription',
  'whisper_transcription',
  'triage',
  'extraction',
  'enrichment',
  'synthesis',
  'translation',
] as const;

export type SSEPhase = typeof VALID_SSE_PHASES[number];

const phaseEventSchema = z.object({
  event: z.literal('phase'),
  phase: z.enum(VALID_SSE_PHASES),
});

/**
 * Validate a phase event from SSE stream.
 * Returns the validated phase or null if invalid.
 */
export function validatePhaseEvent(data: unknown): SSEPhase | null {
  const result = phaseEventSchema.safeParse(data);
  if (!result.success) {
    reportInvalidEvent('Invalid phase event:', result.error.message);
    return null;
  }
  return result.data.phase;
}
