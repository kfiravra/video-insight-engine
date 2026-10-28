import { useMemo } from "react";
import { useDirection } from "@/contexts/DirectionContext";
import {
  formatDate,
  formatDurationUntil,
  formatMinutes,
  formatNumber,
  formatPlural,
} from "@/lib/format-minutes";
import { useLabels } from "@/lib/i18n";
import type { BillingLabels } from "@/lib/i18n";

export interface BillingText {
  labels: BillingLabels;
  language: string;
  /** A bare number in Latin digits with grouping: "1,250". */
  number: (count: number) => string;
  /** "150 minutes", "1 minute", Hebrew "שתי דקות", Arabic "دقيقتان". */
  minutes: (count: number) => string;
  videos: (count: number) => string;
  /** Countdown to `iso` ("7h 30m"), or null when it is missing, invalid or already past. */
  until: (iso: string | null | undefined, nowMs: number) => string | null;
  date: (iso: string | null | undefined) => string;
}

/** The billing strings of the current language plus formatters bound to it. */
export function useBillingText(): BillingText {
  const { language } = useDirection();
  const { billing: labels } = useLabels();

  return useMemo<BillingText>(
    () => ({
      labels,
      language,
      number: (count) => formatNumber(count, language),
      minutes: (count) => formatMinutes(count, language, labels.minutes),
      videos: (count) => formatPlural(count, language, labels.videos),
      until: (iso, nowMs) => (iso ? formatDurationUntil(iso, nowMs, language, labels.duration) : null),
      date: (iso) => formatDate(iso, language),
    }),
    [labels, language],
  );
}
