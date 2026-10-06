import { useEffect, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { Layout } from "@/components/layout/Layout";
import { useAllVideos } from "@/hooks/use-videos";
import { useIsAuthenticated } from "@/stores/auth-store";
import { VideoIntakeForm } from "@/features/video-output/components/VideoIntakeForm";
import { readSubmitUrl } from "@/lib/submit-url";
import { OnboardingValueProps } from "@/features/video-output/components/onboarding/OnboardingValueProps";
import { ExampleDisclosure } from "@/features/video-output/components/onboarding/ExampleDisclosure";

export function GeneratePage() {
  const isAuthenticated = useIsAuthenticated();
  const location = useLocation();
  const navigate = useNavigate();
  // Captured once: the effect below wipes the router state so Back or a
  // reload lands on an empty form instead of submitting the same URL again.
  const [submitUrl] = useState<string | undefined>(() => readSubmitUrl(location.state));

  useEffect(() => {
    if (!submitUrl || location.state === null) return;
    navigate(location.pathname, { replace: true, state: null });
  }, [submitUrl, location.state, location.pathname, navigate]);

  // Read cached video count to decide whether to render the full onboarding.
  // While the query is in-flight we mark the variant as "unknown" so we can
  // skip onboarding-only blocks — returning users would otherwise see chips
  // briefly appear then disappear (visible CLS) when their cache resolves.
  // Logged-out viewers go straight to onboarding (no query is dispatched).
  const { data, isPending } = useAllVideos({ enabled: isAuthenticated });
  const queryUnsettled = isAuthenticated && isPending;
  const isReturningUser = (data?.videos.length ?? 0) > 0;

  return (
    <Layout showSidebar>
      <div className="surface-ambient relative flex-1 flex flex-col items-center justify-center page-gutter page-gutter-y overflow-x-clip">
        {/* Ambient accent orb — quiet hero atmosphere behind the form. */}
        <div
          aria-hidden="true"
          className="vie-accent-orb"
          style={{
            ['--orb-size' as string]: '480px',
            bottom: '-120px',
            right: '-120px',
            background:
              'radial-gradient(closest-side, oklch(from var(--primary) l c h / 0.5), transparent 70%)',
          }}
        />

        <div className="relative max-w-2xl w-full stack-xl py-10">
          <div className="stack-sm text-center">
            {/* Eyebrow diverges from .type-eyebrow defaults: mono family + wider
                0.18em tracking dial in the "code stamp" feel for the hero copy.
                Color is muted-foreground/80 so it sits behind the title. */}
            <p className="type-eyebrow font-mono tracking-[0.18em] text-muted-foreground/80">
              <span className="inline-block h-1 w-1 rounded-full bg-primary align-middle me-2" />
              {/* Default to the returning-user copy while the query is in flight
                  so authenticated users don't see "Welcome" flash into "New summary". */}
              {queryUnsettled || isReturningUser ? 'New summary' : 'Welcome'}
            </p>
            <h1 className="type-page-title text-balance">
              {queryUnsettled || isReturningUser
                ? 'Turn a video into a study guide'
                : 'Turn any video into a study guide'}
            </h1>
            <p className="type-caption text-pretty max-w-md mx-auto">
              {queryUnsettled || isReturningUser
                ? "Paste a YouTube link and we'll break it down."
                : "Paste a YouTube link and we'll break it down — summary, key points, flashcards, and more."}
            </p>
          </div>

          <VideoIntakeForm initialUrl={submitUrl} />

          {queryUnsettled ? (
            // Suppress both branches while the query is in flight to avoid CLS
            // from the onboarding chips appearing then disappearing.
            <div aria-hidden="true" className="h-10" />
          ) : isReturningUser ? (
            <div className="text-center">
              <Link
                to="/board"
                className="inline-flex items-center gap-1.5 text-xs text-muted-foreground/80 hover:text-foreground transition-colors"
              >
                <ArrowLeft className="h-3.5 w-3.5 rtl:rotate-180" aria-hidden="true" />
                Back to your videos
              </Link>
            </div>
          ) : (
            <div className="flex flex-col gap-5">
              <OnboardingValueProps />
              <ExampleDisclosure />
            </div>
          )}
        </div>
      </div>
    </Layout>
  );
}
