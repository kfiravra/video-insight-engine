import { Loader2, TriangleAlert } from "lucide-react";
import { useBillingText } from "@/hooks/use-billing-text";
import { useMinuteTick } from "@/hooks/use-minute-tick";
import { useUserUsage } from "@/hooks/use-usage";
import { cn } from "@/lib/utils";
import { PlanPill } from "./PlanPill";
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
      <div className="flex items-center gap-2 py-3 text-foreground" role="alert">
        <TriangleAlert className="h-3.5 w-3.5 shrink-0 text-warning" aria-hidden="true" />
        <span>{labels.tileError}</span>
      </div>
    );
  }
  return (
    <div className="flex items-center gap-2 py-3 text-muted-foreground" role="status" aria-live="polite">
      <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin motion-reduce:animate-none" aria-hidden="true" />
      <span>{labels.tileLoading}</span>
    </div>
  );
}

/**
 * The signed-in user's tokens in the user menu: the balance (negative while in
 * debt), the Pro cycle, credits and the estimates held by runs in progress.
 * Tokens only; no money amount appears here. The link to /settings/billing is a
 * menu item beside it (UsageMenuItem), because a link inside the menu header is
 * not keyboard-reachable.
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
        {data ? (
          <PlanPill planId={data.plan.id} />
        ) : (
          <span className="text-[10px] font-medium text-muted-foreground">—</span>
        )}
      </div>
      <TileBody data={data} isError={isError} />
    </div>
  );
}
