import { useParams, Link, useNavigate } from "react-router-dom";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useVideo, useRetryVideo } from "@/hooks/use-videos";
import { useSummaryStream } from "@/features/video-output/hooks/use-summary-stream";
import { useCelebrationTrigger } from "@/features/video-output/hooks/use-celebration-trigger";
import { useProcessingStore } from "@/features/video-output/stores/processing-store";
import { Layout } from "@/components/layout/Layout";
import { Button } from "@/components/ui/button";
import { ErrorBoundary } from "@/components/ui/error-boundary";
import { Loader2, ArrowLeft, RefreshCw, AlertCircle, PauseCircle } from "lucide-react";
import { OutputRouter } from "@/features/video-output/components/OutputRouter";
import { LanguageToggle } from "@/features/video-output/components/LanguageToggle";
import { DegradedNotice } from "@/features/video-output/components/DegradedNotice";
import { VideoPlayerProvider } from "@/features/video-output/contexts/VideoPlayerContext";

import { Confetti } from "@/components/ui/Confetti";
import { RetryErrorNotice } from "@/components/usage/RetryErrorNotice";
import { buildSynthesisFromMeta } from "@/features/video-output/lib/synthesis-utils";
import { shouldOpenStreamForStatus } from "@/features/video-output/lib/streaming/should-open-stream";
import { resolveDisplayTabs } from "@/features/video-output/lib/streaming/resolve-display-tabs";
import { isStreamSettled, type StreamTab } from "@/features/video-output/lib/streaming/stream-tabs";
import type { TabEntry } from "@vie/types";

// localStorage key for the cross-video language preference. Scoped under
// the `vie:` namespace so future preferences share a namespace and don't
// collide with third-party libraries dumping into root keys.
const LANGUAGE_PREFERENCE_KEY = "vie:prefersOriginalLang";

export function VideoDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { data, isLoading, error, refetch } = useVideo(id ?? "");
  const retryVideo = useRetryVideo();
  // True while the Retry on the stream-error card checks what state the video is in.
  const [checkingStream, setCheckingStream] = useState(false);

  // Extract data safely (may be undefined during loading/error)
  const video = data ?? null;

  // Frontend stream dedup: only open `/stream` when the cached video record
  // says processing is in flight. COMPLETED → render cached output, do NOT
  // re-open the stream (saves a round-trip + a backend "additional consumer"
  // attach log). FAILED renders the retry UI further down — no auto-stream.
  const isProcessing = shouldOpenStreamForStatus(video?.status);
  const videoSummaryId = video?.videoSummaryId || "";

  // Tell the processing manager to yield streaming to the page-level hook
  // (the manager aborts its stream when viewingVideoSummaryId is set)
  const setViewingVideo = useProcessingStore((s) => s.setViewingVideo);
  const markCancelled = useProcessingStore((s) => s.markCancelled);
  const clearCancelled = useProcessingStore((s) => s.clearCancelled);
  // Whether THIS video was explicitly cancelled in this tab. Survives nav so
  // the user doesn't auto-resubscribe to a stream they already walked away
  // from. Cleared on Resume / Retry.
  const isCancelled = useProcessingStore(
    (s) => (videoSummaryId ? s.cancelledIds.has(videoSummaryId) : false),
  );
  useEffect(() => {
    if (videoSummaryId) {
      setViewingVideo(videoSummaryId);
    }
    return () => {
      setViewingVideo(null);
    };
  }, [videoSummaryId, setViewingVideo]);

  // One settled handler for both outcomes (stable to avoid re-creation):
  // on complete, refetch swaps in the persisted doc; on error, refetch lets
  // the record's status flip to "failed" swap in the full-page failure UI
  // (until then OutputRouter shows the inline stream-error card).
  const handleStreamSettled = useCallback(() => {
    refetch();
  }, [refetch]);

  // Handle retry for failed videos
  const handleRetry = () => {
    if (!video?.youtubeId) return;
    retryVideo.mutate(
      { youtubeId: video.youtubeId, folderId: video.folderId },
      {
        onSuccess: (result) => {
          navigate(`/video/${result.video.id}`);
        },
      }
    );
  };

  // Use streaming hook when processing
  const {
    metadata: streamMetadata,
    duration: streamDuration,
    tabs: streamTabs,
    finalTabIds,
    replacedTabIds,
    meta: streamMeta,
    synthesis: streamSynthesis,
    tabCount,
    tabLabels,
    phase,
    phaseDetail,
    extractionProgress,
    triage: streamTriage,
    frames: streamFrames,
    warnings: streamWarnings,
    error: streamError,
    errorCode: streamErrorCode,
    confettiCount,
    degraded: streamDegraded,
    stop: stopStream,
    retry: retryStream,
  } = useSummaryStream({
    videoSummaryId,
    // Stream is enabled only when the backend says we're processing AND the
    // user hasn't explicitly cancelled in this tab. Without the cancel gate,
    // re-entering a video the user just cancelled would auto-resubscribe and
    // make the Cancel button feel decorative.
    enabled: isProcessing && !!videoSummaryId && !isCancelled,
    onComplete: handleStreamSettled,
    onError: handleStreamSettled,
  });

  // Retry on the stream-error card. A stream that stopped is either a dropped
  // connection to a run that is still going (reattach: no new charge, the hold
  // is already in place) or a run that failed. Re-opening the stream of a failed
  // video would run it again with no hold, so a failed video goes through the
  // billed submit instead, and a refusal shows like any other retry's. If the
  // state cannot be read (the connection is down), reattach; the stream
  // reports its own error again if it is still down.
  const handleStreamRetry = async () => {
    setCheckingStream(true);
    try {
      const { data: fresh } = await refetch();
      if (fresh?.status === "failed") {
        handleRetry();
      } else if (fresh?.status !== "completed") {
        retryStream();
      }
    } finally {
      setCheckingStream(false);
    }
  };

  // Cancel handler — flags the id as user-cancelled (so revisits show Resume
  // instead of re-attaching), aborts the local stream, and routes back to the
  // library. The backend pipeline keeps running; Resume reattaches when ready.
  const handleCancelStream = useCallback(() => {
    if (videoSummaryId) markCancelled(videoSummaryId);
    stopStream();
    navigate("/board");
  }, [stopStream, navigate, videoSummaryId, markCancelled]);

  // Resume handler — clears the cancel flag so the next render of this page
  // re-enables the stream subscription. No-op when the id isn't cancelled.
  const handleResumeStream = useCallback(() => {
    if (videoSummaryId) clearCancelled(videoSummaryId);
  }, [videoSummaryId, clearCancelled]);

  // Confetti: fires exactly once per video per session via the shared hook.
  // See use-celebration-trigger for the atomic per-id gating logic.
  const confettiTrigger = useCelebrationTrigger(videoSummaryId, confettiCount);

  // Merge streamed metadata into video object
  const mergedVideo = useMemo(() => {
    if (!video) return null;
    return {
      ...video,
      title: streamMetadata?.title || video.title,
      creator: streamMetadata?.channel || video.creator,
      thumbnailUrl: streamMetadata?.thumbnailUrl || video.thumbnailUrl,
      duration: streamDuration || video.duration,
    };
  }, [video, streamMetadata, streamDuration]);

  // Language view: top-level tabs/meta are always English-primary. The
  // sourceLanguage block, when present, carries the original-language
  // artifact. Default = English (top-level). Toggle reveals the original.
  // No special case for sound-only — sourceLanguage is simply absent there.
  const sourceLanguage = video?.sourceLanguage ?? null;
  const canToggleLanguage = !!sourceLanguage && sourceLanguage.tabs.length > 0;

  // Persist the language preference across videos and sessions. A Hebrew
  // or Arabic speaker who toggles to the original language once expects to
  // land on the original on every subsequent video and on every reload —
  // the previous "reset on id change" behaviour penalised exactly the users
  // the toggle was built for. localStorage holds the cross-video preference;
  // per-video sourceLanguage presence is enforced separately via
  // canToggleLanguage so English-only videos still render the EN view.
  const [showOriginal, setShowOriginal] = useState<boolean>(() => {
    try {
      return localStorage.getItem(LANGUAGE_PREFERENCE_KEY) === '1';
    } catch {
      // Private mode / storage disabled — fall back to English-primary.
      return false;
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem(LANGUAGE_PREFERENCE_KEY, showOriginal ? '1' : '0');
    } catch {
      // No-op — non-persistent preference is acceptable in restricted storage.
    }
  }, [showOriginal]);

  // While the stream is open, content is streaming-priority (always English-
  // shape during the pipeline; sourceLanguage isn't populated until the final
  // translation phase). Gating ``useOriginal`` on ``!isProcessing`` keeps the
  // page direction (displayIsRTL below) aligned with the streamed tabs/meta —
  // otherwise a Hebrew speaker with showOriginal=true persisted gets RTL
  // direction wrapping LTR-English streamed content.
  const useOriginal = !isProcessing && showOriginal && canToggleLanguage;

  // A completed DB doc beats an unsettled streamed set (a dropped connection
  // must not pin a subset); a settled live run — its final tabs, all final —
  // keeps its tab objects where the DB copy renders the same.
  const streamSettled = isStreamSettled({ tabs: streamTabs, finalTabIds });
  const resolvedTabs = useMemo((): StreamTab[] | null => {
    const englishTabs = video?.tabs;
    const dbTabs = useOriginal && sourceLanguage
      ? sourceLanguage.tabs
      : Array.isArray(englishTabs) && englishTabs.length > 0
        ? (englishTabs as TabEntry[])
        : null;
    // Streamed tabs are always English-shape; when the user is viewing the
    // original-language doc, the defensive stream-wins count comparison would
    // be cross-language — drop the stream side entirely in that mode.
    return resolveDisplayTabs({
      streamTabs: useOriginal ? [] : streamTabs,
      streamSettled: !useOriginal && streamSettled,
      dbTabs,
      videoStatus: video?.status,
    });
  }, [streamTabs, streamSettled, useOriginal, sourceLanguage, video?.tabs, video?.status]);

  const resolvedMeta = useMemo(() => {
    const dbMeta = useOriginal && sourceLanguage ? sourceLanguage.meta : (video?.meta ?? null);
    // Same precedence as tabs: a completed doc's meta is richer than the
    // plan-time stream meta.
    if (video?.status === "completed" && dbMeta) return dbMeta;
    if (streamMeta) return streamMeta;
    return dbMeta;
  }, [streamMeta, useOriginal, sourceLanguage, video?.meta, video?.status]);

  // Synthesis is always derived from meta — meta is the superset (carries
  // tldr / masterSummary / keyTakeaways / seoDescription). `resolvedMeta`
  // already routes to sourceLanguage.meta when the toggle is active, so
  // the derivation produces correct source-language synthesis automatically.
  const synthesis = useMemo(() => {
    if (streamSynthesis) return streamSynthesis;
    return buildSynthesisFromMeta(resolvedMeta);
  }, [streamSynthesis, resolvedMeta]);

  const displayLanguage = useOriginal && sourceLanguage
    ? sourceLanguage.code
    : (resolvedMeta?.language ?? "en");
  const displayIsRTL = useOriginal && sourceLanguage
    ? sourceLanguage.isRTL
    : (resolvedMeta?.isRTL ?? false);

  // Loading state
  if (isLoading) {
    return (
      <Layout>
        <div className="mx-auto flex min-h-[60vh] max-w-md flex-col items-center justify-center px-4 text-center">
          <div className="icon-glow mb-5">
            <Loader2
              className="h-14 w-14 animate-spin text-primary motion-reduce:animate-none"
              aria-hidden="true"
            />
          </div>
          <p className="type-eyebrow">Loading your video</p>
          <p className="type-caption mt-2 max-w-xs">
            Pulling the stream — a few seconds, tops.
          </p>
        </div>
      </Layout>
    );
  }

  // Error state
  if (error || !video || !mergedVideo) {
    return (
      <Layout>
        <div className="mx-auto flex min-h-[60vh] max-w-md flex-col items-center justify-center px-4 py-12 text-center">
          <div className="icon-glow mb-5">
            <AlertCircle
              className="h-14 w-14 text-destructive"
              aria-hidden="true"
            />
          </div>
          <h1 className="type-page-title text-balance">
            We couldn&apos;t load this video
          </h1>
          <p className="type-caption mt-3 max-w-sm text-pretty">
            It may have been deleted, or you might not have access. Try going
            back to your library.
          </p>
          <Link to="/board" className="mt-6">
            <Button variant="outline">
              <ArrowLeft className="me-2 h-4 w-4" /> Back to library
            </Button>
          </Link>
        </div>
      </Layout>
    );
  }

  // Failed state - show retry button + structured reasons
  if (video?.status === "failed") {
    const possibleCauses = [
      {
        title: "The video is private or unlisted",
        remedy: "Only public YouTube videos can be processed. Ask the creator to make it public, or try a different video.",
      },
      {
        title: "The video is age-restricted",
        remedy: "VIE can't access age-restricted content without signed-in cookies. Try an unrestricted alternative.",
      },
      {
        title: "The transcript language isn't supported yet",
        remedy: "We support most major languages. If the video has no captions or auto-captions, processing will fail.",
      },
      {
        title: "The video is very long",
        remedy: "Videos over 3 hours may time out. Try a shorter clip or a single section.",
      },
      {
        title: "Temporary network or AI service issue",
        remedy: "This is usually transient. Click Retry — it often works on the second attempt.",
      },
    ];

    return (
      <Layout>
        <div className="mx-auto max-w-xl px-4 py-12 md:px-6">
          <div className="flex flex-col items-center text-center">
            <div className="icon-glow mb-5">
              <AlertCircle
                className="h-14 w-14 text-destructive"
                aria-hidden="true"
              />
            </div>
            <h1 className="type-page-title text-balance">
              We couldn&apos;t summarize this video
            </h1>
            <p className="type-caption mt-3 max-w-md text-pretty">
              Something went wrong while processing. Here are the most likely
              reasons:
            </p>
          </div>

          <ul className="mt-8 mb-8 space-y-3">
            {possibleCauses.map((cause, index) => (
              <li
                key={cause.title}
                className="flex gap-3 rounded-xl border border-border/60 bg-card p-4 shadow-xs transition-shadow hover:shadow-sm"
              >
                <span
                  aria-hidden="true"
                  className="mt-0.5 inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-mono tabular-nums text-muted-foreground"
                >
                  {index + 1}
                </span>
                <div className="min-w-0">
                  <p className="text-base font-semibold text-foreground">
                    {cause.title}
                  </p>
                  <p className="type-caption mt-1">{cause.remedy}</p>
                </div>
              </li>
            ))}
          </ul>

          <div className="flex justify-center gap-3">
            <Button onClick={handleRetry} disabled={retryVideo.isPending}>
              {retryVideo.isPending ? (
                <Loader2 className="me-2 h-4 w-4 animate-spin motion-reduce:animate-none" />
              ) : (
                <RefreshCw className="me-2 h-4 w-4" />
              )}
              Retry
            </Button>
            <Link to="/board">
              <Button variant="outline">
                <ArrowLeft className="me-2 h-4 w-4" /> Back
              </Button>
            </Link>
          </div>
          <RetryErrorNotice error={retryVideo.error} className="mt-4 text-center" />
        </div>
      </Layout>
    );
  }

  const isStreaming = isProcessing &&
    !isCancelled &&
    phase !== "done" &&
    phase !== "cancelled" &&
    phase !== "error";

  // Partial result (project-score-9 3.6): the summarizer flags degraded runs
  // (dropped extraction batches / critical coverage) on the SSE terminal
  // events and on meta.degraded of persisted docs. Either source shows the
  // retry affordance once streaming has settled.
  const isDegraded = streamDegraded || resolvedMeta?.degraded === true;

  // User cancelled in this tab but the backend pipeline is still running.
  // Surface an explicit Resume control instead of silently re-attaching to
  // the SSE feed — that would make Cancel feel decorative.
  if (isProcessing && isCancelled) {
    return (
      <Layout>
        <div className="mx-auto flex min-h-[60vh] max-w-md flex-col items-center justify-center px-4 py-12 text-center">
          <div className="icon-glow mb-5">
            <PauseCircle
              className="h-14 w-14 text-primary"
              aria-hidden="true"
            />
          </div>
          <h1 className="type-page-title text-balance">
            You stopped watching this stream
          </h1>
          <p className="type-caption mt-3 max-w-sm text-pretty">
            Processing is still running in the background. Resume to reattach,
            or come back later — the video will be ready when you do.
          </p>
          <div className="mt-6 flex justify-center gap-3">
            <Button onClick={handleResumeStream}>
              <RefreshCw className="me-2 h-4 w-4" /> Resume
            </Button>
            <Link to="/board">
              <Button variant="outline">
                <ArrowLeft className="me-2 h-4 w-4" /> Back to library
              </Button>
            </Link>
          </div>
        </div>
      </Layout>
    );
  }

  // Issue #13: Error boundary fallback for rendering errors from malformed streaming state
  const errorFallback = (
    <Layout>
      <div className="mx-auto flex min-h-[60vh] max-w-md flex-col items-center justify-center px-4 py-12 text-center">
        <div className="icon-glow mb-5">
          <AlertCircle
            className="h-14 w-14 text-destructive"
            aria-hidden="true"
          />
        </div>
        <h1 className="type-page-title text-balance text-destructive">
          Something broke while rendering this video
        </h1>
        <Button
          variant="outline"
          className="mt-6"
          onClick={() => window.location.reload()}
        >
          <RefreshCw className="me-2 h-4 w-4" /> Reload page
        </Button>
      </div>
    </Layout>
  );

  return (
    <ErrorBoundary key={id} fallback={errorFallback}>
      <VideoPlayerProvider>
        <Layout>
          <RetryErrorNotice error={retryVideo.error} className="mx-auto max-w-2xl px-4" />
          {isDegraded && !isStreaming && (
            <DegradedNotice
              onRetry={handleRetry}
              retrying={retryVideo.isPending}
            />
          )}
          <OutputRouter
            title={resolvedMeta?.videoTitle || mergedVideo.title}
            videoSummaryId={videoSummaryId}
            tabs={resolvedTabs}
            meta={resolvedMeta}
            synthesis={synthesis}
            isStreaming={isStreaming}
            tabCount={tabCount}
            tabLabels={tabLabels}
            tabsSettled={streamSettled}
            replacedTabIds={replacedTabIds}
            youtubeId={video.youtubeId}
            creator={mergedVideo.creator ?? undefined}
            duration={mergedVideo.duration}
            language={displayLanguage}
            isRTL={displayIsRTL}
            streamPhase={phase}
            phaseDetail={phaseDetail}
            extractionProgress={extractionProgress}
            triage={streamTriage}
            frames={streamFrames}
            thumbnailUrl={mergedVideo.thumbnailUrl ?? undefined}
            warnings={streamWarnings}
            streamError={phase === "error" ? streamError : null}
            streamErrorCode={phase === "error" ? streamErrorCode : null}
            // A stream error (often a dropped connection) reattaches to a run that
            // is still going; a failed video is retried through the billed submit.
            onRetryStream={handleStreamRetry}
            retryingStream={checkingStream || retryVideo.isPending}
            onCancelStream={isStreaming ? handleCancelStream : undefined}
            languageToggle={
              canToggleLanguage && sourceLanguage ? (
                <LanguageToggle
                  showOriginal={showOriginal}
                  onChange={setShowOriginal}
                  originalName={sourceLanguage.name}
                  originalCode={sourceLanguage.code}
                />
              ) : null
            }
          />
          <Confetti trigger={confettiTrigger} />
        </Layout>
      </VideoPlayerProvider>
    </ErrorBoundary>
  );
}

