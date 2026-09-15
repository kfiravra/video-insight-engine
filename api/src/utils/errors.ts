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

export class ShareNotAllowedError extends AppError {
  constructor() {
    super('SHARE_NOT_ALLOWED', 403, 'Sharing is not available on your current tier');
    this.name = 'ShareNotAllowedError';
  }
}

// Override-specific errors
export class InvalidCategoryError extends AppError {
  constructor(category: string) {
    super('INVALID_CATEGORY', 400, `Invalid category: ${category}`);
    this.name = 'InvalidCategoryError';
  }
}

// Payment-specific errors
export class PaymentError extends AppError {
  constructor(message = 'Payment processing failed') {
    super('PAYMENT_ERROR', 500, message);
    this.name = 'PaymentError';
  }
}

export class InvalidWebhookError extends AppError {
  constructor(message = 'Invalid webhook signature') {
    super('INVALID_WEBHOOK', 400, message);
    this.name = 'InvalidWebhookError';
  }
}

// Tier-specific errors
export class TierLimitExceededError extends AppError {
  constructor(resource: string, limit: number) {
    super('TIER_LIMIT_EXCEEDED', 403, `${resource} limit reached (${limit}). Upgrade your plan for more.`);
    this.name = 'TierLimitExceededError';
  }
}

// Cost-specific errors
export class CostLimitExceededError extends AppError {
  constructor() {
    super('COST_LIMIT_EXCEEDED', 503, 'Daily LLM cost limit exceeded. Service temporarily unavailable.');
    this.name = 'CostLimitExceededError';
  }
}

export class DailyLimitReachedError extends AppError {
  /** ISO timestamp of next UTC midnight reset. */
  public readonly resetAt: string;
  /** Daily limit for the user's tier in USD. */
  public readonly limitUsd: number;

  constructor(limitUsd: number, resetAt: string) {
    super(
      'DAILY_LIMIT_REACHED',
      429,
      `You've reached your daily cost limit ($${limitUsd.toFixed(2)}). Resets at midnight UTC.`,
    );
    this.name = 'DailyLimitReachedError';
    this.resetAt = resetAt;
    this.limitUsd = limitUsd;
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
