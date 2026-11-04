import { useId, useState, type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { PaymentsPlaceholderNotice } from "./PaymentsPlaceholderNotice";

interface OfferCardProps {
  title: string;
  /** Shown beside the title, e.g. "Your plan" on the plan the account already has. */
  badge?: ReactNode;
  /** The price, already formatted by the card that owns it. */
  price: string;
  /** What the price buys: "20,000 tokens every month". */
  tokensLine: string;
  details: string[];
  /** The action's label; null when there is nothing to buy (the account already has it). */
  ctaLabel: string | null;
  testId: string;
}

/**
 * The shell of the Pro and pack cards on /settings/billing. Checkout is not
 * live, so the action opens an inline notice under it instead of a payment
 * step; the notice stays open once shown.
 */
export function OfferCard({ title, badge, price, tokensLine, details, ctaLabel, testId }: OfferCardProps) {
  const [noticeOpen, setNoticeOpen] = useState<boolean>(false);
  const noticeId = useId();

  return (
    <article className="flex flex-col gap-3 rounded-xl border border-border bg-card p-4" data-testid={testId}>
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-foreground">{title}</h3>
        {badge}
      </div>
      <div>
        <p className="text-lg font-semibold tracking-tight tabular-nums text-foreground">{price}</p>
        <p className="mt-0.5 text-sm font-medium tabular-nums text-foreground">{tokensLine}</p>
      </div>
      <ul className="space-y-1 text-xs text-muted-foreground tabular-nums">
        {details.map((detail) => (
          <li key={detail}>{detail}</li>
        ))}
      </ul>
      {ctaLabel && (
        <div className="mt-auto space-y-2 pt-1">
          <Button
            className="w-full"
            onClick={() => setNoticeOpen(true)}
            aria-expanded={noticeOpen}
            aria-controls={noticeOpen ? noticeId : undefined}
          >
            {ctaLabel}
          </Button>
          {/* The live region is there before the notice fills it, so screen readers announce it. */}
          <div aria-live="polite">{noticeOpen && <PaymentsPlaceholderNotice id={noticeId} />}</div>
        </div>
      )}
    </article>
  );
}
