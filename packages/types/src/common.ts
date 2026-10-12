// ═══════════════════════════════════════════════════
// Common Types — Status, Transcript, Generation
// ═══════════════════════════════════════════════════

export type ProcessingStatus =
  | 'pending'
  | 'processing'
  | 'completed'
  | 'failed';

export type TranscriptSource = 'ytdlp' | 'api' | 'proxy' | 'whisper' | 'gemini' | 'metadata';

export interface TranscriptSegment {
  text: string;
  startMs: number;
  endMs: number;
}

/**
 * The yt-dlp caption track a "ytdlp" transcript blob holds (caption key + bucket).
 * Mirrors `CaptionRef` in services/summarizer/src/services/transcription/transcript_store.py.
 */
export interface CaptionRef {
  lang: string;                // caption key as yt-dlp lists it, e.g. "iw", "en-US", "en-orig"
  kind: 'manual' | 'auto-generated';
}

/**
 * Full transcript stored in S3 for regeneration without re-fetching from YouTube.
 * S3 key format: `videos/{youtubeId}/transcript.json`
 */
export interface RawTranscript {
  youtubeId: string;
  fetchedAt: string;           // ISO date
  source: TranscriptSource;
  language: string | null;
  segments: TranscriptSegment[];
  caption?: CaptionRef | null; // absent on blobs stored before 2.4
}

/**
 * Generation metadata for tracking prompt versions and enabling regeneration.
 * Stored with each summary to know what model/prompt produced it.
 */
export interface GenerationMetadata {
  model: string;               // e.g., "anthropic/claude-sonnet-4-6"
  promptVersion: string;       // e.g., "v2.3"
  generatedAt: string;         // ISO date
}
