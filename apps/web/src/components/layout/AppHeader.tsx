import { useState, useEffect } from "react";
import { PanelLeft, PanelLeftClose, LogOut, Keyboard, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { VieLogotype } from "@/components/brand/VieMark";
import {
  VieMenu,
  VieMenuItem,
  VieMenuHeader,
  VieMenuSeparator,
  VieMenuDestructiveDivider,
} from "@/components/vie";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { ThemeToggle } from "@/components/theme-toggle";
import { UsageMenuItem } from "@/components/usage/UsageMenuItem";
import { UsageTile } from "@/components/usage/UsageTile";
import { ErrorBoundary } from "@/components/ui/error-boundary";
import { useUIStore } from "@/stores/ui-store";
import { useAuthStore } from "@/stores/auth-store";
import { useSidebarToggle } from "@/hooks/use-sidebar-toggle";
import { getInitials } from "@/lib/string-utils";
import { getCmdGlyph } from "@/lib/platform";
import { cn } from "@/lib/utils";

// Module-level — the platform doesn't change mid-session, so resolve once
// and keep the SSR-safe navigator guard out of render entirely.
const CMD_GLYPH = getCmdGlyph();

interface AppHeaderProps {
  /** Receives the slot a page portals its own control into (header-slot.ts). */
  slotRef?: (element: HTMLDivElement | null) => void;
}

export function AppHeader({ slotRef }: AppHeaderProps) {
  const sidebarOpen = useUIStore((s) => s.sidebarOpen);
  const { toggle: toggleSidebar } = useSidebarToggle();
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const [showLogoutConfirm, setShowLogoutConfirm] = useState(false);

  // Ctrl+B / Cmd+B toggles sidebar
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "b") {
        e.preventDefault();
        toggleSidebar();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [toggleSidebar]);

  const openShortcuts = useUIStore((s) => s.openShortcutsModal);

  const openPalette = () => {
    window.dispatchEvent(new CustomEvent("vie:open-command-palette"));
  };

  return (
    <>
      <header
        className={cn(
          "h-(--app-header-height) shrink-0 relative",
          "border-b border-border/40 bg-background/80 backdrop-blur-[12px]",
          "flex items-center px-3 gap-3",
          // Quiet 1px domain underline — see --rule-domain in index.css
          "after:absolute after:inset-x-0 after:-bottom-px after:h-px after:opacity-60",
          "after:[background:var(--rule-domain)]",
        )}
      >
        {/* Left: sidebar toggle + branding */}
        <div className="flex items-center gap-2 shrink-0">
          <Button
            variant="ghost"
            size="icon"
            className="h-11 w-11 shrink-0"
            onClick={toggleSidebar}
            aria-label={sidebarOpen ? "Hide sidebar" : "Show sidebar"}
            aria-expanded={sidebarOpen}
            aria-controls="app-sidebar"
          >
            {sidebarOpen ? (
              <PanelLeftClose className="h-4 w-4" />
            ) : (
              <PanelLeft className="h-4 w-4" />
            )}
          </Button>
          {!sidebarOpen && <VieLogotype size="md" className="hidden sm:inline-flex" />}
        </div>

        <div className="flex-1" />

        {/* A page's own control (the video page's language toggle), portalled in. */}
        <div ref={slotRef} className="flex items-center empty:hidden" />

        {/* Right: command-palette hint + theme + user.
            The chip is a discoverable surface for power users — clicking it
            opens the palette, the kbd glyphs cue the shortcut. Hidden on
            touch viewports where keyboard shortcuts don't apply. */}
        <div className="flex items-center gap-1.5 shrink-0">
          <button
            type="button"
            onClick={openPalette}
            aria-label="Open command palette"
            className={cn(
              "hidden md:inline-flex items-center gap-2 h-9 px-3 rounded-md",
              "text-muted-foreground hover:text-foreground",
              "bg-muted/40 hover:bg-muted/70 ring-1 ring-border/50",
              "transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            )}
          >
            <Search className="h-3.5 w-3.5" aria-hidden="true" />
            <span className="type-caption text-foreground/70 leading-none">Search</span>
            <span aria-hidden="true" className="inline-flex items-center gap-0.5">
              <kbd className="px-1.5 py-0.5 rounded bg-background/70 ring-1 ring-border/60 font-mono tabular-nums text-[10px] leading-none">
                {CMD_GLYPH}
              </kbd>
              <kbd className="px-1.5 py-0.5 rounded bg-background/70 ring-1 ring-border/60 font-mono tabular-nums text-[10px] leading-none">
                K
              </kbd>
            </span>
          </button>

          <ThemeToggle />

          {isAuthenticated && user && (
            <VieMenu
              align="end"
              className="w-64"
              trigger={
                <Button
                  variant="ghost"
                  size="icon-bare"
                  aria-label={user.name ?? "Profile"}
                  className={cn(
                    "h-10 w-10 rounded-full text-xs font-semibold",
                    "bg-primary/10 text-primary",
                    "hover:bg-primary/15 transition-colors",
                  )}
                >
                  {getInitials(user.name, user.email)}
                </Button>
              }
            >
              <VieMenuHeader>
                <p className="type-eyebrow text-muted-foreground/80">
                  Signed in as
                </p>
                <p className="text-sm font-semibold truncate mt-0.5">{user.name || "User"}</p>
                {user.email && (
                  <p className="text-xs text-muted-foreground truncate">{user.email}</p>
                )}
                {/* A failing tile must not take the whole user menu down with it. */}
                <ErrorBoundary fallback={null}>
                  <UsageTile className="mt-3" />
                </ErrorBoundary>
              </VieMenuHeader>
              <UsageMenuItem />
              <VieMenuSeparator />
              <VieMenuItem
                icon={<Keyboard className="h-4 w-4" />}
                hint="?"
                onSelect={openShortcuts}
              >
                Keyboard shortcuts
              </VieMenuItem>
              <VieMenuDestructiveDivider />
              <VieMenuItem
                icon={<LogOut className="h-4 w-4" />}
                destructive
                onSelect={() => setShowLogoutConfirm(true)}
              >
                Sign out
              </VieMenuItem>
            </VieMenu>
          )}
        </div>
      </header>

      <AlertDialog open={showLogoutConfirm} onOpenChange={setShowLogoutConfirm}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Log out?</AlertDialogTitle>
            <AlertDialogDescription>
              You&apos;ll be signed out. Your library stays right where you left it — sign back in any time.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={logout}>Log out</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
