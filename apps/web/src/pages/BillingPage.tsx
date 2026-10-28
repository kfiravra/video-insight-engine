import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, ArrowLeft, Loader2 } from "lucide-react";
import { Layout } from "@/components/layout/Layout";
import { Button } from "@/components/ui/button";
import { UsageRows } from "@/components/usage/UsageRows";
import { useBillingText } from "@/hooks/use-billing-text";
import { useMinuteTick } from "@/hooks/use-minute-tick";
import { useUserUsage } from "@/hooks/use-usage";
import { fillTemplate } from "@/lib/format-minutes";
import { useLabels } from "@/lib/i18n";
import type { UsageSummary } from "@vie/types";

interface SectionProps {
  title: string;
  testId?: string;
  children: ReactNode;
}

function Section({ title, testId, children }: SectionProps) {
  return (
    <section className="rounded-xl border border-border bg-card/40 p-4 space-y-3" data-testid={testId}>
      <h2 className="type-eyebrow text-[11px] uppercase tracking-wide text-muted-foreground">{title}</h2>
      {children}
    </section>
  );
}

function PlanSection({ summary }: { summary: UsageSummary }) {
  const { labels, date } = useBillingText();
  const isPro = summary.plan.id === "pro";
  const { periodEnd } = summary.plan;
  const { cycleEnd } = summary.planMinutes;

  return (
    <Section title={labels.planSection} testId="billing-plan">
      <p className="text-lg font-semibold">{isPro ? labels.planPro : labels.planFree}</p>
      {isPro ? (
        <ul className="space-y-1 text-sm text-muted-foreground">
          {periodEnd && <li>{fillTemplate(labels.planTermEnds, { date: date(periodEnd) })}</li>}
          {cycleEnd && <li>{fillTemplate(labels.cycleEnds, { date: date(cycleEnd) })}</li>}
        </ul>
      ) : (
        <p className="text-sm text-muted-foreground">{labels.planFreeNote}</p>
      )}
      {summary.blocked && (
        <p role="alert" className="text-sm text-destructive">
          {labels.blocked}
        </p>
      )}
    </Section>
  );
}

function LotsSection({ lots }: { lots: UsageSummary["credits"]["lots"] }) {
  const { labels, minutes, date } = useBillingText();

  return (
    <Section title={labels.lotsSection} testId="billing-lots">
      {lots.length === 0 ? (
        <p className="text-sm text-muted-foreground">{labels.lotsEmpty}</p>
      ) : (
        <ul className="divide-y divide-border/60 text-sm">
          {lots.map((lot, index) => (
            <li key={`${lot.expiresAt ?? "never"}-${index}`} className="flex items-baseline justify-between gap-3 py-2">
              <span className="font-medium tabular-nums">{minutes(lot.minutes)}</span>
              <span className="text-xs text-muted-foreground">
                {lot.expiresAt ? fillTemplate(labels.lotExpires, { date: date(lot.expiresAt) }) : labels.lotNoExpiry}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

function HoldsSection({ holds }: { holds: UsageSummary["holds"] }) {
  const { labels, minutes } = useBillingText();

  return (
    <Section title={labels.holdsSection} testId="billing-holds">
      {holds.length === 0 ? (
        <p className="text-sm text-muted-foreground">{labels.holdsEmpty}</p>
      ) : (
        <ul className="divide-y divide-border/60 text-sm">
          {holds.map((hold, index) => (
            <li key={`${hold.youtubeId ?? "video"}-${index}`} className="flex items-baseline justify-between gap-3 py-2">
              <span className="truncate font-mono text-xs" dir="ltr">
                {hold.youtubeId ?? labels.holdVideoFallback}
              </span>
              <span className="font-medium tabular-nums">{minutes(hold.minutes)}</span>
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

function BillingBody({ summary }: { summary: UsageSummary }) {
  const { labels } = useBillingText();
  const now = useMinuteTick();

  return (
    <div className="space-y-4">
      <PlanSection summary={summary} />
      <Section title={labels.minutesSection} testId="billing-minutes">
        <UsageRows summary={summary} nowMs={now} className="text-sm" />
      </Section>
      <LotsSection lots={summary.credits.lots} />
      <HoldsSection holds={summary.holds} />
      <p
        className="rounded-xl border border-dashed border-border bg-muted/30 px-4 py-3 text-sm text-muted-foreground"
        data-testid="billing-team-note"
      >
        {labels.teamNote}
      </p>
    </div>
  );
}

/** /settings/billing: the signed-in user's plan, minutes and credit lots. Read-only; no checkout yet. */
export function BillingPage() {
  const { data, isLoading, isError, refetch } = useUserUsage();
  const { labels } = useBillingText();
  const { tryAgain } = useLabels();

  return (
    <Layout showSidebar={false}>
      <div className="page-gutter page-gutter-y">
        <div className="mx-auto w-full max-w-2xl stack-lg py-6">
          <Link
            to="/board"
            className="inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft className="h-3.5 w-3.5 rtl:rotate-180" aria-hidden="true" />
            {labels.back}
          </Link>

          <div className="stack-sm">
            <h1 className="type-page-title">{labels.pageTitle}</h1>
            <p className="type-caption">{labels.pageIntro}</p>
          </div>

          {isLoading ? (
            <div className="flex items-center gap-2 py-6 text-muted-foreground" role="status" aria-live="polite">
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              <span>{labels.tileLoading}</span>
            </div>
          ) : isError || !data ? (
            <div className="flex items-center gap-3 py-6 text-amber-600 dark:text-amber-400" role="alert">
              <AlertTriangle className="h-4 w-4" aria-hidden="true" />
              <span>{labels.tileError}</span>
              <Button size="sm" variant="outline" onClick={() => void refetch()}>
                {tryAgain}
              </Button>
            </div>
          ) : (
            <BillingBody summary={data} />
          )}
        </div>
      </div>
    </Layout>
  );
}
