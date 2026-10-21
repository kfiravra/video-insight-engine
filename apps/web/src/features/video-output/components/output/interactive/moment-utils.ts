/** Shared MomentTrack family contract + pure helpers. Leaf module so
 *  MomentGalleryCard/MomentTimelineRow don't import back into MomentTrack
 *  (a require cycle that only worked via function-declaration hoisting). */

export interface MomentItem {
  time: string;
  seconds: number;
  endSeconds?: number;
  label: string;
  description?: string;
  /** The speaker's line that carries the moment, verbatim (output-quality R7/R10). */
  quote?: string;
  /** The quote is the English rendering of non-English speech (2.0 d, D12): prose. */
  quoteTranslated?: boolean;
  /** An interview exchange's whole question — sent only when it differs from the
   *  label (the label is then its first sentence). */
  question?: string;
  mood?: string;
  emoji?: string;
  speaker?: string;
  tags?: string[];
  thumbnailUrl?: string;
  /** One-line vision caption for the frame at this timestamp. */
  frameCaption?: string;
  /** Vision LLM rationale for why this frame is educationally valuable. */
  frameEvidence?: string;
  /** On-screen text (OCR or LLM-read) visible in the frame. */
  frameOcr?: string;
  /** "slide", "code", "diagram", "demo", etc. — internal pipeline signal;
   *  moment views deliberately don't render it. */
  frameSceneType?: string;
  /** Durable S3 key — the API re-signs thumbnailUrl from it on every read. */
  s3Key?: string;
}

export function isClip(item: MomentItem): boolean {
  return typeof item.endSeconds === 'number' && item.endSeconds > item.seconds + 1;
}

export function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const rem = seconds % 60;
  return rem === 0 ? `${minutes}m` : `${minutes}m ${rem}s`;
}

export function resolveLabel(item: MomentItem): string {
  const label = item.label?.trim();
  if (label && label.toLowerCase() !== 'clip' && label.toLowerCase() !== 'moment') return label;
  // No frontend truncation — the backend word-boundary truncates already.
  if (item.description) return item.description;
  return `Moment at ${item.time}`;
}
