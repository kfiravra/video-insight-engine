import { classifyBillingError } from "@/lib/billing-errors";
import { RETRY_FAILED_MESSAGE } from "@/lib/billing-notice-text";
import { cn } from "@/lib/utils";
import { BillingNoticeAlert } from "./BillingNoticeAlert";

interface RetryErrorNoticeProps {
  /** The retry mutation's `error`; nothing renders without one. */
  error: unknown;
  className?: string;
}

/**
 * Why a retry / re-summarize did not start. A retry is a billed submit, so it can
 * be refused like any other: not enough minutes (402) or too long (422) get the
 * minutes callout with its link to /settings/billing, the other billing refusals a
 * plain localised alert, and anything else the error's own message.
 */
export function RetryErrorNotice({ error, className }: RetryErrorNoticeProps) {
  if (!error) return null;

  const notice = classifyBillingError(error);
  if (notice) return <BillingNoticeAlert notice={notice} className={className} />;

  return (
    <p role="alert" data-testid="retry-error" className={cn("text-sm text-destructive", className)}>
      {error instanceof Error && error.message ? error.message : RETRY_FAILED_MESSAGE}
    </p>
  );
}
