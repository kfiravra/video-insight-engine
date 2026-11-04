import { useMemo } from "react";
import { useDirection } from "@/contexts/DirectionContext";
import {
  fillTemplate,
  formatDate,
  formatDurationSeconds,
  formatDurationUntil,
  formatNumber,
  formatPlural,
} from "@/lib/format-tokens";
import { useLabels } from "@/lib/i18n";
import type { BillingLabels } from "@/lib/i18n";

export interface BillingText {
  labels: BillingLabels;
  language: string;
  /** A bare number in Latin digits with grouping: "1,250", "-120". */
  number: (count: number) => string;
  /** "840 tokens", "1 token"; the noun forms come from the language's table. */
  tokens: (count: number) => string;
  /** An estimate rather than a final charge: "≈ 840 tokens". */
  approxTokens: (count: number) => string;
  minutes: (count: number) => string;
  videos: (count: number) => string;
  months: (count: number) => string;
  /** The same phrases after a preposition ("needs …", "up to …"); differs only in Arabic. */
  tokensOblique: (count: number) => string;
  minutesOblique: (count: number) => string;
  videosOblique: (count: number) => string;
  /** Countdown to `iso` ("7h 30m"), or null when it is missing, invalid or already past. */
  until: (iso: string | null | undefined, nowMs: number) => string | null;
  /** Countdown for a wait of `seconds`, or null when it is missing or not positive. */
  untilSeconds: (seconds: number | null | undefined, nowMs: number) => string | null;
  date: (iso: string | null | undefined) => string;
}

/** The billing strings of the current language plus formatters bound to it. */
export function useBillingText(): BillingText {
  const { language } = useDirection();
  const { billing: labels } = useLabels();

  return useMemo<BillingText>(() => {
    const tokens = (count: number): string => formatPlural(count, language, labels.tokens);
    return {
      labels,
      language,
      number: (count) => formatNumber(count, language),
      tokens,
      approxTokens: (count) => fillTemplate(labels.approxTokens, { tokens: tokens(count) }),
      minutes: (count) => formatPlural(count, language, labels.minutes),
      videos: (count) => formatPlural(count, language, labels.videos),
      months: (count) => formatPlural(count, language, labels.months),
      tokensOblique: (count) => formatPlural(count, language, labels.tokensOblique),
      minutesOblique: (count) => formatPlural(count, language, labels.minutesOblique),
      videosOblique: (count) => formatPlural(count, language, labels.videosOblique),
      until: (iso, nowMs) => (iso ? formatDurationUntil(iso, nowMs, language, labels.duration) : null),
      untilSeconds: (seconds, nowMs) => formatDurationSeconds(seconds, nowMs, language, labels.duration),
      date: (iso) => formatDate(iso, language),
    };
  }, [labels, language]);
}
