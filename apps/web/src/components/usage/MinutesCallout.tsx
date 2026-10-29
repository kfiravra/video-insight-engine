import { Link } from "react-router-dom";
import { AlertCircle, Clock } from "lucide-react";
import { useBillingText } from "@/hooks/use-billing-text";
import { useMinuteTick } from "@/hooks/use-minute-tick";
import { minutesCalloutLines } from "@/lib/billing-notice-text";
import { cn } from "@/lib/utils";
import type { BillingNotice } from "@/lib/billing-errors";

interface MinutesCalloutProps {
  /** The 402 / 422 refusal that opened the callout. */
  notice: Extract<BillingNotice, { kind: "insufficient" | "too_long" }>;
  className?: string;
}

/**
 * Inline callout for "not enough minutes" (402 INSUFFICIENT_MINUTES) and "too
 * long for this account" (422 VIDEO_TOO_LONG_FOR_PLAN). The server decides both
 * from the free video, plan minutes and credits, so the callout shows only when
 * the free video is spent and what is left cannot cover this video. It points at
 * /settings/billing; there is no checkout yet.
 */
export function MinutesCallout({ notice, className }: MinutesCalloutProps) {
  const text = useBillingText();
  const now = useMinuteTick();
  const { title, detail } = minutesCalloutLines(notice, text, now);

  return (
    <div
      role="alert"
      data-testid="minutes-callout"
      className={cn(
        "mt-3 rounded-xl border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm",
        "flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3",
        className,
      )}
    >
      <div className="flex items-start gap-2">
        <AlertCircle className="h-4 w-4 mt-0.5 text-amber-600 dark:text-amber-400 shrink-0" aria-hidden="true" />
        <div className="min-w-0 text-start">
          <p className="font-medium text-foreground">{title}</p>
          {detail && <p className="text-xs text-muted-foreground mt-0.5">{detail}</p>}
        </div>
      </div>
      <Link
        to="/settings/billing"
        className={cn(
          "inline-flex items-center justify-center gap-1.5 rounded-lg px-3 py-1.5",
          "text-xs font-semibold bg-primary text-primary-foreground",
          "hover:bg-primary/90 transition-colors shrink-0",
        )}
        data-testid="minutes-callout-cta"
      >
        <Clock className="h-3.5 w-3.5" aria-hidden="true" />
        {text.labels.calloutCta}
      </Link>
    </div>
  );
}
