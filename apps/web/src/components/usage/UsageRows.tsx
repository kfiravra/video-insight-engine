import type { ReactNode } from "react";
import { useBillingText } from "@/hooks/use-billing-text";
import { fillTemplate } from "@/lib/format-minutes";
import { cn } from "@/lib/utils";
import type { UsageSummary } from "@vie/types";
import type { BillingText } from "@/hooks/use-billing-text";

interface UsageRowProps {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  tone?: "default" | "good";
  testId?: string;
}

function UsageRow({ label, value, hint, tone = "default", testId }: UsageRowProps) {
  return (
    <div data-testid={testId}>
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-muted-foreground">{label}</span>
        <span
          className={cn(
            "font-medium tabular-nums text-end",
            tone === "good" ? "text-emerald-600 dark:text-emerald-400" : "text-foreground",
          )}
        >
          {value}
        </span>
      </div>
      {hint && <div className="mt-0.5 text-[10px] text-muted-foreground">{hint}</div>}
    </div>
  );
}

function freeVideoValue(free: UsageSummary["free"], text: BillingText, nowMs: number): string {
  const { labels } = text;
  if (free.available) return labels.freeVideoReady;
  if (!free.nextFreeAt) return labels.freeVideoUnavailable;
  const when = text.until(free.nextFreeAt, nowMs);
  // The window has just closed; the next refetch will say "ready".
  return when === null ? labels.freeVideoReady : fillTemplate(labels.freeVideoNext, { when });
}

function earliestExpiry(lots: UsageSummary["credits"]["lots"]): string | null {
  const dates = lots.map((lot) => lot.expiresAt).filter((iso): iso is string => iso !== null);
  return dates.length === 0 ? null : dates.reduce((a, b) => (new Date(a).getTime() <= new Date(b).getTime() ? a : b));
}

interface PlanMinutesRowProps {
  summary: UsageSummary;
  nowMs: number;
}

function PlanMinutesRow({ summary, nowMs }: PlanMinutesRowProps) {
  const text = useBillingText();
  const { labels } = text;
  const { granted, remaining, cycleEnd } = summary.planMinutes;
  const percent = granted > 0 ? Math.min(100, Math.max(0, Math.round((remaining / granted) * 100))) : 0;
  const when = text.until(cycleEnd, nowMs);

  return (
    <div data-testid="usage-plan-minutes">
      <UsageRow
        label={labels.planMinutes}
        value={fillTemplate(labels.planMinutesLeft, { left: text.number(remaining), total: text.minutes(granted) })}
        hint={when === null ? undefined : fillTemplate(labels.planMinutesResets, { when })}
      />
      <div
        className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-muted"
        role="progressbar"
        aria-label={labels.planMinutes}
        aria-valuenow={percent}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div className="h-full bg-primary transition-all" style={{ width: `${Math.max(percent, 2)}%` }} />
      </div>
    </div>
  );
}

interface UsageRowsProps {
  summary: UsageSummary;
  nowMs: number;
  className?: string;
}

/** The minutes breakdown shared by the user-menu tile and /settings/billing: free video, Pro, credits, holds. */
export function UsageRows({ summary, nowMs, className }: UsageRowsProps) {
  const text = useBillingText();
  const { labels } = text;
  const { free, credits, holds } = summary;
  const expiry = earliestExpiry(credits.lots);
  const heldMinutes = holds.reduce((sum, hold) => sum + hold.minutes, 0);

  return (
    <div className={cn("space-y-2", className)}>
      <UsageRow
        label={labels.freeVideo}
        value={freeVideoValue(free, text, nowMs)}
        tone={free.available ? "good" : "default"}
        hint={fillTemplate(labels.freeVideoUpTo, { minutes: text.minutes(free.maxVideoMinutes) })}
        testId="usage-free-video"
      />
      {summary.plan.id === "pro" && <PlanMinutesRow summary={summary} nowMs={nowMs} />}
      <UsageRow
        label={labels.credits}
        value={text.minutes(credits.available)}
        hint={expiry ? fillTemplate(labels.lotExpires, { date: text.date(expiry) }) : undefined}
        testId="usage-credits"
      />
      {holds.length > 0 && (
        <UsageRow label={labels.inProgress} value={text.minutes(heldMinutes)} testId="usage-holds" />
      )}
    </div>
  );
}
