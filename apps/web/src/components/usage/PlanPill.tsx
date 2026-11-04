import { Badge } from "@/components/ui/badge";
import { useBillingText } from "@/hooks/use-billing-text";
import type { PlanId } from "@vie/types";

interface PlanPillProps {
  planId: PlanId;
  className?: string;
}

/** The account's plan as a pill: "Pro" in the brand colour, "Free" as quiet metadata. */
export function PlanPill({ planId, className }: PlanPillProps) {
  const { labels } = useBillingText();
  const isPro = planId === "pro";

  return (
    <Badge variant={isPro ? "default" : "secondary"} className={className} data-testid="plan-pill">
      {isPro ? labels.planPro : labels.planFree}
    </Badge>
  );
}
