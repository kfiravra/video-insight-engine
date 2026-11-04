import { Link } from "react-router-dom";
import { AlertCircle, Coins } from "lucide-react";
import { buttonVariants } from "@/components/ui/button";
import { useBillingText } from "@/hooks/use-billing-text";
import { tokensCalloutLines } from "@/lib/billing-notice-text";
import { cn } from "@/lib/utils";
import type { TokensNotice } from "@/lib/billing-errors";

interface TokensCalloutProps {
  /** The 402 / 422 refusal that opened the callout. */
  notice: TokensNotice;
  className?: string;
}

/**
 * Inline callout for "not enough tokens" (402 INSUFFICIENT_TOKENS: the balance
 * cannot cover the video's estimate, or it is below zero) and "too long" (422
 * VIDEO_TOO_LONG). Only the first can be fixed with tokens, so only it points
 * at /settings/billing; payments are not live yet, the page says so.
 */
export function TokensCallout({ notice, className }: TokensCalloutProps) {
  const text = useBillingText();
  const { title, detail } = tokensCalloutLines(notice, text);
  const isInsufficient = notice.kind === "insufficient";
  const Icon = isInsufficient ? Coins : AlertCircle;

  return (
    <div
      role="alert"
      data-testid="tokens-callout"
      className={cn(
        "mt-3 rounded-xl border border-warning/40 bg-warning-soft px-4 py-3 text-sm",
        "flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3",
        className,
      )}
    >
      <div className="flex items-start gap-2">
        <Icon className="h-4 w-4 mt-0.5 shrink-0 text-warning" aria-hidden="true" />
        <div className="min-w-0 text-start tabular-nums">
          <p className="font-medium text-foreground">{title}</p>
          {detail && <p className="text-xs text-muted-foreground mt-0.5">{detail}</p>}
        </div>
      </div>
      {isInsufficient && (
        <Link
          to="/settings/billing"
          className={cn(buttonVariants({ size: "sm" }), "shrink-0 self-start sm:self-auto")}
          data-testid="tokens-callout-cta"
        >
          <Coins className="h-3.5 w-3.5" aria-hidden="true" />
          {text.labels.calloutCta}
        </Link>
      )}
    </div>
  );
}
