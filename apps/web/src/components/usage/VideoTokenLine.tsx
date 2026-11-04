import { createPortal } from "react-dom";
import { Coins } from "lucide-react";
import { useHeaderSlot } from "@/components/layout/header-slot";
import { useBillingText, type BillingText } from "@/hooks/use-billing-text";
import { fillTemplate } from "@/lib/format-tokens";
import type { VideoBilling } from "@/api/videos";

/** "≈ 840 tokens, in progress" / "758 tokens" / "Free" / "Not charged"; null for a state this build does not know. */
function videoTokenText(billing: VideoBilling, text: BillingText): string | null {
  const { labels } = text;
  switch (billing.state) {
    case "held":
      return fillTemplate(labels.videoHeld, { tokens: text.tokens(billing.estimatedTokens) });
    case "charged":
      return text.tokens(billing.chargedTokens ?? billing.estimatedTokens);
    case "free":
      return labels.videoFree;
    case "released":
      return labels.videoReleased;
    default:
      return null;
  }
}

interface VideoTokenLineProps {
  /** `GET /videos/:id` `billing`; nothing renders without it (a server that predates tokens). */
  billing: VideoBilling | undefined;
}

/**
 * What this user's latest run of the video holds or cost. It sits in the app
 * header's page-control slot beside the language toggle, so it costs the hero
 * no row; outside a Layout it keeps a row of its own at the top.
 */
export function VideoTokenLine({ billing }: VideoTokenLineProps) {
  const slot = useHeaderSlot();
  const text = useBillingText();
  const line = billing ? videoTokenText(billing, text) : null;
  if (!line) return null;

  const chip = (
    <p
      data-testid="video-token-line"
      title={line}
      className="inline-flex max-w-44 items-center gap-1.5 rounded-full border border-border/60 bg-muted/40 px-2.5 py-1 text-xs text-muted-foreground tabular-nums sm:max-w-none"
    >
      <Coins className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
      <span className="sr-only">{text.labels.videoTokensLabel}: </span>
      <span className="truncate">{line}</span>
    </p>
  );

  if (slot) return createPortal(chip, slot);
  return <div className="mx-auto flex w-full max-w-5xl justify-end px-3 pt-3 md:px-6">{chip}</div>;
}
