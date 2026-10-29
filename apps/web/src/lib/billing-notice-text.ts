import { fillTemplate } from "@/lib/format-minutes";
import type { BillingText } from "@/hooks/use-billing-text";
import { classifyBillingCode, classifyBillingError, type BillingNotice } from "@/lib/billing-errors";
import type { FailedVideoImport, PlaylistQuote } from "@/api/playlists";

type MinutesNotice = Extract<BillingNotice, { kind: "insufficient" | "too_long" }>;

export interface CalloutLines {
  title: string;
  detail: string | null;
}

function joinSentences(...sentences: Array<string | null>): string {
  return sentences.filter((sentence): sentence is string => !!sentence).join(" ");
}

/** What the not-enough-minutes callout says: a headline and, when there is more, one detail line. */
export function minutesCalloutLines(notice: MinutesNotice, text: BillingText, nowMs: number): CalloutLines {
  const { labels } = text;
  if (notice.kind === "too_long") {
    const title =
      notice.maxMinutes === null
        ? labels.calloutTooLongUnknown
        : fillTemplate(labels.calloutTooLong, { max: text.minutesOblique(notice.maxMinutes) });
    return { title, detail: null };
  }

  const needs =
    notice.needed === null || notice.available === null
      ? labels.calloutNeedsUnknown
      : fillTemplate(labels.calloutNeeds, {
          needed: text.minutesOblique(notice.needed),
          available: text.minutes(notice.available),
        });
  // A next-free time means the video fits the free video, which is already used.
  if (notice.nextFreeAt === null) return { title: needs, detail: null };

  const when = text.until(notice.nextFreeAt, nowMs);
  const next = when === null ? null : fillTemplate(labels.calloutFreeNext, { when });
  return { title: labels.calloutFreeUsed, detail: joinSentences(needs, next) };
}

/** One sentence for any billing refusal (the callout kinds are joined into one line). */
export function describeBillingNotice(notice: BillingNotice, text: BillingText, nowMs: number): string {
  const { labels } = text;
  switch (notice.kind) {
    case "insufficient":
    case "too_long": {
      const { title, detail } = minutesCalloutLines(notice, text, nowMs);
      return joinSentences(title, detail);
    }
    case "blocked":
      return labels.blocked;
    case "too_many_runs":
      return notice.maxConcurrent === null
        ? labels.errorTooManyRunsUnknown
        : fillTemplate(labels.errorTooManyRuns, { running: text.videos(notice.maxConcurrent) });
    case "rate_limited": {
      const when = text.untilSeconds(notice.retryAfterSec, nowMs);
      return when === null ? labels.errorRateLimited : fillTemplate(labels.errorRateLimitedIn, { when });
    }
    case "chat_limit": {
      // A clock time, not a countdown: this sentence is saved in the chat transcript.
      const upcoming = notice.nextAt !== null && Date.parse(notice.nextAt) > nowMs;
      const time = upcoming ? text.moment(notice.nextAt, nowMs) : null;
      return joinSentences(
        labels.errorChatLimit,
        time === null ? null : fillTemplate(labels.errorChatLimitAt, { time }),
      );
    }
  }
}

/** A stable machine code such as FOO_BAR, as opposed to a sentence. */
const CODE_PATTERN = /^[A-Z][A-Z0-9]*(_[A-Z0-9]+)+$/;

/** Codes for an item whose length or hold could not be settled: the video could not be read. */
const UNREADABLE_CODES = new Set(["METADATA_UNAVAILABLE", "HOLD_CONFLICT"]);

/** Why one playlist item was not added: the refusal in the user's language, else the server's message. */
export function describeFailedImport(item: FailedVideoImport, text: BillingText): string {
  const { labels } = text;
  if (UNREADABLE_CODES.has(item.error)) return labels.failedUnreadable;
  switch (classifyBillingCode(item.error)?.kind) {
    case "insufficient":
      return labels.failedInsufficient;
    case "too_long":
      return labels.failedTooLong;
    case "blocked":
      return labels.failedBlocked;
    case "too_many_runs":
      return labels.failedTooManyRuns;
    case "rate_limited":
      return labels.failedRateLimited;
    default:
      return item.error && !CODE_PATTERN.test(item.error) ? item.error : labels.failedUnknown;
  }
}

/** True when the item failed because of the user's minutes, so the result can point at /settings/billing. */
export function isMinutesFailure(item: FailedVideoImport): boolean {
  const kind = classifyBillingCode(item.error)?.kind;
  return kind === "insufficient" || kind === "too_long";
}

/** "The whole playlist needs 340 minutes; you have 120 minutes." — only when the quote shows a shortfall. */
export function playlistQuoteLine(billing: PlaylistQuote | undefined, text: BillingText): string | null {
  if (!billing || billing.neededMinutes <= billing.availableMinutes) return null;
  return fillTemplate(text.labels.playlistNeeds, {
    needed: text.minutesOblique(billing.neededMinutes),
    available: text.minutes(billing.availableMinutes),
  });
}

export const RETRY_FAILED_MESSAGE = "Couldn't retry this video. Try again in a moment.";

/** One line saying why a retry / re-summarize was refused: a billing refusal in the user's language, else the error's message. */
export function describeRetryFailure(error: unknown, text: BillingText, nowMs: number): string {
  const notice = classifyBillingError(error);
  if (notice) return describeBillingNotice(notice, text, nowMs);
  return error instanceof Error && error.message ? error.message : RETRY_FAILED_MESSAGE;
}
