import { Info } from "lucide-react";
import { useBillingText } from "@/hooks/use-billing-text";
import { cn } from "@/lib/utils";

interface PaymentsPlaceholderNoticeProps {
  /** Referenced by the button that opened it (`aria-controls`). */
  id: string;
  className?: string;
}

/** What "Go Pro" and "Buy tokens" open until checkout exists: tokens come from the team for now. */
export function PaymentsPlaceholderNotice({ id, className }: PaymentsPlaceholderNoticeProps) {
  const { labels } = useBillingText();

  return (
    <p
      id={id}
      data-testid="payments-placeholder"
      className={cn(
        "flex items-start gap-1.5 rounded-lg border border-border bg-muted/40 px-3 py-2 text-xs text-muted-foreground",
        className,
      )}
    >
      <Info className="mt-px h-3.5 w-3.5 shrink-0" aria-hidden="true" />
      <span>{labels.paymentsPlaceholder}</span>
    </p>
  );
}
