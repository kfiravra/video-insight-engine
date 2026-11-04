import { Badge } from "@/components/ui/badge";
import { useBillingText } from "@/hooks/use-billing-text";
import { fillTemplate } from "@/lib/format-tokens";
import { PLANS, type PlanId } from "@vie/types";
import { OfferCard } from "./OfferCard";

// One of the two files allowed to print a price (lib/__tests__/no-dollar-ui.test.ts).
function formatPrice(priceUsdCents: number): string {
  const fractionDigits = priceUsdCents % 100 === 0 ? 0 : 2;
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: fractionDigits,
  }).format(priceUsdCents / 100);
}

interface PlanCardProps {
  /** The account's plan: on Pro the card shows its dates instead of "Go Pro". */
  planId: PlanId;
  periodEnd: string | null;
  cycleEnd: string | null;
}

/** Pro: the monthly price and allowance from `PLANS.pro`, the account's term when it is on Pro. */
export function PlanCard({ planId, periodEnd, cycleEnd }: PlanCardProps) {
  const text = useBillingText();
  const { labels } = text;
  const pro = PLANS.pro;
  const isCurrent = planId === "pro";

  const details = [labels.proCardNote];
  if (isCurrent && periodEnd) details.push(fillTemplate(labels.planTermEnds, { date: text.date(periodEnd) }));
  if (isCurrent && cycleEnd) details.push(fillTemplate(labels.cycleEnds, { date: text.date(cycleEnd) }));

  return (
    <OfferCard
      title={labels.proCardTitle}
      badge={isCurrent ? <Badge>{labels.proCardCurrent}</Badge> : undefined}
      price={fillTemplate(labels.proCardPrice, { price: formatPrice(pro.priceUsdCents) })}
      tokensLine={fillTemplate(labels.proCardTokens, { tokens: text.tokens(pro.periodTokens) })}
      details={details}
      ctaLabel={isCurrent ? null : labels.proCardCta}
      testId="plan-card"
    />
  );
}
