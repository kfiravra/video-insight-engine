import { fillTemplate } from "@/lib/format-minutes";
import type { BillingText } from "@/hooks/use-billing-text";
import { classifyBillingCode, type BillingNotice } from "@/lib/billing-errors";
import type { FailedVideoImport } from "@/api/playlists";

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
        : fillTemplate(labels.calloutTooLong, { max: text.minutes(notice.maxMinutes) });
    return { title, detail: null };
  }

  const needs =
    notice.needed === null || notice.available === null
      ? labels.calloutNeedsUnknown
      : fillTemplate(labels.calloutNeeds, {
          needed: text.minutes(notice.needed),
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
    case "rate_limited":
      return notice.message ?? labels.errorRateLimited;
    case "chat_limit": {
      const when = text.until(notice.nextAt, nowMs);
      return joinSentences(
        labels.errorChatLimit,
        when === null ? null : fillTemplate(labels.errorChatLimitNext, { when }),
      );
    }
  }
}

/** A stable machine code such as FOO_BAR, as opposed to a sentence. */
const CODE_PATTERN = /^[A-Z][A-Z0-9]*(_[A-Z0-9]+)+$/;

/** Why one playlist item was not added: the billing refusal in the user's language, else the server's message. */
export function describeFailedImport(item: FailedVideoImport, text: BillingText): string {
  const { labels } = text;
  const notice = classifyBillingCode(item.code ?? item.error);
  switch (notice?.kind) {
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
  const kind = classifyBillingCode(item.code ?? item.error)?.kind;
  return kind === "insufficient" || kind === "too_long";
}
