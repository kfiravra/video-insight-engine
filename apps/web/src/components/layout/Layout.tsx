import { useRef, useCallback, useEffect, useState, lazy, Suspense } from "react";
import type { ReactNode } from "react";
import { ErrorBoundary } from "@/components/ui/error-boundary";
import { useUIStore } from "@/stores/ui-store";
import { useSidebarToggle } from "@/hooks/use-sidebar-toggle";
import { useMediaQuery } from "@/hooks/use-media-query";
import { AppHeader } from "./AppHeader";
import { HeaderSlotContext } from "./header-slot";
import { LeftSidebarIconStrip } from "./LeftSidebarIconStrip";
import { MobileBottomNav } from "./MobileBottomNav";
import { ScrollContainer } from "@/components/ui/scroll-container";
import { CommandPalette } from "@/components/CommandPalette";
import { KeyboardShortcutsModal } from "@/components/KeyboardShortcutsModal";
import { cn } from "@/lib/utils";

// Lazy load Sidebar - it includes DnD Kit context (~100KB)
const Sidebar = lazy(() =>
  import("@/features/sidebar").then((m) => ({ default: m.Sidebar }))
);

function SidebarSkeleton() {
  return (
    <div className="h-full bg-card animate-pulse flex flex-col">
      <div className="flex items-center gap-2 px-3 py-2.5 border-b border-border/50">
        <div className="h-7 w-7 bg-muted rounded-lg" />
        <div className="h-4 bg-muted rounded w-24" />
      </div>
      <div className="flex gap-4 px-3 py-2 border-b border-border/50">
        <div className="h-4 bg-muted rounded w-20" />
        <div className="h-4 bg-muted rounded w-20" />
      </div>
      <div className="flex-1 p-3 space-y-2">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="h-8 bg-muted rounded-md" />
        ))}
      </div>
    </div>
  );
}

function SidebarErrorFallback() {
  return (
    <div className="h-full bg-card flex items-center justify-center p-4">
      <div className="text-center space-y-2">
        <p className="text-sm font-medium text-destructive">Sidebar failed to load</p>
        <button
          onClick={() => window.location.reload()}
          className="text-xs text-primary hover:underline"
        >
          Reload page
        </button>
      </div>
    </div>
  );
}

interface LayoutProps {
  children: ReactNode;
  showSidebar?: boolean;
}

export function Layout({ children, showSidebar = true }: LayoutProps) {
  const sidebarOpen = useUIStore((s) => s.sidebarOpen);
  const sidebarWidth = useUIStore((s) => s.sidebarWidth);
  const setSidebarWidth = useUIStore((s) => s.setSidebarWidth);
  const { setOpen: setSidebarOpen } = useSidebarToggle();
  const isMobile = useMediaQuery("(max-width: 767px)");
  // The header's page-control slot, held in state so a page renders into it
  // once it has mounted (a ref would not re-render the page).
  const [headerSlot, setHeaderSlot] = useState<HTMLElement | null>(null);

  // Close sidebar on mobile on mount — drawer auto-opening is disorienting.
  // Ref guard keeps this as a mount-only reaction while still satisfying
  // exhaustive-deps, so later resizes don't slam the drawer shut mid-use.
  const didInitMobileCloseRef = useRef(false);
  useEffect(() => {
    if (didInitMobileCloseRef.current) return;
    didInitMobileCloseRef.current = true;
    if (isMobile) setSidebarOpen(false);
  }, [isMobile, setSidebarOpen]);

  // Lock body scroll when the mobile drawer is open so the page behind doesn't move.
  // Desktop ignores this — the sidebar is in-flow above 768px.
  useEffect(() => {
    if (!showSidebar || !sidebarOpen || !isMobile) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, [showSidebar, sidebarOpen, isMobile]);

  // Resize state — all kept in refs to avoid re-renders during drag
  const startXRef = useRef(0);
  const startWidthRef = useRef(360);
  const rafIdRef = useRef<number | null>(null);
  const setSidebarWidthRef = useRef(setSidebarWidth);
  const mouseHandlersRef = useRef<{ move: (e: MouseEvent) => void; up: () => void } | null>(null);

  useEffect(() => {
    setSidebarWidthRef.current = setSidebarWidth;
  }, [setSidebarWidth]);

  // Build stable handlers once on mount via effect
  useEffect(() => {
    const move = (e: MouseEvent) => {
      if (rafIdRef.current !== null) {
        cancelAnimationFrame(rafIdRef.current);
      }
      rafIdRef.current = requestAnimationFrame(() => {
        const newWidth = startWidthRef.current + (e.clientX - startXRef.current);
        if (newWidth >= 300 && newWidth <= 440) {
          setSidebarWidthRef.current(newWidth);
        }
        rafIdRef.current = null;
      });
    };

    const up = () => {
      if (rafIdRef.current !== null) {
        cancelAnimationFrame(rafIdRef.current);
        rafIdRef.current = null;
      }
      document.removeEventListener("mousemove", move);
      document.removeEventListener("mouseup", up);
    };

    mouseHandlersRef.current = { move, up };

    return () => {
      if (rafIdRef.current !== null) {
        cancelAnimationFrame(rafIdRef.current);
      }
      document.removeEventListener("mousemove", move);
      document.removeEventListener("mouseup", up);
    };
  }, []);

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    startXRef.current = e.clientX;
    startWidthRef.current = sidebarWidth;
    const handlers = mouseHandlersRef.current;
    if (handlers) {
      document.addEventListener("mousemove", handlers.move);
      document.addEventListener("mouseup", handlers.up);
    }
  }, [sidebarWidth]);

  return (
    <div className="h-dvh flex bg-[var(--app-canvas)] overflow-hidden md:p-(--app-chrome-gap) md:gap-(--app-chrome-gap)">
      {/* Skip to main content — accessible keyboard navigation */}
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:absolute focus:z-modal focus:top-2 focus:left-2 focus:rounded-md focus:bg-primary focus:text-primary-foreground focus:px-4 focus:py-2 focus:text-sm focus:font-medium focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
      >
        Skip to main content
      </a>

      {/* Left sidebar
          Desktop (md+): floating panel, user-resizable between 300–440px.
          Mobile (<md):  floating overlay drawer with tap-to-close backdrop.
          The icon strip is desktop-only chrome — mobile users toggle from AppHeader. */}
      {showSidebar && (
        sidebarOpen ? (
          <>
            {/* Mobile backdrop — tap to close.
                role=presentation + aria-hidden keeps assistive tech from
                announcing the entire backdrop as a "Close sidebar" button,
                which would shout over the drawer's own content. Keyboard
                users close via Cmd/Ctrl+B (AppHeader global shortcut). */}
            <div
              role="presentation"
              aria-hidden="true"
              onClick={() => setSidebarOpen(false)}
              className="md:hidden fixed inset-0 z-modal bg-background/70 backdrop-blur-sm animate-in fade-in duration-150"
            />
            <div
              className={cn(
                "fixed top-2 bottom-2 left-2 z-modal shrink-0 w-[min(85vw,22rem)] shadow-xl rounded-(--app-chrome-radius) overflow-hidden",
                "md:relative md:inset-auto md:z-auto md:w-[var(--sb-w)] md:shadow-none md:top-auto md:bottom-auto md:left-auto"
              )}
              style={{ ['--sb-w' as string]: `${sidebarWidth}px` } as React.CSSProperties}
            >
              <ErrorBoundary fallback={<SidebarErrorFallback />}>
                <Suspense fallback={<SidebarSkeleton />}>
                  <Sidebar />
                </Suspense>
              </ErrorBoundary>
            </div>
            {/* Resize handle — desktop only. Sits in the gap between sidebar
                and main panel. Zero-width flex item with an expanded hit area
                via ::before so the grip is grabbable without a visible divider. */}
            <div
              role="separator"
              aria-orientation="vertical"
              aria-label="Resize sidebar"
              tabIndex={0}
              className={cn(
                "hidden md:flex items-center justify-center w-0 relative cursor-col-resize shrink-0 outline-none",
                "before:content-[''] before:absolute before:inset-y-6 before:w-1 before:rounded-full before:transition-colors",
                "hover:before:bg-primary/20 focus-visible:before:bg-primary/30 focus-visible:ring-2 focus-visible:ring-ring"
              )}
              onMouseDown={handleMouseDown}
              onKeyDown={(e) => {
                if (e.key === "ArrowLeft") {
                  e.preventDefault();
                  setSidebarWidth(Math.max(300, sidebarWidth - 20));
                } else if (e.key === "ArrowRight") {
                  e.preventDefault();
                  setSidebarWidth(Math.min(440, sidebarWidth + 20));
                } else if (e.key === "Home") {
                  e.preventDefault();
                  setSidebarWidth(300);
                } else if (e.key === "End") {
                  e.preventDefault();
                  setSidebarWidth(440);
                }
              }}
            />
          </>
        ) : (
          <div className="hidden md:block">
            <LeftSidebarIconStrip />
          </div>
        )
      )}

      {/* Right column: floating panel with header + content */}
      <HeaderSlotContext.Provider value={headerSlot}>
        <div className="flex-1 flex flex-col min-w-0 md:rounded-(--app-chrome-radius) md:overflow-hidden bg-background">
          <AppHeader slotRef={setHeaderSlot} />
          <main id="main-content" className="flex-1 flex flex-col min-h-0">
            <ScrollContainer wrapperClassName="flex-1 min-w-0 min-h-0">
              {children}
            </ScrollContainer>
          </main>
        </div>
      </HeaderSlotContext.Provider>

      {/* Mobile bottom nav — floating persistent navigation for small screens */}
      {showSidebar && <MobileBottomNav />}

      {/* Global overlays — always mounted, self-managing visibility */}
      <CommandPalette />
      <KeyboardShortcutsModal />
    </div>
  );
}
