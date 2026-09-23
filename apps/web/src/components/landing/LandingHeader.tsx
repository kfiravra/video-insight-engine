import { memo } from "react";
import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { VieLogotype } from "@/components/brand/VieMark";
import { useRegistrationOpen } from "@/hooks/use-registration-open";

/**
 * Landing-only header — flush to the page gutter, with a
 * faint monospace version badge on the right to anchor the
 * editorial feel without adding nav clutter.
 */
export const LandingHeader = memo(function LandingHeader() {
  const registrationOpen = useRegistrationOpen();

  return (
    <header className="flex items-center justify-between py-5">
      <Link
        to="/"
        className="flex items-center gap-2.5 hover:opacity-80 transition-opacity"
        aria-label="VIE home"
      >
        <VieLogotype size="md" animated />
        <span
          className="hidden sm:inline-flex items-center font-mono text-[0.625rem] text-muted-foreground uppercase tracking-[0.15em] ms-2"
          aria-hidden="true"
        >
          · video insight engine
        </span>
      </Link>

      <div className="flex items-center gap-1.5">
        <Button variant="ghost" size="sm" asChild>
          <Link to="/login">Log in</Link>
        </Button>
        {registrationOpen && (
          <Button size="sm" asChild>
            <Link to="/register">Sign up</Link>
          </Button>
        )}
      </div>
    </header>
  );
});
