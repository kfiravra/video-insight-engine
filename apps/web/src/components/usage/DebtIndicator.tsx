import { Link } from "react-router-dom";
import { TriangleAlert } from "lucide-react";
import { useBillingText } from "@/hooks/use-billing-text";
import { useUserUsage } from "@/hooks/use-usage";
import { debtLine } from "@/lib/billing-notice-text";
import { fillTemplate } from "@/lib/format-tokens";
import { cn } from "@/lib/utils";

interface DebtIndicatorProps {
  className?: string;
}

/**
 * "120 tokens short" in the app header while the account owes tokens. A run
 * that cost more than its hold leaves debt, and no new video or chat starts
 * until a top-up repays it, so the state shows outside the user menu, in the
 * usage tile's warning style. Links to /settings/billing. Narrow screens keep
 * the icon and the signed balance; the link's name is the full sentence.
 * Nothing renders without debt or before the usage has loaded.
 */
export function DebtIndicator({ className }: DebtIndicatorProps) {
  const { data: usage } = useUserUsage();
  const text = useBillingText();
  if (!usage || !(usage.debt > 0)) return null;

  const sentence = debtLine(usage.debt, text);

  return (
    <Link
      to="/settings/billing"
      data-testid="header-debt"
      aria-label={sentence}
      title={sentence}
      className={cn(
        "inline-flex h-9 shrink-0 items-center gap-1.5 rounded-lg border border-warning/40 bg-warning-soft px-2.5",
        "text-xs font-medium text-foreground tabular-nums transition-colors hover:border-warning/70",
        "outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50",
        className,
      )}
    >
      <TriangleAlert className="h-3.5 w-3.5 shrink-0 text-warning" aria-hidden="true" />
      <span className="hidden sm:inline">{fillTemplate(text.labels.headerDebt, { tokens: text.tokens(usage.debt) })}</span>
      <span className="sm:hidden">{text.number(-usage.debt)}</span>
    </Link>
  );
}
