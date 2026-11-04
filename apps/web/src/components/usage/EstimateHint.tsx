import { Coins } from "lucide-react";
import { useBillingText } from "@/hooks/use-billing-text";
import { useUserUsage } from "@/hooks/use-usage";
import { useVideoEstimate } from "@/hooks/use-video-estimate";
import { tokensCalloutLines } from "@/lib/billing-notice-text";
import { cn } from "@/lib/utils";

interface EstimateHintProps {
  /** A YouTube video URL to estimate, or null to show nothing (empty, invalid, a playlist, a refusal on screen). */
  url: string | null;
  className?: string;
}

/**
 * "≈ 840 tokens" (or "Already summarized — free") under a submit form, from
 * GET /videos/estimate once the pasted URL settles. When the balance cannot
 * cover it (or the account owes tokens) it says so before the submit, in the
 * callout's own words. Silent while loading and on any error; it informs,
 * the submit decides.
 */
export function EstimateHint({ url, className }: EstimateHintProps) {
  const estimate = useVideoEstimate(url);
  const { data: usage } = useUserUsage();
  const text = useBillingText();
  const short = !!estimate && !estimate.free && !!usage && (usage.debt > 0 || usage.balance < estimate.tokens);

  let line: string | null = null;
  if (estimate) {
    if (estimate.free) line = text.labels.estimateFree;
    else if (short && usage) {
      line = tokensCalloutLines({ kind: "insufficient", needed: estimate.tokens, available: usage.balance }, text).title;
    } else line = text.approxTokens(estimate.tokens);
  }

  return (
    <div aria-live="polite" className={className}>
      {line && (
        <p
          data-testid="estimate-hint"
          data-short={short || undefined}
          className={cn(
            "inline-flex items-center gap-1.5 text-xs tabular-nums",
            short ? "font-medium text-foreground" : "text-muted-foreground",
          )}
        >
          {/* Only the icon carries the warning hue: light-mode --warning is too light for text. */}
          <Coins className={cn("h-3.5 w-3.5 shrink-0", short && "text-warning")} aria-hidden="true" />
          <span className="sr-only">{text.labels.estimateLabel}: </span>
          {line}
        </p>
      )}
    </div>
  );
}
