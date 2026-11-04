import { useBillingText } from "@/hooks/use-billing-text";
import { useMinuteTick } from "@/hooks/use-minute-tick";
import { describeBillingNotice } from "@/lib/billing-notice-text";
import { isTokensNotice, type BillingNotice } from "@/lib/billing-errors";
import { cn } from "@/lib/utils";
import { TokensCallout } from "./TokensCallout";

interface BillingNoticeAlertProps {
  notice: BillingNotice;
  className?: string;
}

/** A billing refusal at submit time: the tokens callout for 402 / 422, a plain alert for the rest. */
export function BillingNoticeAlert({ notice, className }: BillingNoticeAlertProps) {
  const text = useBillingText();
  const now = useMinuteTick();

  if (isTokensNotice(notice)) {
    return <TokensCallout notice={notice} className={className} />;
  }

  return (
    <p
      role="alert"
      data-testid="billing-notice"
      className={cn("text-sm tabular-nums text-destructive", className)}
    >
      {describeBillingNotice(notice, text, now)}
    </p>
  );
}
