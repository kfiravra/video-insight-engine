/**
 * User-friendly error message mapping for SSE streaming.
 * Maps error codes and raw messages to text users can understand.
 */

// Duration limits are env-configured on the summarizer, so the duration copy
// states no number — a hard-coded one goes stale the moment the config changes.
const CODE_MESSAGES: Record<string, string> = {
  NO_TRANSCRIPT: "This video doesn't have captions available. Videos need captions or subtitles to be summarized.",
  VIDEO_TOO_LONG: "This video is longer than the maximum length we can summarize.",
  VIDEO_TOO_SHORT: "This video is shorter than the minimum length we can summarize.",
  VIDEO_UNAVAILABLE: "This video is unavailable. It may be private, deleted, or region-restricted.",
  VIDEO_RESTRICTED: "This video has restrictions that prevent it from being processed.",
  LIVE_STREAM: "Live streams cannot be summarized. Please wait until the stream ends.",
  // The gateway answers the stream of a failed video with this and runs nothing; the card
  // offers Retry, which starts a new, billed run (the stored raw message is never shown).
  VIDEO_FAILED: "This video didn't finish processing. Retry starts it again as a new run.",
  LLM_ERROR: "Our AI service encountered an issue. Please try again in a few moments.",
  RATE_LIMITED: "Too many requests. Please wait a moment and try again.",
  UNKNOWN_ERROR: "Something went wrong while processing this video. Please try again.",
};

const PATTERNS: Array<[RegExp, string]> = [
  [/timeout|timed out/i, "The request took too long. This video might be too complex. Please try again."],
  [/rate limit|429/i, "Too many requests. Please wait a moment and try again."],
  [/connection|network|fetch/i, "Connection issue. Please check your internet and try again."],
  [/transcript.*not available|no captions/i, "This video doesn't have captions available."],
  [/video.*unavailable|private video/i, "This video is unavailable or private."],
  [/authentication|unauthorized|401/i, "Session expired. Please refresh the page."],
  [/server error|500|502|503/i, "Our servers are having issues. Please try again later."],
];

const DEFAULT_ERROR = "Something went wrong while processing this video. Please try again.";

export function getUserFriendlyError(message: string, code?: string): string {
  if (code && CODE_MESSAGES[code]) {
    return CODE_MESSAGES[code];
  }

  for (const [pattern, friendlyMessage] of PATTERNS) {
    if (pattern.test(message)) {
      return friendlyMessage;
    }
  }

  return DEFAULT_ERROR;
}
