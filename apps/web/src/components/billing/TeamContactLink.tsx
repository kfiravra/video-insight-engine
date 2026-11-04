import { Mail } from "lucide-react";
import { useBillingText } from "@/hooks/use-billing-text";
import { supportEmail, supportMailto } from "@/lib/support";
import { cn } from "@/lib/utils";

interface TeamContactLinkProps {
  className?: string;
}

/**
 * "Email the team: support@…" after a note that says tokens come from the
 * team. Renders nothing when VITE_SUPPORT_EMAIL is unset, so a build without
 * an address shows the note alone. The address stays visible for a reader
 * with no mail client, isolated so it reads left to right in RTL.
 */
export function TeamContactLink({ className }: TeamContactLinkProps) {
  const { labels } = useBillingText();
  const email = supportEmail();
  if (!email) return null;

  return (
    <a
      href={supportMailto(email, labels.contactSubject)}
      data-testid="team-contact"
      className={cn(
        "group inline-flex items-center gap-1 rounded-sm font-medium text-foreground outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50",
        className,
      )}
    >
      <Mail className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
      <span className="underline decoration-border underline-offset-4 group-hover:decoration-foreground">
        {labels.contactTeam}: <bdi>{email}</bdi>
      </span>
    </a>
  );
}
