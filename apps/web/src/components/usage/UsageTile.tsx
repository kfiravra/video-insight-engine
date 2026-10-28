import { Link } from "react-router-dom";
import { Loader2, AlertTriangle } from "lucide-react";
import { useBillingText } from "@/hooks/use-billing-text";
import { useMinuteTick } from "@/hooks/use-minute-tick";
import { useUserUsage } from "@/hooks/use-usage";
import { cn } from "@/lib/utils";
import { UsageRows } from "./UsageRows";

interface UsageTileProps {
  /** Optional className for parent layouts (e.g. inside a popover) */
  className?: string;
}

/**
 * The signed-in user's minutes in the user menu: the free video, Pro minutes
 * left this cycle, credits and runs in progress, with a link to
 * /settings/billing. Minutes only; no money amount appears anywhere.
 */
export function UsageTile({ className }: UsageTileProps) {
  const { data, isLoading, isError } = useUserUsage();
  const { labels } = useBillingText();
  const now = useMinuteTick();

  return (
    <div
      className={cn("rounded-xl border border-border bg-card/40 px-3 py-3 text-xs", "shadow-sm", className)}
      role="region"
      aria-label={labels.tileAria}
      data-testid="usage-tile"
    >
      <div className="flex items-center justify-between gap-2">
        <p className="type-eyebrow text-[10px] uppercase tracking-wide text-muted-foreground">
          {labels.tileTitle}
        </p>
        <span className="text-[10px] font-medium text-muted-foreground">
          {data ? (data.plan.id === "pro" ? labels.planPro : labels.planFree) : "—"}
        </span>
      </div>

      {isLoading ? (
        <div className="flex items-center gap-2 py-3 text-muted-foreground" role="status" aria-live="polite">
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
          <span>{labels.tileLoading}</span>
        </div>
      ) : isError || !data ? (
        <div className="flex items-center gap-2 py-3 text-amber-600 dark:text-amber-400" role="alert">
          <AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" />
          <span>{labels.tileError}</span>
        </div>
      ) : (
        <>
          <UsageRows summary={data} nowMs={now} className="mt-2" />

          {data.blocked && (
            <p role="alert" className="mt-2 text-[11px] text-destructive">
              {labels.blocked}
            </p>
          )}

          <Link
            to="/settings/billing"
            className={cn(
              "mt-3 inline-flex items-center gap-1.5 rounded-md border border-primary/30 px-2.5 py-1.5",
              "text-[11px] font-medium text-primary",
              "bg-primary/10 hover:bg-primary/15 transition-colors",
              "w-full justify-center",
            )}
            data-testid="usage-tile-link"
          >
            {labels.manageLink}
          </Link>
        </>
      )}
    </div>
  );
}
