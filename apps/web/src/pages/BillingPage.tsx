import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { ArrowLeft, Loader2, TriangleAlert } from "lucide-react";
import { Layout } from "@/components/layout/Layout";
import { PackCard } from "@/components/billing/PackCard";
import { PlanCard } from "@/components/billing/PlanCard";
import { TeamContactLink } from "@/components/billing/TeamContactLink";
import { Button } from "@/components/ui/button";
import { PlanPill } from "@/components/usage/PlanPill";
import { UsageRows } from "@/components/usage/UsageRows";
import { useBillingText } from "@/hooks/use-billing-text";
import { useMinuteTick } from "@/hooks/use-minute-tick";
import { useUserUsage } from "@/hooks/use-usage";
import { fillTemplate } from "@/lib/format-tokens";
import { useLabels } from "@/lib/i18n";
import { TOKEN_PACKS, type UsageSummary } from "@vie/types";

interface SectionProps {
  title: string;
  testId?: string;
  /** Beside the title, e.g. the plan pill. */
  aside?: ReactNode;
  children: ReactNode;
}

function Section({ title, testId, aside, children }: SectionProps) {
  return (
    <section className="rounded-xl border border-border bg-card/40 p-4 space-y-3" data-testid={testId}>
      <div className="flex items-center justify-between gap-2">
        <h2 className="type-eyebrow text-[11px] uppercase tracking-wide text-muted-foreground">{title}</h2>
        {aside}
      </div>
      {children}
    </section>
  );
}

interface BalanceSectionProps {
  summary: UsageSummary;
}

function BalanceSection({ summary }: BalanceSectionProps) {
  const { labels } = useBillingText();
  const now = useMinuteTick();

  return (
    <Section title={labels.tileTitle} testId="billing-balance" aside={<PlanPill planId={summary.plan.id} />}>
      <UsageRows summary={summary} nowMs={now} className="text-sm" />
      {summary.blocked && (
        <p role="alert" className="text-sm text-destructive">
          {labels.blocked}
        </p>
      )}
    </Section>
  );
}

interface OffersSectionProps {
  summary: UsageSummary;
}

/** Pro and the token packs, with the real prices; their buttons say payments are not live yet. */
function OffersSection({ summary }: OffersSectionProps) {
  const { labels } = useBillingText();

  return (
    <section className="space-y-3" data-testid="billing-offers" aria-labelledby="billing-offers-title">
      <h2 id="billing-offers-title" className="type-eyebrow text-[11px] uppercase tracking-wide text-muted-foreground">
        {labels.offersSection}
      </h2>
      <div className="grid gap-3 sm:grid-cols-2">
        <PlanCard
          planId={summary.plan.id}
          periodEnd={summary.plan.periodEnd}
          cycleEnd={summary.planTokens?.cycleEnd ?? null}
        />
        {TOKEN_PACKS.map((pack) => (
          <PackCard key={pack.id} pack={pack} />
        ))}
      </div>
    </section>
  );
}

interface LotsSectionProps {
  lots: UsageSummary["credits"]["lots"];
}

function LotsSection({ lots }: LotsSectionProps) {
  const { labels, tokens, date } = useBillingText();

  return (
    <Section title={labels.lotsSection} testId="billing-lots">
      {lots.length === 0 ? (
        <p className="text-sm text-muted-foreground">{labels.lotsEmpty}</p>
      ) : (
        <ul className="divide-y divide-border/60 text-sm">
          {lots.map((lot, index) => (
            <li key={`${lot.expiresAt ?? "never"}-${index}`} className="flex items-baseline justify-between gap-3 py-2">
              <span className="font-medium tabular-nums">{tokens(lot.tokens)}</span>
              <span className="text-xs text-muted-foreground tabular-nums">
                {lot.expiresAt ? fillTemplate(labels.lotExpires, { date: date(lot.expiresAt) }) : labels.lotNoExpiry}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

interface HoldsSectionProps {
  holds: UsageSummary["holds"];
}

function HoldsSection({ holds }: HoldsSectionProps) {
  const { labels, approxTokens } = useBillingText();

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
              <span className="font-medium tabular-nums">{approxTokens(hold.tokens)}</span>
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

interface BillingBodyProps {
  summary: UsageSummary;
}

function BillingBody({ summary }: BillingBodyProps) {
  const { labels } = useBillingText();

  return (
    <div className="space-y-4">
      <BalanceSection summary={summary} />
      <OffersSection summary={summary} />
      <LotsSection lots={summary.credits.lots} />
      <HoldsSection holds={summary.holds} />
      <p
        className="rounded-xl border border-dashed border-border bg-muted/30 px-4 py-3 text-sm text-muted-foreground"
        data-testid="billing-team-note"
      >
        {labels.teamNote} <TeamContactLink />
      </p>
    </div>
  );
}

/**
 * /settings/billing: the signed-in user's tokens (balance, Pro cycle, credit
 * lots, runs in progress) and what adds more (Pro, token packs). Checkout is
 * not live: the buttons open a notice that the team adds tokens for now.
 */
export function BillingPage() {
  const { data, isError, refetch } = useUserUsage();
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

          {data ? (
            <BillingBody summary={data} />
          ) : isError ? (
            <div className="flex items-center gap-3 py-6 text-foreground" role="alert">
              <TriangleAlert className="h-4 w-4 shrink-0 text-warning" aria-hidden="true" />
              <span>{labels.tileError}</span>
              <Button size="sm" variant="outline" onClick={() => void refetch()}>
                {tryAgain}
              </Button>
            </div>
          ) : (
            <div className="flex items-center gap-2 py-6 text-muted-foreground" role="status" aria-live="polite">
              <Loader2 className="h-4 w-4 shrink-0 animate-spin motion-reduce:animate-none" aria-hidden="true" />
              <span>{labels.tileLoading}</span>
            </div>
          )}
        </div>
      </div>
    </Layout>
  );
}
