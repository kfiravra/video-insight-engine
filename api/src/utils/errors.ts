export class AppError extends Error {
  constructor(
    public code: string,
    public status: number,
    message?: string
  ) {
    super(message || code);
    this.name = 'AppError';
  }
}

export class ValidationError extends AppError {
  constructor(message: string) {
    super('VALIDATION_ERROR', 400, message);
    this.name = 'ValidationError';
  }
}

export class NotFoundError extends AppError {
  constructor(resource: string) {
    super('NOT_FOUND', 404, `${resource} not found`);
    this.name = 'NotFoundError';
  }
}

export class UnauthorizedError extends AppError {
  constructor(message = 'Unauthorized') {
    super('UNAUTHORIZED', 401, message);
    this.name = 'UnauthorizedError';
  }
}

/** An `/api/admin/*` caller without the permission the route needs (the shared key on a mutation, or a demoted admin). */
export class AdminPermissionError extends AppError {
  constructor(message = 'Admin permission required') {
    super('ADMIN_PERMISSION_DENIED', 403, message);
    this.name = 'AdminPermissionError';
  }
}

/** A mutation's Idempotency-Key was already used for a different request. */
export class IdempotencyKeyReusedError extends AppError {
  constructor() {
    super('IDEMPOTENCY_KEY_REUSED', 422, 'This Idempotency-Key was already used for a different request');
    this.name = 'IdempotencyKeyReusedError';
  }
}

export class ConflictError extends AppError {
  constructor(code: string, message: string) {
    super(code, 409, message);
    this.name = 'ConflictError';
  }
}

/**
 * Refresh-token failure on /api/auth/refresh — missing, expired, malformed,
 * or type-confused cookie. Deliberately one code for every failure mode so
 * the endpoint doesn't oracle which cookies are structurally valid; the FE
 * reaction is identical anyway (session gone → re-login).
 */
export class RefreshExpiredError extends AppError {
  constructor() {
    super('REFRESH_EXPIRED', 401, 'Session expired, please login');
    this.name = 'RefreshExpiredError';
  }
}

// Video-specific errors
export class InvalidYouTubeUrlError extends AppError {
  constructor() {
    super('INVALID_YOUTUBE_URL', 400, 'Invalid YouTube URL');
    this.name = 'InvalidYouTubeUrlError';
  }
}

export class VideoNotFoundError extends AppError {
  constructor() {
    super('VIDEO_NOT_FOUND', 404, 'Video not found');
    this.name = 'VideoNotFoundError';
  }
}

// Auth-specific errors
export class EmailExistsError extends AppError {
  constructor() {
    super('EMAIL_EXISTS', 409, 'Email already exists');
    this.name = 'EmailExistsError';
  }
}

export class InvalidCredentialsError extends AppError {
  constructor() {
    super('INVALID_CREDENTIALS', 401, 'Invalid email or password');
    this.name = 'InvalidCredentialsError';
  }
}

export class RegistrationClosedError extends AppError {
  constructor() {
    super('REGISTRATION_CLOSED', 403, 'Registration is closed');
    this.name = 'RegistrationClosedError';
  }
}

export class DemoDisabledError extends AppError {
  constructor() {
    super('DEMO_DISABLED', 403, 'The demo is not available');
    this.name = 'DemoDisabledError';
  }
}

/** The shared demo account refuses an action that would break it for every visitor. */
export class DemoRestrictedError extends AppError {
  constructor() {
    super('DEMO_RESTRICTED', 403, 'This action is not available on the demo account');
    this.name = 'DemoRestrictedError';
  }
}

/** `cold: true` (benchmark-only cold-media run) from a user who is neither admin nor the eval account. */
export class ColdRunForbiddenError extends AppError {
  constructor() {
    super('COLD_RUN_FORBIDDEN', 403, 'Cold runs are limited to admin and eval accounts');
    this.name = 'ColdRunForbiddenError';
  }
}

export class UserNotFoundError extends AppError {
  constructor() {
    super('USER_NOT_FOUND', 404, 'User not found');
    this.name = 'UserNotFoundError';
  }
}

// Folder-specific errors
export class FolderNotFoundError extends AppError {
  constructor() {
    super('FOLDER_NOT_FOUND', 404, 'Folder not found');
    this.name = 'FolderNotFoundError';
  }
}

export class ParentFolderNotFoundError extends AppError {
  constructor() {
    super('PARENT_FOLDER_NOT_FOUND', 400, 'Parent folder not found');
    this.name = 'ParentFolderNotFoundError';
  }
}

export class FolderMoveError extends AppError {
  constructor(message = 'Cannot move folder') {
    super('FOLDER_MOVE_ERROR', 400, message);
    this.name = 'FolderMoveError';
  }
}

export class VideoSummaryNotFoundError extends AppError {
  constructor() {
    super('VIDEO_SUMMARY_NOT_FOUND', 404, 'Video summary not found or not processed');
    this.name = 'VideoSummaryNotFoundError';
  }
}

export class SectionNotFoundError extends AppError {
  constructor() {
    super('SECTION_NOT_FOUND', 404, 'Section not found');
    this.name = 'SectionNotFoundError';
  }
}

export class ConceptNotFoundError extends AppError {
  constructor() {
    super('CONCEPT_NOT_FOUND', 404, 'Concept not found');
    this.name = 'ConceptNotFoundError';
  }
}

export class ExpansionNotFoundError extends AppError {
  constructor() {
    super('EXPANSION_NOT_FOUND', 404, 'Expansion not found');
    this.name = 'ExpansionNotFoundError';
  }
}

// Database/Transaction errors
export class VersionCreationError extends AppError {
  constructor(message = 'Failed to create new video version') {
    super('VERSION_CREATION_FAILED', 500, message);
    this.name = 'VersionCreationError';
  }
}

/** Generic database / driver-shape error. Use when the driver returns an
 *  unexpected response shape (e.g. `upsert` with no returned document) — not
 *  for normal query failures, which propagate as MongoDB driver errors. */
export class DatabaseError extends AppError {
  constructor(message: string) {
    super('DATABASE_ERROR', 500, message);
    this.name = 'DatabaseError';
  }
}

// Playlist-specific errors
export class InvalidPlaylistUrlError extends AppError {
  constructor() {
    super('INVALID_PLAYLIST_URL', 400, 'Invalid playlist URL');
    this.name = 'InvalidPlaylistUrlError';
  }
}

export class PlaylistNotFoundError extends AppError {
  constructor() {
    super('PLAYLIST_NOT_FOUND', 404, 'Playlist not found or unavailable');
    this.name = 'PlaylistNotFoundError';
  }
}

export class PlaylistExtractionError extends AppError {
  constructor(message = 'Failed to extract playlist data') {
    super('PLAYLIST_EXTRACTION_FAILED', 502, message);
    this.name = 'PlaylistExtractionError';
  }
}

export class PlaylistTooLargeError extends AppError {
  constructor(max: number) {
    super('PLAYLIST_TOO_LARGE', 400, `Playlist exceeds maximum of ${max} videos`);
    this.name = 'PlaylistTooLargeError';
  }
}

export class UrlModeMismatchError extends AppError {
  constructor(message: string, suggestion?: string) {
    super('URL_MODE_MISMATCH', 400, suggestion ? `${message}. ${suggestion}` : message);
    this.name = 'UrlModeMismatchError';
  }
}

// Share-specific errors
export class ShareNotFoundError extends AppError {
  constructor() {
    super('SHARE_NOT_FOUND', 404, 'Shared summary not found');
    this.name = 'ShareNotFoundError';
  }
}

export class AlreadySharedError extends AppError {
  constructor(slug: string) {
    super('ALREADY_SHARED', 409, `Video is already shared at /s/${slug}`);
    this.name = 'AlreadySharedError';
  }
}

// Override-specific errors
export class InvalidCategoryError extends AppError {
  constructor(category: string) {
    super('INVALID_CATEGORY', 400, `Invalid category: ${category}`);
    this.name = 'InvalidCategoryError';
  }
}

// Payment errors. 501, not 503: no provider is built or configured, which a
// retry never fixes, and clients can tell "not offered" from "down right now".
export class ProviderNotConfiguredError extends AppError {
  constructor() {
    super('PROVIDER_NOT_CONFIGURED', 501, 'Payments are not available yet.');
    this.name = 'ProviderNotConfiguredError';
  }
}

/**
 * A throttle answered 429: the assistant's own 30/min chat window, or the
 * shared submit burst limit. Same wire shape as the rate-limit plugin's
 * envelope — `RATE_LIMITED`, 429 — so clients keep one code map for all of
 * them; the message says which limit was hit and `retryAfterSec` (when the
 * limiter knows it) is what clients localize their own copy from.
 */
export class RateLimitedError extends AppError {
  /** Whole seconds until the limiter lets the caller back in; undefined when it cannot tell. */
  public readonly retryAfterSec?: number;

  constructor(message = 'Too many messages. Please wait a moment and try again.', retryAfterSec?: number) {
    super('RATE_LIMITED', 429, message);
    this.name = 'RateLimitedError';
    this.retryAfterSec = retryAfterSec;
  }
}

/**
 * A free account used its chat messages for the rolling 24 hours (Q7). Not
 * RATE_LIMITED: clients tell "slow down" from "you are out until `nextAt`".
 */
export class ChatLimitReachedError extends AppError {
  /** ISO timestamp of the moment the oldest counted message leaves the window. */
  public readonly nextAt: string;

  constructor(messagesPerWindow: number, nextAt: Date) {
    super(
      'CHAT_LIMIT_REACHED',
      429,
      `Free accounts can send ${messagesPerWindow} chat messages in 24 hours. More open up as the last 24 hours pass.`,
    );
    this.name = 'ChatLimitReachedError';
    this.nextAt = nextAt.toISOString();
  }
}

// External service errors
export class ServiceTimeoutError extends AppError {
  constructor(service: string) {
    super('SERVICE_TIMEOUT', 504, `${service} service timed out`);
    this.name = 'ServiceTimeoutError';
  }
}

export class ServiceUnavailableError extends AppError {
  constructor(service: string) {
    super('SERVICE_UNAVAILABLE', 503, `${service} service unavailable`);
    this.name = 'ServiceUnavailableError';
  }
}

// Queue errors — surfaced as 503 so the client retries; the DB row is already
// created so a retry is idempotent.
export class QueuePublishError extends AppError {
  constructor(message: string) {
    super('QUEUE_PUBLISH_FAILED', 503, `Failed to enqueue job: ${message}`);
    this.name = 'QueuePublishError';
  }
}

// GDPR / account-deletion errors
export class AccountDeletionPendingError extends AppError {
  constructor() {
    super(
      'ACCOUNT_DELETION_PENDING',
      403,
      'This account is scheduled for deletion. Contact support to cancel.',
    );
    this.name = 'AccountDeletionPendingError';
  }
}

export class AccountAlreadyDeletedError extends AppError {
  constructor() {
    super('ACCOUNT_ALREADY_DELETED', 409, 'Account is already scheduled for deletion');
    this.name = 'AccountAlreadyDeletedError';
  }
}

/**
 * The summarizer could not purge a video's artifacts (Qdrant, S3, Redis).
 * The Mongo half of the cascade is skipped so the admin can simply retry.
 */
export class SummarizerPurgeError extends AppError {
  constructor(detail: string) {
    super('SUMMARIZER_PURGE_FAILED', 502, `Video purge failed: ${detail}`);
    this.name = 'SummarizerPurgeError';
  }
}

/**
 * Why the pre-flight metadata call failed. `not_found` (unknown or private
 * video) and `unsupported` (a live stream, or a premiere with no duration yet)
 * will not heal on an immediate retry; `timeout` and `upstream` may.
 */
export type MetadataUnavailableReason = 'not_found' | 'timeout' | 'upstream' | 'unsupported';

const METADATA_UNAVAILABLE_MESSAGES: Record<MetadataUnavailableReason, string> = {
  not_found: "We couldn't find this video. Check the link and that the video is public.",
  timeout: 'Looking up the video took too long. Please try again.',
  upstream: 'Video details are temporarily unavailable. Please try again.',
  unsupported:
    "Live streams and premieres can't be processed yet. Try again once the video is a regular upload.",
};

const METADATA_UNAVAILABLE_STATUS: Record<MetadataUnavailableReason, number> = {
  not_found: 404,
  timeout: 503,
  upstream: 503,
  unsupported: 422,
};

/**
 * The summarizer could not give the duration/title of a video before submit
 * (unknown video, YouTube rate limit, summarizer down, slow, a live stream).
 * A billing quote built on it fails closed (no run without a length);
 * `reason` says whether retrying can help.
 */
export class MetadataUnavailableError extends AppError {
  public readonly reason: MetadataUnavailableReason;

  constructor(reason: MetadataUnavailableReason) {
    super('METADATA_UNAVAILABLE', METADATA_UNAVAILABLE_STATUS[reason], METADATA_UNAVAILABLE_MESSAGES[reason]);
    this.name = 'MetadataUnavailableError';
    this.reason = reason;
  }
}

// ─── Minutes gate (billing-roles) ───────────────────────────────────────
// Thrown by the minutes ledger on every submit; error-envelope.ts
// carries the numbers (`needed`, `available`, `nextFreeAt`, `maxMinutes`,
// `maxConcurrent`) as top-level body fields.

/** The account cannot pay for the video: `needed` minutes, `available` paid minutes. */
export class InsufficientMinutesError extends AppError {
  constructor(
    public readonly needed: number,
    public readonly available: number,
    public readonly nextFreeAt: string | null,
  ) {
    super('INSUFFICIENT_MINUTES', 402, `This video needs ${needed} minutes; ${available} available`);
    this.name = 'InsufficientMinutesError';
  }
}

/** Longer than any minutes on this account can pay for (paid 180, free 30, or an admin cap). */
export class VideoTooLongForPlanError extends AppError {
  constructor(public readonly maxMinutes: number) {
    super('VIDEO_TOO_LONG_FOR_PLAN', 422, `Videos up to ${maxMinutes} minutes are supported on this account`);
    this.name = 'VideoTooLongForPlanError';
  }
}

export class AccountBlockedError extends AppError {
  constructor() {
    super('ACCOUNT_BLOCKED', 403, 'This account cannot start new videos');
    this.name = 'AccountBlockedError';
  }
}

/** `caps.maxConcurrent` runs are already in flight. */
export class TooManyRunsError extends AppError {
  constructor(public readonly maxConcurrent: number) {
    super('TOO_MANY_RUNS', 429, `At most ${maxConcurrent} videos can run at once on this account`);
    this.name = 'TooManyRunsError';
  }
}

/** A hold was requested for a requestId whose run was already settled or released. */
export class HoldFinalizedError extends ConflictError {
  constructor() {
    super('HOLD_FINALIZED', 'This run has already been settled or released');
    this.name = 'HoldFinalizedError';
  }
}

/** A hold already exists under this run id for another user or video (W3) — never ride on someone else's hold. */
export class HoldConflictError extends ConflictError {
  constructor() {
    super('HOLD_CONFLICT', 'This run id is already holding minutes for another video');
    this.name = 'HoldConflictError';
  }
}

/**
 * createVideo with `serveOnly` (the ledger refused to pay) hit a branch that
 * would start a run. The submission catches it and throws the ledger's own
 * refusal instead; it should never reach a client.
 */
export class RunNotAllowedError extends AppError {
  constructor() {
    super('RUN_NOT_ALLOWED', 409, 'This video would need a new run');
    this.name = 'RunNotAllowedError';
  }
}
