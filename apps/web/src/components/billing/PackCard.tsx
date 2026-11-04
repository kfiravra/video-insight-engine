import { useBillingText } from "@/hooks/use-billing-text";
import { fillTemplate } from "@/lib/format-tokens";
import type { TokenPack } from "@vie/types";
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

interface PackCardProps {
  /** One entry of `TOKEN_PACKS`. */
  pack: TokenPack;
}

/** A token pack: its price, its tokens and how long they last, from `TOKEN_PACKS`. */
export function PackCard({ pack }: PackCardProps) {
  const text = useBillingText();
  const { labels } = text;

  return (
    <OfferCard
      title={labels.packCardTitle}
      price={formatPrice(pack.priceUsdCents)}
      tokensLine={text.tokens(pack.tokens)}
      details={[fillTemplate(labels.packCardExpiry, { months: text.months(pack.expiryMonths) }), labels.packCardNote]}
      ctaLabel={labels.packCardCta}
      testId={`pack-card-${pack.id}`}
    />
  );
}
