import { useBillingText } from "@/hooks/use-billing-text";
import { useMinuteTick } from "@/hooks/use-minute-tick";
import { describeBillingNotice } from "@/lib/billing-notice-text";
import type { BillingNotice } from "@/lib/billing-errors";
import { cn } from "@/lib/utils";
import { MinutesCallout } from "./MinutesCallout";

interface BillingNoticeAlertProps {
  notice: BillingNotice;
  className?: string;
}

/** A billing refusal at submit time: the minutes callout for 402 / 422, a plain alert for the rest. */
export function BillingNoticeAlert({ notice, className }: BillingNoticeAlertProps) {
  const text = useBillingText();
  const now = useMinuteTick();

  if (notice.kind === "insufficient" || notice.kind === "too_long") {
    return <MinutesCallout notice={notice} className={className} />;
  }

  return (
    <p
      role="alert"
      data-testid="billing-notice"
      className={cn("text-sm text-destructive", className)}
    >
      {describeBillingNotice(notice, text, now)}
    </p>
  );
}
