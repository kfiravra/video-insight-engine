// ═══════════════════════════════════════════════════
// API Response & Error Types
// ═══════════════════════════════════════════════════

import type { User } from './user.js';
import type { ProcessingStatus } from './common.js';
import type { TabEntry } from './vie-response.js';

// ─────────────────────────────────────────────────────
// API Responses
// ─────────────────────────────────────────────────────

export interface AuthResponse {
  accessToken: string;
  expiresIn: number;
  user: Pick<User, 'email' | 'name'> & { id: string };
}

export interface FolderResponse {
  id: string;
  name: string;
  parentId: string | null;
  path: string;
  level: number;
  color: string | null;
  icon: string | null;
  order: number;
  createdAt: string;
  updatedAt: string;
}

// ─────────────────────────────────────────────────────
// WebSocket Event Types
// ─────────────────────────────────────────────────────

export interface VideoStatusEvent {
  type: 'video.status';
  payload: {
    videoSummaryId: string;
    userVideoId: string;
    youtubeId: string;
    status: ProcessingStatus;
    progress: number;
    message?: string;
    error?: string;
  };
}

export interface VideoMetadataEvent {
  type: 'video.metadata';
  payload: {
    videoSummaryId: string;
    title: string;
    channel?: string;
    thumbnailUrl?: string;
    duration?: number;
  };
}

export interface ExpansionStatusEvent {
  type: 'expansion.status';
  payload: {
    videoSummaryId: string;
    targetType: 'section' | 'concept';
    targetId: string;
    status: ProcessingStatus;
    error?: string;
  };
}

export type WebSocketEvent = VideoStatusEvent | VideoMetadataEvent | ExpansionStatusEvent;

// ─────────────────────────────────────────────────────
// SSE Event Types
// ─────────────────────────────────────────────────────

export interface SSEDoneEvent {
  event: 'done';
  videoSummaryId: string;
  processingTimeMs?: number;
  cached?: boolean;
}

export interface SSEPhaseEvent {
  event: 'phase';
  phase: string;
}

export interface SSETokenEvent {
  event: 'token';
  phase: string;
  token: string;
}

export interface SSEErrorEvent {
  event: 'error';
  message: string;
  code?: string;
}

// v2: Assembly pipeline SSE events

export interface SSEMetaEvent {
  event: 'meta';
  videoId?: string;
  title: string;
  creator?: string;
  userGoal?: string;
  contentTags: string[];
  modifiers: string[];
  primaryTag: string;
  stats?: { label: string; value: string; emoji?: string }[];
  tabCount: number;
  tabLabels: { id: string; label: string; emoji: string }[];
}

/** One tab, streamed as it becomes available (emission contract: pipeline-1min
 *  2.1, docs/API-REFERENCE.md#tab_ready). A tab id can arrive more than once;
 *  the later emission replaces the earlier one by id. */
export interface SSETabReadyEvent extends TabEntry {
  event: 'tab_ready';
  /** The tab's index in the response's tab order — a sort key, never a splice
   *  index. Exact on a `final` emission; an estimate before it (a provisional
   *  or held-back tab), so two tabs can share a value until the final one. */
  position?: number;
  /** This is the tab's last emission of the run: content and position are
   *  final. Every tab of the final response gets one, before `complete`; a
   *  cached replay sends every tab final. Should a tab still change, a
   *  corrected final follows for the same id — the last final wins. Absent =
   *  may change. */
  final?: boolean;
  /** A placeholder built before extraction — the plan-done Moments tab (memory
   *  outline → YouTube chapters → description timestamps). Never final; the
   *  real tab with the same id replaces it, or the tab whose `replaces` names
   *  it. */
  provisional?: boolean;
  /** Set on the first emission of the tab that takes the provisional tab's
   *  place under ANOTHER id (e.g. a required backfill): the provisional id.
   *  Drop that provisional tab and give this one its slot and focus. A tab
   *  with the provisional's own id never carries it; `complete.tabIds` still
   *  withdraws the provisional id. */
  replaces?: string;
}

export interface SSECompleteEvent {
  event: 'complete';
  tabCount: number;
  processingTimeMs: number;
  degraded?: boolean;
  /** The final response's tab ids in order (pipeline runs). A tab streamed
   *  earlier whose id is missing here was withdrawn — drop it. */
  tabIds?: string[];
  cached?: boolean;
}

/** Emitted up to twice per run: early at memory-done with only `tldr` +
 *  `keyTakeaways` (drives the hero), then the full superset at
 *  synthesis-done. Either can be missing on a failure path and a reconnect
 *  can replay them in any order, so every content field is optional and
 *  consumers merge emissions — a partial never overwrites the superset. */
export interface SSESynthesisCompleteEvent {
  event: 'synthesis_complete';
  tldr?: string;
  keyTakeaways?: string[];
  masterSummary?: string;
  seoDescription?: string;
}

export interface SSEFramesEvent {
  event: 'frames';
  videoId: string;
  frames: { url: string; timestamp?: number; index: number }[];
}

// ─────────────────────────────────────────────────────
// Error Types
// ─────────────────────────────────────────────────────

export interface ApiError {
  error: string;
  message: string;
  statusCode: number;
}

export const ErrorCodes = {
  // Auth
  INVALID_CREDENTIALS: 'INVALID_CREDENTIALS',
  EMAIL_EXISTS: 'EMAIL_EXISTS',
  REFRESH_EXPIRED: 'REFRESH_EXPIRED',

  // Video
  INVALID_YOUTUBE_URL: 'INVALID_YOUTUBE_URL',
  VIDEO_NOT_FOUND: 'VIDEO_NOT_FOUND',
  NO_TRANSCRIPT: 'NO_TRANSCRIPT',
  VIDEO_TOO_LONG: 'VIDEO_TOO_LONG',
  VIDEO_TOO_SHORT: 'VIDEO_TOO_SHORT',
  VIDEO_UNAVAILABLE: 'VIDEO_UNAVAILABLE',

  // Playlist
  INVALID_PLAYLIST_URL: 'INVALID_PLAYLIST_URL',
  PLAYLIST_NOT_FOUND: 'PLAYLIST_NOT_FOUND',
  PLAYLIST_EXTRACTION_FAILED: 'PLAYLIST_EXTRACTION_FAILED',
  PLAYLIST_TOO_LARGE: 'PLAYLIST_TOO_LARGE',
  URL_MODE_MISMATCH: 'URL_MODE_MISMATCH',

  // Share
  SHARE_NOT_FOUND: 'SHARE_NOT_FOUND',
  SHARE_ALREADY_EXISTS: 'SHARE_ALREADY_EXISTS',

  // Tier & Payment
  TIER_LIMIT_EXCEEDED: 'TIER_LIMIT_EXCEEDED',
  PAYMENT_FAILED: 'PAYMENT_FAILED',
  PAYMENT_REQUIRED: 'PAYMENT_REQUIRED',

  // General
  NOT_FOUND: 'NOT_FOUND',
  UNAUTHORIZED: 'UNAUTHORIZED',
  RATE_LIMITED: 'RATE_LIMITED',
  INTERNAL_ERROR: 'INTERNAL_ERROR',
} as const;
