import { Loader2, AlertTriangle } from "lucide-react";
import { useBillingText } from "@/hooks/use-billing-text";
import { useMinuteTick } from "@/hooks/use-minute-tick";
import { useUserUsage } from "@/hooks/use-usage";
import { cn } from "@/lib/utils";
import { UsageRows } from "./UsageRows";
import type { UsageSummary } from "@vie/types";

interface UsageTileProps {
  /** Optional className for parent layouts (e.g. inside a popover) */
  className?: string;
}

interface TileBodyProps {
  data: UsageSummary | undefined;
  isError: boolean;
}

/** Loading, failed, or the rows. A failed refresh keeps showing the last good summary. */
function TileBody({ data, isError }: TileBodyProps) {
  const { labels } = useBillingText();
  const now = useMinuteTick();

  if (data) {
    return (
      <>
        <UsageRows summary={data} nowMs={now} className="mt-2" />
        {data.blocked && (
          <p role="alert" className="mt-2 text-[11px] text-destructive">
            {labels.blocked}
          </p>
        )}
      </>
    );
  }
  if (isError) {
    return (
      <div className="flex items-center gap-2 py-3 text-amber-600 dark:text-amber-400" role="alert">
        <AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" />
        <span>{labels.tileError}</span>
      </div>
    );
  }
  return (
    <div className="flex items-center gap-2 py-3 text-muted-foreground" role="status" aria-live="polite">
      <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
      <span>{labels.tileLoading}</span>
    </div>
  );
}

/**
 * The signed-in user's minutes in the user menu: the free video, Pro minutes
 * left this cycle, credits and runs in progress. Minutes only; no money amount
 * appears anywhere. The link to /settings/billing is a menu item beside it
 * (UsageMenuItem), because a link inside the menu header is not keyboard-reachable.
 */
export function UsageTile({ className }: UsageTileProps) {
  const { data, isError } = useUserUsage();
  const { labels } = useBillingText();

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
      <TileBody data={data} isError={isError} />
    </div>
  );
}
