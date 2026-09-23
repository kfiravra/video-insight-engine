import { Component, useEffect, useMemo, type ReactNode } from "react";
import { useParams, Link } from "react-router-dom";
import { ArrowRight, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { VieLogotype } from "@/components/brand/VieMark";
import { useShareOutput } from "@/hooks/use-share";
import { useRegistrationOpen } from "@/hooks/use-registration-open";
import { OutputRouter } from "@/features/video-output/components/OutputRouter";
import { buildSynthesisFromMeta } from "@/features/video-output/lib/synthesis-utils";
import type { TabEntry } from "@vie/types";

/** Error boundary for shared content — a malformed block shouldn't crash the page. */
class ShareContentBoundary extends Component<
  { children: ReactNode },
  { hasError: boolean }
> {
  state = { hasError: false };

  static getDerivedStateFromError() {
    return { hasError: true };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    if (import.meta.env.DEV) {
      console.error("[ShareContentBoundary] Block render error:", error, info.componentStack);
    }
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="text-center py-8 text-muted-foreground">
          <p>Some content could not be displayed.</p>
        </div>
      );
    }
    return this.props.children;
  }
}

export function SharePage() {
  const { slug } = useParams<{ slug: string }>();
  const { data: shareData, isLoading, error } = useShareOutput(slug ?? "");
  const registrationOpen = useRegistrationOpen();

  // Set document title for social sharing / SEO
  useEffect(() => {
    if (shareData?.title) {
      document.title = `${String(shareData.title).slice(0, 200)} | VIE`;
    }
    return () => {
      document.title = "Video Insight Engine";
    };
  }, [shareData?.title]);

  // Shared output pages are the viral surface — render the English-primary
  // top-level tabs (always present post-translation). The source-language
  // nested block is intentionally ignored on share pages: anonymous visitors
  // can't reasonably opt into a language they don't read.
  const tabs = useMemo((): TabEntry[] | null => {
    const topTabs = shareData?.tabs;
    return Array.isArray(topTabs) && topTabs.length > 0
      ? (topTabs as TabEntry[])
      : null;
  }, [shareData?.tabs]);

  const meta = useMemo(() => shareData?.meta ?? null, [shareData?.meta]);

  const synthesis = useMemo(() => buildSynthesisFromMeta(meta), [meta]);

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center" role="status">
        <Loader2 className="h-8 w-8 animate-spin text-primary" />
        <span className="sr-only">Loading shared content...</span>
      </div>
    );
  }

  if (error || !shareData) {
    return (
      <div className="surface-ambient relative min-h-dvh flex flex-col items-center justify-center gap-6 px-4 overflow-hidden">
        <div className="stack-sm text-center max-w-md">
          {/* Eyebrow diverges from .type-eyebrow defaults: mono family + wider
              0.18em tracking match the Generate page hero stamp. */}
          <p className="type-eyebrow font-mono tracking-[0.18em] text-muted-foreground/80">
            <span className="inline-block h-1 w-1 rounded-full bg-primary align-middle me-2" />
            404 · Share not found
          </p>
          <h1 className="type-page-title text-balance">This share link no longer works</h1>
          <p className="type-caption text-pretty">
            The owner may have unshared it, or the URL was mistyped. VIE turns
            long YouTube videos into structured study guides — see what one
            looks like.
          </p>
        </div>
        <div className="flex flex-wrap justify-center gap-2">
          <Button asChild>
            <Link to="/">
              See a sample video <ArrowRight className="h-4 w-4 ms-1 rtl:rotate-180" />
            </Link>
          </Button>
          {registrationOpen && (
            <Button variant="outline" asChild>
              <Link to="/register">Create a free account</Link>
            </Button>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="surface-ambient relative min-h-dvh flex flex-col">
      {/* Floating panel header — matches the rounded-chrome language of the
          authenticated app shell so visitors don't land on a visibly different
          product. Contained max-width keeps the eye on content, not chrome. */}
      <header className="sticky top-0 z-20 backdrop-blur-[12px] bg-background/70 border-b border-border/40">
        <div className="mx-auto max-w-6xl flex items-center justify-between px-4 sm:px-6 py-3">
          <Link
            to="/"
            className="flex items-center gap-2.5 hover:opacity-80 transition-opacity"
            aria-label="VIE home"
          >
            <VieLogotype size="md" animated />
            {/* Divergence from .type-eyebrow: mono family + 0.15em tracking
                for the secondary "shared with you" stamp next to the logotype. */}
            <span
              className="type-eyebrow hidden sm:inline-flex items-center font-mono tracking-[0.15em] ms-1"
              aria-hidden="true"
            >
              · shared with you
            </span>
          </Link>
          {registrationOpen && (
            <Button size="sm" asChild>
              <Link to="/register">
                Try VIE free <ArrowRight className="h-3 w-3 ms-1 rtl:rotate-180" />
              </Link>
            </Button>
          )}
        </div>
      </header>

      {/* Shared content */}
      <main className="flex-1 relative z-10">
        <ShareContentBoundary>
          {tabs && tabs.length > 0 ? (
            <OutputRouter
              title={shareData.title}
              videoSummaryId={shareData.id}
              tabs={tabs}
              meta={meta}
              synthesis={synthesis}
              language={meta?.language}
              isRTL={meta?.isRTL}
            />
          ) : (
            <div className="max-w-3xl mx-auto w-full px-4 py-10">
              <div className="glass rounded-2xl p-6 sm:p-7 space-y-3">
                <h1 className="type-page-title text-balance">{shareData.title}</h1>
                {typeof meta?.tldr === 'string' && meta.tldr && (
                  <p className="text-base text-foreground/90 leading-relaxed text-pretty">
                    {meta.tldr}
                  </p>
                )}
              </div>
              <p className="type-eyebrow mt-6 text-center text-muted-foreground/70">
                Shared via Video Insight Engine
              </p>
            </div>
          )}
        </ShareContentBoundary>
      </main>
    </div>
  );
}
