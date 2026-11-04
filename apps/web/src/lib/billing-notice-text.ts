import { fillTemplate } from "@/lib/format-tokens";
import type { BillingText } from "@/hooks/use-billing-text";
import {
  classifyBillingCode,
  classifyBillingError,
  type BillingNotice,
  type TokensNotice,
} from "@/lib/billing-errors";
import type { FailedVideoImport, PlaylistQuote } from "@/api/playlists";

export interface CalloutLines {
  title: string;
  detail: string | null;
}

function joinSentences(...sentences: Array<string | null>): string {
  return sentences.filter((sentence): sentence is string => !!sentence).join(" ");
}

/** "You're 120 tokens short — add tokens to continue." for an account 120 tokens below zero. */
export function debtLine(shortfall: number, text: BillingText): string {
  return fillTemplate(text.labels.debtShort, { tokens: text.tokens(shortfall) });
}

/**
 * What the tokens callout says. A video the balance cannot cover names its
 * estimate and the balance; a balance below zero (a run cost more than it
 * held) leads with the shortfall, since every new execution waits for it.
 */
export function tokensCalloutLines(notice: TokensNotice, text: BillingText): CalloutLines {
  const { labels } = text;
  if (notice.kind === "too_long") {
    const title =
      notice.maxMinutes === null
        ? labels.calloutTooLongUnknown
        : fillTemplate(labels.calloutTooLong, { max: text.minutesOblique(notice.maxMinutes) });
    return { title, detail: null };
  }

  const { needed, available } = notice;
  if (needed === null || available === null) return { title: labels.calloutNeedsUnknown, detail: null };
  if (available < 0) {
    return {
      title: debtLine(-available, text),
      detail: fillTemplate(labels.calloutDebtDetail, { needed: text.tokensOblique(needed) }),
    };
  }
  return {
    title: fillTemplate(labels.calloutNeeds, {
      needed: text.tokensOblique(needed),
      available: text.tokens(available),
    }),
    detail: null,
  };
}

/** One sentence for any billing refusal at submit time (the callout kinds are joined into one line). */
export function describeBillingNotice(notice: BillingNotice, text: BillingText, nowMs: number): string {
  const { labels } = text;
  switch (notice.kind) {
    case "insufficient":
    case "too_long": {
      const { title, detail } = tokensCalloutLines(notice, text);
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
  }
}

/**
 * Why a chat turn was refused before it started (402 / 403), or null for any
 * other refusal. A turn needs a positive balance, so the copy names no
 * per-turn price: it says the tokens ran out, or how far below zero they are.
 */
export function describeChatRefusal(notice: BillingNotice, text: BillingText): string | null {
  if (notice.kind === "blocked") return text.labels.chatBlocked;
  if (notice.kind !== "insufficient") return null;
  return notice.available !== null && notice.available < 0
    ? debtLine(-notice.available, text)
    : text.labels.chatInsufficient;
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

/** True when the item failed for want of tokens, so the result can point at /settings/billing. */
export function isTokensFailure(item: FailedVideoImport): boolean {
  return classifyBillingCode(item.error)?.kind === "insufficient";
}

/** "The whole playlist needs ≈ 3,400 tokens; you have 1,200 tokens." — only when the quote shows a shortfall. */
export function playlistQuoteLine(billing: PlaylistQuote | undefined, text: BillingText): string | null {
  if (!billing || billing.neededTokens <= billing.availableTokens) return null;
  return fillTemplate(text.labels.playlistNeeds, {
    needed: text.tokensOblique(billing.neededTokens),
    available: text.tokens(billing.availableTokens),
  });
}

export const RETRY_FAILED_MESSAGE = "Couldn't retry this video. Try again in a moment.";

/** One line saying why a retry / re-summarize was refused: a billing refusal in the user's language, else the error's message. */
export function describeRetryFailure(error: unknown, text: BillingText, nowMs: number): string {
  const notice = classifyBillingError(error);
  if (notice) return describeBillingNotice(notice, text, nowMs);
  return error instanceof Error && error.message ? error.message : RETRY_FAILED_MESSAGE;
}
