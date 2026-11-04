import type { ReactNode } from "react";
import { TriangleAlert } from "lucide-react";
import { useBillingText } from "@/hooks/use-billing-text";
import { debtLine } from "@/lib/billing-notice-text";
import { fillTemplate } from "@/lib/format-tokens";
import { cn } from "@/lib/utils";
import type { UsageSummary } from "@vie/types";

interface UsageRowProps {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  testId?: string;
}

function UsageRow({ label, value, hint, testId }: UsageRowProps) {
  return (
    <div data-testid={testId}>
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-muted-foreground">{label}</span>
        <span className="font-medium tabular-nums text-end text-foreground">{value}</span>
      </div>
      {hint && <div className="mt-0.5 text-[10px] text-muted-foreground text-end tabular-nums">{hint}</div>}
    </div>
  );
}

function earliestExpiry(lots: UsageSummary["credits"]["lots"]): string | null {
  const dates = lots.map((lot) => lot.expiresAt).filter((iso): iso is string => iso !== null);
  return dates.length === 0 ? null : dates.reduce((a, b) => (new Date(a).getTime() <= new Date(b).getTime() ? a : b));
}

interface PlanTokensRowProps {
  planTokens: NonNullable<UsageSummary["planTokens"]>;
  nowMs: number;
}

/** "Pro tokens · 12,400 of 20,000 left · resets in 12d" with the share left as a bar. */
function PlanTokensRow({ planTokens, nowMs }: PlanTokensRowProps) {
  const text = useBillingText();
  const { labels } = text;
  const { granted, remaining, cycleEnd } = planTokens;
  const percent = granted > 0 ? Math.min(100, Math.max(0, Math.round((remaining / granted) * 100))) : 0;
  const when = text.until(cycleEnd, nowMs);
  const left = fillTemplate(labels.planTokensLeft, { left: text.number(remaining), total: text.number(granted) });
  const line = when === null ? left : `${left} · ${fillTemplate(labels.planTokensResets, { when })}`;

  return (
    <div data-testid="usage-plan-tokens">
      <p className="text-muted-foreground">{labels.planTokens}</p>
      <p className="mt-0.5 font-medium tabular-nums text-foreground">{line}</p>
      <div
        className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-muted"
        role="progressbar"
        aria-label={labels.planTokens}
        aria-valuenow={percent}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuetext={left}
      >
        <div
          className="h-full bg-primary transition-[width] motion-reduce:transition-none"
          style={{ width: `${remaining > 0 ? Math.max(percent, 2) : 0}%` }}
        />
      </div>
    </div>
  );
}

interface DebtNoticeProps {
  debt: number;
}

/** The overdraft state: a run cost more than the account had, and nothing new starts until it is repaid. */
function DebtNotice({ debt }: DebtNoticeProps) {
  const text = useBillingText();

  return (
    <p
      role="status"
      data-testid="usage-debt"
      className="flex items-start gap-1.5 rounded-lg border border-warning/40 bg-warning-soft px-2.5 py-2 text-foreground tabular-nums"
    >
      <TriangleAlert className="mt-px h-3.5 w-3.5 shrink-0 text-warning" aria-hidden="true" />
      <span>{debtLine(debt, text)}</span>
    </p>
  );
}

interface UsageRowsProps {
  summary: UsageSummary;
  nowMs: number;
  className?: string;
}

/**
 * The tokens breakdown shared by the user-menu tile and /settings/billing: the
 * balance (negative while in debt), the debt notice, the Pro cycle, credits
 * and the estimates held by runs in flight.
 */
export function UsageRows({ summary, nowMs, className }: UsageRowsProps) {
  const text = useBillingText();
  const { labels } = text;
  const { balance, debt, planTokens, credits, holds } = summary;
  const expiry = earliestExpiry(credits.lots);
  const heldTokens = holds.reduce((sum, hold) => sum + hold.tokens, 0);

  return (
    <div className={cn("space-y-2", className)}>
      <p
        data-testid="usage-balance"
        className="text-xl font-semibold leading-tight tracking-tight tabular-nums text-foreground"
      >
        {text.tokens(balance)}
      </p>
      {debt > 0 && <DebtNotice debt={debt} />}
      {planTokens && <PlanTokensRow planTokens={planTokens} nowMs={nowMs} />}
      <UsageRow
        label={labels.credits}
        value={text.tokens(credits.available)}
        hint={expiry ? fillTemplate(labels.lotExpires, { date: text.date(expiry) }) : undefined}
        testId="usage-credits"
      />
      {holds.length > 0 && (
        <UsageRow label={labels.inProgress} value={text.approxTokens(heldTokens)} testId="usage-holds" />
      )}
    </div>
  );
}
