import { Clock } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { VieMenuItem } from "@/components/vie";
import { useBillingText } from "@/hooks/use-billing-text";

/**
 * "Minutes & plan" in the user menu. A real menu item, so Tab / arrow keys
 * reach it and Enter opens /settings/billing (a link inside the menu header is
 * skipped by the menu's focus handling).
 */
export function UsageMenuItem() {
  const navigate = useNavigate();
  const { labels } = useBillingText();

  return (
    <VieMenuItem icon={<Clock className="h-4 w-4" />} onSelect={() => navigate("/settings/billing")}>
      {labels.manageLink}
    </VieMenuItem>
  );
}
