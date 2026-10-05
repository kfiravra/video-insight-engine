import { useState } from "react";
import type { CSSProperties, FormEvent, ReactElement } from "react";
import { useNavigate, Navigate } from "react-router-dom";
import { ArrowRight, Loader2, Play } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useAuthStore } from "@/stores/auth-store";
import { useDemoEnabled } from "@/hooks/use-demo-enabled";
import { translateDemoError } from "@/lib/validation";
import { isYouTubeUrl } from "@/lib/youtube-utils";
import type { SubmitUrlState } from "@/lib/submit-url";
import { cn } from "@/lib/utils";

import { LandingHeader } from "@/components/landing/LandingHeader";
import { StreamingDemoLoop } from "@/components/landing/StreamingDemoLoop";
import { PreviewTimeline } from "@/components/landing/PreviewTimeline";
import { PreviewQuiz } from "@/components/landing/PreviewQuiz";
import { PreviewTakeaways } from "@/components/landing/PreviewTakeaways";

import "@/styles/landing.css";

/** A short, popular video with high-quality summary output — good first impression. */
const SAMPLE_VIDEO_URL =
  import.meta.env.VITE_SAMPLE_VIDEO_URL ??
  "https://www.youtube.com/watch?v=8jPQjjsBbIc";

export function LandingPage(): ReactElement {
  const [url, setUrl] = useState<string>("");
  const [starting, setStarting] = useState<boolean>(false);
  const [startError, setStartError] = useState<string>("");
  const navigate = useNavigate();
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const loginDemo = useAuthStore((s) => s.loginDemo);
  const demoEnabled = useDemoEnabled();

  // Authenticated users skip the funnel — straight to board. Held back while a
  // submit is in flight: the demo sign-in flips `isAuthenticated` before the
  // hand-off to /generate has navigated, and redirecting then drops the URL.
  if (isAuthenticated && !starting) {
    return <Navigate to="/board" replace />;
  }

  const trimmed = url.trim();
  const isInvalid = trimmed.length > 0 && !isYouTubeUrl(trimmed);

  /** Sends `targetUrl` to the intake form, signing in as the demo account
   *  first when the visitor has no session and the deployment offers one. */
  async function start(targetUrl: string): Promise<void> {
    // A stored token means a returning user whose auth check is still running;
    // never replace their session with the demo account.
    const hasSession = useAuthStore.getState().accessToken !== null;
    if (!hasSession && !demoEnabled) {
      // The login page forwards this to /generate once the visitor signs in.
      navigate("/login", { state: { submitUrl: targetUrl } satisfies SubmitUrlState });
      return;
    }

    setStartError("");
    // Stays true on success: this page remains mounted until /generate has
    // loaded, and clearing it would trigger the /board redirect above.
    setStarting(true);
    try {
      if (!hasSession) await loginDemo();
    } catch (err) {
      setStartError(translateDemoError(err));
      setStarting(false);
      return;
    }
    navigate("/generate", { state: { submitUrl: targetUrl } satisfies SubmitUrlState });
  }

  function handleSubmit(e: FormEvent): void {
    e.preventDefault();
    if (!trimmed || isInvalid || starting) return;
    void start(trimmed);
  }

  function handleSample(): void {
    if (starting) return;
    void start(SAMPLE_VIDEO_URL);
  }

  return (
    <div className="landing-shell landing-grid-paper min-h-dvh flex flex-col bg-background text-foreground">
      <div
        className="w-full mx-auto"
        style={{ maxWidth: "var(--landing-max)", paddingInline: "var(--landing-gutter)" }}
      >
        <LandingHeader />
      </div>

      <main
        className="flex-1 w-full mx-auto pb-24"
        style={{ maxWidth: "var(--landing-max)", paddingInline: "var(--landing-gutter)" }}
      >
        {/* ── Editorial hero: headline left, live demo right ── */}
        <section
          aria-labelledby="landing-headline"
          className="landing-hero-orbs pt-8 sm:pt-12 lg:pt-20"
        >
          <div className="landing-hero-grid">
            <div className="flex flex-col justify-center gap-8">
              <div>
                <div
                  className="landing-hero-enter inline-flex items-center gap-2 text-xs font-mono uppercase tracking-[0.15em] text-muted-foreground mb-6"
                  style={{ "--enter-delay": "0ms" } as CSSProperties}
                >
                  <span className="h-1 w-1 rounded-full bg-primary" aria-hidden="true" />
                  Watch less · Learn everything
                </div>
                <h1
                  id="landing-headline"
                  className="landing-hero-enter type-hero-xl text-balance text-foreground"
                  style={{ "--enter-delay": "120ms" } as CSSProperties}
                >
                  Any YouTube video,
                  <br />
                  <span className="text-foreground/85">
                    turned into an app you can use.
                  </span>
                </h1>
                <p
                  className="landing-hero-enter mt-6 max-w-xl text-lg leading-relaxed text-muted-foreground text-pretty"
                  style={{ "--enter-delay": "240ms" } as CSSProperties}
                >
                  Paste a link. VIE extracts the lecture, the recipe, the code,
                  the chapters — as structured, interactive surfaces. Not a
                  transcript. Not a summary. A thing you can study from.
                </p>
              </div>

              <form
                onSubmit={handleSubmit}
                className="landing-hero-enter flex flex-col gap-3 max-w-xl"
                style={{ "--enter-delay": "360ms" } as CSSProperties}
                noValidate
              >
                <div className="landing-input" data-invalid={isInvalid ? "true" : "false"}>
                  <Play
                    className="h-4 w-4 text-muted-foreground shrink-0"
                    aria-hidden="true"
                  />
                  <input
                    type="url"
                    value={url}
                    onChange={(e) => setUrl(e.target.value)}
                    placeholder="youtube.com/watch?v=…"
                    aria-label="YouTube video URL"
                    aria-invalid={isInvalid || undefined}
                    aria-describedby={isInvalid ? "landing-url-error" : undefined}
                  />
                  <Button
                    type="submit"
                    size="default"
                    className={cn(
                      "cta-magnetic rounded-[10px] px-5 font-semibold shrink-0 scale-pulse-once",
                    )}
                    style={{ animationDelay: "1.2s" }}
                    disabled={!trimmed || isInvalid || starting}
                  >
                    Summarize
                    {starting ? (
                      <Loader2 className="h-4 w-4 ms-1 animate-spin" aria-hidden="true" />
                    ) : (
                      <ArrowRight className="h-4 w-4 ms-1" aria-hidden="true" />
                    )}
                  </Button>
                </div>

                {isInvalid && (
                  <p
                    id="landing-url-error"
                    className="text-xs text-destructive"
                    role="alert"
                  >
                    That doesn&apos;t look like a YouTube link. Try a watch URL
                    like <span className="font-mono">youtube.com/watch?v=…</span>
                  </p>
                )}

                {startError && !isInvalid && (
                  <p className="text-xs text-destructive" role="alert">
                    {startError}
                  </p>
                )}

                <div className="flex items-center gap-3 text-sm">
                  <span className="text-muted-foreground">No URL handy?</span>
                  <button
                    type="button"
                    onClick={handleSample}
                    disabled={starting}
                    className="inline-flex items-center gap-1.5 rounded-full bg-muted/70 hover:bg-muted px-3 py-1 text-foreground font-medium transition-colors"
                  >
                    <Play
                      className="h-3 w-3 text-primary shrink-0"
                      aria-hidden="true"
                    />
                    Try a sample video
                  </button>
                </div>
              </form>
            </div>

            {/* Right column — the live streaming demo (hero asset) */}
            <div
              className="landing-hero-enter landing-demo-frame relative flex items-stretch w-full self-stretch"
              style={{ "--enter-delay": "300ms" } as CSSProperties}
            >
              <div className="w-full self-stretch">
                <StreamingDemoLoop tabPortalId="hero-tab-strip" />
              </div>
            </div>
          </div>

          {/* Tab strip — portaled here from StreamingDemoLoop */}
          <div
            id="hero-tab-strip"
            className="landing-hero-enter mt-6"
            style={{ "--enter-delay": "480ms" } as CSSProperties}
          />
        </section>

        {/* ── Proof section: 12-col bento ── */}
        <section
          aria-labelledby="showcase-heading"
          className="pt-20 sm:pt-28"
        >
          <div className="flex flex-col sm:flex-row sm:items-end sm:justify-between gap-4 mb-8">
            <div className="max-w-xl">
              <div className="inline-flex items-center gap-2 text-xs font-mono uppercase tracking-[0.15em] text-muted-foreground mb-3">
                <span className="h-1 w-1 rounded-full bg-primary" aria-hidden="true" />
                What actually comes out
              </div>
              <h2 className="font-display font-bold text-4xl md:text-5xl leading-[1.02] tracking-[-0.025em] text-foreground text-balance">
                Three tabs from one talk.
                <br />
                Real interactions, not screenshots.
              </h2>
            </div>
            <p className="max-w-sm text-sm text-muted-foreground text-pretty">
              Try the quiz. Scrub the timeline. These are the real components
              your processed videos render with — wired up, below, right now.
            </p>
          </div>

          <div className="landing-bento">
            <div className="landing-bento__timeline">
              <PreviewTimeline />
            </div>
            <div className="landing-bento__quiz">
              <PreviewQuiz />
            </div>
            <div className="landing-bento__takeaways">
              <PreviewTakeaways />
            </div>
          </div>
        </section>

        {/* ── Final CTA line ── */}
        <section className="pt-20 sm:pt-28">
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-6 py-10 border-t border-border/60">
            <div>
              <h2 className="type-page-title text-foreground">
                Paste a link. Get the app.
              </h2>
              <p className="text-muted-foreground mt-1.5">
                {demoEnabled
                  ? "Free to try. No account needed."
                  : "Free to try. Sign in takes a keystroke."}
              </p>
              <p className="mt-2 text-xs text-muted-foreground">
                No card needed · About a minute from paste to playable summary
              </p>
            </div>
            <div className="flex items-center gap-2">
              <Button size="lg" variant="ghost" onClick={handleSample} disabled={starting}>
                <Play className="h-4 w-4 me-1 text-primary" aria-hidden="true" />
                Try sample
              </Button>
            </div>
          </div>
        </section>
      </main>

      <footer
        className="w-full mx-auto py-8 flex items-center justify-between text-xs text-muted-foreground/70"
        style={{ maxWidth: "var(--landing-max)", paddingInline: "var(--landing-gutter)" }}
      >
        <span className="font-mono uppercase tracking-[0.15em]">Video Insight Engine</span>
      </footer>
    </div>
  );
}
