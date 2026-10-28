import { useEffect, useMemo, useState } from 'react';
import { Check, AlertCircle, RefreshCw, Loader2 } from 'lucide-react';
import { cn } from '@/lib/utils';
import { getLabels, useLabels } from '@/lib/i18n';
import { Button } from '@/components/ui/button';
import { EmojiMarker } from '@/components/vie';
import { DOMAIN_META } from '@/components/vie/cards/domain-meta';
import { accentStyle } from './output/lib/content-accent';
import { STREAM_PHASE_LABELS } from '@/features/video-output/hooks/use-summary-stream';
import { STAGE_INDEX, stagesFor, type StageDef } from '@/features/video-output/lib/stream-stages';
import type {
  StreamPhase,
  StreamPhaseDetail,
  ExtractionProgressInfo,
  FrameInfo,
} from '@/features/video-output/hooks/use-summary-stream';
import type { TriageResult } from '@vie/types';

interface StreamingPlaceholderProps {
  streamPhase?: StreamPhase;
  phaseDetail?: StreamPhaseDetail | null;
  extractionProgress?: ExtractionProgressInfo | null;
  triage?: TriageResult | null;
  frames?: FrameInfo[];
  thumbnailUrl?: string;
  tabCount?: number;
  tabLabels?: { id: string; label: string; emoji: string }[];
  language?: string;
  warnings?: string[];
  onCancel?: () => void;
}

/** Delay (ms) before the Cancel control fades in. The first seconds of
 *  streaming are the product's peak moment; an abort affordance shouldn't
 *  compete with it. Space is reserved from mount so the reveal never shifts
 *  layout. */
const CANCEL_REVEAL_MS = 5000;

/** Rotation interval for the feature spotlight. Slow enough to read twice. */
const SPOTLIGHT_INTERVAL_MS = 6500;

/** Shown on the metadata or transcript stage while the summarizer rotates proxy exits. */
const YOUTUBE_RETRYING = 'YouTube is rate-limiting, retrying';

const TRANSCRIPT_DETAIL: Record<StreamPhaseDetail, string> = {
  'captions-cached': 'Reusing a transcript we already have',
  'audio-transcription': 'No captions found, so the audio is being transcribed. This is the longest step.',
  'metadata-only': 'No captions available; working from the video details',
  'youtube-retrying': YOUTUBE_RETRYING,
};

interface Spotlight {
  emoji: string;
  title: string;
  text: string;
}

/** Shown before the tab plan is known — the product's standing promises. */
const GENERIC_SPOTLIGHTS: Spotlight[] = [
  { emoji: '⚡', title: 'Instant next time', text: 'Once processed, this video opens instantly: cached for you and every future viewer.' },
  { emoji: '🎯', title: 'Every claim has a timestamp', text: 'Tap any moment in the output to jump straight to that point in the video.' },
  { emoji: '🧠', title: 'Made to stick', text: 'Quizzes and flashcards are generated from this exact video, ready to drill.' },
  { emoji: '🖼️', title: 'Frames as evidence', text: 'Key frames are vision-analyzed and pinned to the steps they support.' },
  { emoji: '💬', title: 'Chat with the video', text: 'Ask questions once processing finishes; answers cite the transcript.' },
];

function formatElapsed(totalSeconds: number): string {
  const m = Math.floor(totalSeconds / 60);
  const s = totalSeconds % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

/** Wall-clock seconds since this placeholder mounted. Restarts on refresh —
 *  it measures the user's wait, not the pipeline's runtime. */
function useElapsedSeconds(): number {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const startedAt = Date.now();
    const timer = setInterval(() => {
      setElapsed(Math.floor((Date.now() - startedAt) / 1000));
    }, 1000);
    return () => clearInterval(timer);
  }, []);
  return elapsed;
}

export function StreamingPlaceholder({
  streamPhase = 'connecting',
  phaseDetail,
  extractionProgress,
  triage,
  frames = [],
  thumbnailUrl,
  tabCount = 0,
  tabLabels = [],
  language,
  warnings = [],
  onCancel,
}: StreamingPlaceholderProps) {
  const elapsed = useElapsedSeconds();

  const [showCancel, setShowCancel] = useState(false);
  useEffect(() => {
    if (!onCancel) return;
    const t = setTimeout(() => setShowCancel(true), CANCEL_REVEAL_MS);
    return () => clearTimeout(t);
  }, [onCancel]);

  const stages = stagesFor(streamPhase);
  const activeIndex = STAGE_INDEX[streamPhase] ?? 0;

  const spotlights = useMemo<Spotlight[]>(() => {
    const previews = getLabels(language ?? 'en').tabPreviews;
    const fromTabs = tabLabels
      .filter((t) => previews[t.id])
      .map((t) => ({ emoji: t.emoji, title: t.label, text: previews[t.id] }));
    return fromTabs.length > 0 ? fromTabs : GENERIC_SPOTLIGHTS;
  }, [tabLabels, language]);

  const [spotlightIndex, setSpotlightIndex] = useState(0);
  useEffect(() => {
    if (spotlights.length < 2) return;
    const t = setInterval(
      () => setSpotlightIndex((i) => (i + 1) % spotlights.length),
      SPOTLIGHT_INTERVAL_MS,
    );
    return () => clearInterval(t);
  }, [spotlights.length]);
  const spotlight = spotlights[spotlightIndex % spotlights.length];

  const recentFrames = frames.filter((f) => f.url).slice(-4);
  const latestWarning = warnings.length > 0 ? warnings[warnings.length - 1] : null;

  return (
    <section
      className="relative overflow-hidden rounded-2xl border border-border/60 bg-card"
      style={accentStyle(triage?.primaryTag)}
      aria-label="Processing progress"
    >
      <div aria-hidden="true" className="vie-stream-halo pointer-events-none absolute inset-0" />

      {/* Screen readers get the phase headline as a polite live region; the
          ticking clock stays out of it so it doesn't announce every second. */}
      <p className="sr-only" role="status" aria-live="polite">
        {STREAM_PHASE_LABELS[streamPhase]}
      </p>

      <div className="relative flex items-baseline justify-between gap-4 px-5 pt-5 sm:px-6">
        <span className="type-eyebrow text-[var(--vie-accent)]">Generating</span>
        <span className="text-xs tabular-nums text-muted-foreground" aria-hidden="true">
          {formatElapsed(elapsed)}
        </span>
      </div>

      <div className="relative flex flex-col gap-6 px-5 py-5 sm:px-6 md:flex-row md:gap-8">
        <StageTimeline
          stages={stages}
          activeIndex={activeIndex}
          phaseDetail={phaseDetail ?? null}
          extractionProgress={extractionProgress ?? null}
          triage={triage ?? null}
          tabCount={tabCount}
        />
        {(recentFrames.length > 0 || thumbnailUrl) && (
          <StreamVisuals frames={recentFrames} totalFrames={frames.length} thumbnailUrl={thumbnailUrl} />
        )}
      </div>

      {latestWarning && (
        <p className="relative px-5 pb-3 text-xs text-[var(--warning)] sm:px-6">{latestWarning}</p>
      )}

      <div className="relative flex items-start justify-between gap-4 border-t border-border/40 px-5 py-4 sm:px-6">
        <div className="min-w-0 flex-1">
          <p className="type-eyebrow text-muted-foreground">Coming up</p>
          <div className="mt-1.5 min-h-11">
            {spotlight && (
              <div
                key={spotlightIndex}
                className="flex items-start gap-2.5 animate-[fadeUp_0.35s_var(--ease-out-expo)_both] motion-reduce:animate-none"
              >
                <EmojiMarker emoji={spotlight.emoji} size="sm" animated={false} className="mt-px" />
                <p className="min-w-0 text-sm leading-snug text-foreground/85">
                  <span className="font-medium">{spotlight.title}.</span>{' '}
                  <span className="text-muted-foreground">{spotlight.text}</span>
                </p>
              </div>
            )}
          </div>
        </div>
        {onCancel && (
          <Button
            variant="ghost"
            size="sm"
            onClick={onCancel}
            tabIndex={showCancel ? 0 : -1}
            aria-hidden={!showCancel}
            className={cn(
              'shrink-0 self-end text-xs text-muted-foreground transition-opacity duration-300 hover:text-foreground',
              showCancel ? 'opacity-100' : 'pointer-events-none opacity-0',
            )}
          >
            Cancel
          </Button>
        )}
      </div>
    </section>
  );
}

interface StageTimelineProps {
  stages: StageDef[];
  activeIndex: number;
  phaseDetail: StreamPhaseDetail | null;
  extractionProgress: ExtractionProgressInfo | null;
  triage: TriageResult | null;
  tabCount: number;
}

function StageTimeline({
  stages,
  activeIndex,
  phaseDetail,
  extractionProgress,
  triage,
  tabCount,
}: StageTimelineProps) {
  return (
    <ol className="relative flex min-w-0 flex-1 flex-col gap-4">
      <span aria-hidden="true" className="absolute bottom-2.5 start-[9px] top-2.5 w-px bg-border/60" />
      {stages.map((stage, idx) => {
        const isComplete = idx < activeIndex;
        const isActive = idx === activeIndex;
        return (
          <li key={stage.id} className="relative flex items-start gap-3">
            <span
              className={cn(
                'relative z-10 mt-px flex size-5 shrink-0 items-center justify-center rounded-full border bg-card',
                isComplete && 'border-transparent bg-[var(--vie-accent)]/15',
                isActive && 'border-[var(--vie-accent)]',
                !isComplete && !isActive && 'border-border',
              )}
            >
              {isComplete ? (
                <Check className="size-3 text-[var(--vie-accent)]" aria-hidden="true" />
              ) : isActive ? (
                <span
                  aria-hidden="true"
                  className="size-2 rounded-full bg-[var(--vie-accent)] animate-[stage-pulse_1.6s_ease-in-out_infinite] motion-reduce:animate-none"
                />
              ) : null}
            </span>
            <div className="min-w-0 flex-1 pt-0.5">
              <p
                className={cn(
                  'text-sm leading-tight',
                  isActive
                    ? 'font-medium text-foreground'
                    : isComplete
                      ? 'text-muted-foreground'
                      : 'text-muted-foreground/60',
                )}
              >
                {stage.label}
              </p>
              {isActive && (
                <StageDetail
                  stageId={stage.id}
                  phaseDetail={phaseDetail}
                  extractionProgress={extractionProgress}
                  triage={triage}
                  tabCount={tabCount}
                />
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

interface StageDetailProps {
  stageId: StageDef['id'];
  phaseDetail: StreamPhaseDetail | null;
  extractionProgress: ExtractionProgressInfo | null;
  triage: TriageResult | null;
  tabCount: number;
}

function StageDetail({ stageId, phaseDetail, extractionProgress, triage, tabCount }: StageDetailProps) {
  if (phaseDetail === 'youtube-retrying' && stageId === 'metadata') {
    return <p className="mt-1 text-xs text-muted-foreground">{YOUTUBE_RETRYING}</p>;
  }
  if (stageId === 'transcript') {
    const text = phaseDetail ? TRANSCRIPT_DETAIL[phaseDetail] : 'Pulling the captions';
    return <p className="mt-1 text-xs text-muted-foreground">{text}</p>;
  }

  if (stageId === 'extraction') {
    const domainMeta = triage ? DOMAIN_META[triage.primaryTag] : undefined;
    const showBatch =
      !!extractionProgress && typeof extractionProgress.of === 'number' && extractionProgress.of > 1;
    const percent = Math.min(100, Math.max(0, extractionProgress?.percent ?? 0));
    const isSequential = extractionProgress?.section === 'chunked-sequential';
    return (
      <div className="mt-1.5 flex flex-col gap-1.5">
        {domainMeta && (
          <p className="text-xs text-muted-foreground">
            Detected:{' '}
            <span className="font-medium text-foreground/80">
              {domainMeta.emoji} {domainMeta.label}
            </span>
          </p>
        )}
        {extractionProgress && (
          <div className="flex items-center gap-2.5">
            <div
              className="h-1 w-full max-w-56 overflow-hidden rounded-full bg-muted/40"
              role="progressbar"
              aria-valuenow={percent}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-label="Extraction progress"
            >
              <div
                className="h-full w-full origin-left rounded-full bg-[var(--vie-accent)] transition-transform duration-500 ease-[var(--ease-out-quint)] rtl:origin-right"
                style={{ transform: `scaleX(${percent / 100})` }}
              />
            </div>
            {showBatch && (
              <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
                {extractionProgress.batch} of {extractionProgress.of}
              </span>
            )}
          </div>
        )}
        {isSequential && (
          <p className="text-xs text-muted-foreground/70">Running one section at a time</p>
        )}
      </div>
    );
  }

  if (stageId === 'building') {
    return (
      <p className="mt-1 text-xs tabular-nums text-muted-foreground">
        {tabCount > 0 ? `Assembling ${tabCount} tabs` : 'Assembling interactive tabs'}
      </p>
    );
  }

  return null;
}

interface StreamVisualsProps {
  frames: FrameInfo[];
  totalFrames: number;
  thumbnailUrl?: string;
}

/** The evidence column: the video's poster until vision frames start landing,
 *  then the latest analyzed frames. Real pipeline output, not decoration. */
function StreamVisuals({ frames, totalFrames, thumbnailUrl }: StreamVisualsProps) {
  return (
    <div className="w-full shrink-0 md:w-56">
      {frames.length > 0 ? (
        <>
          <div className="grid grid-cols-2 gap-1.5">
            {frames.map((frame) => (
              <img
                key={frame.index}
                src={frame.url}
                alt=""
                loading="lazy"
                draggable={false}
                className="aspect-video w-full rounded-md border border-border/40 object-cover"
              />
            ))}
          </div>
          <p className="mt-1.5 text-xs tabular-nums text-muted-foreground">
            {totalFrames} frames analyzed
          </p>
        </>
      ) : thumbnailUrl ? (
        <img
          src={thumbnailUrl}
          alt=""
          loading="lazy"
          draggable={false}
          className="aspect-video w-full rounded-lg border border-border/40 object-cover"
        />
      ) : null}
    </div>
  );
}

interface StreamErrorCardProps {
  message?: string | null;
  /** The server's code for the error. VIDEO_FAILED (the run had already failed) is worded in the page's language. */
  code?: string | null;
  onRetry?: () => void;
  retrying?: boolean;
}

/** The gateway's code for opening the stream of a video whose run already failed. */
const VIDEO_FAILED_CODE = 'VIDEO_FAILED';

/** Mid-stream failure card. Shown when the SSE stream reports an error before
 *  the video record flips to "failed" (which has its own full-page state). */
export function StreamErrorCard({ message, code, onRetry, retrying }: StreamErrorCardProps) {
  const { billing } = useLabels();
  const text = code === VIDEO_FAILED_CODE ? billing.videoFailedRetry : message;

  return (
    <section className="rounded-2xl border border-border/60 bg-card px-6 py-10 text-center">
      <AlertCircle className="mx-auto h-10 w-10 text-destructive" aria-hidden="true" />
      <h2 className="mt-4 text-lg font-semibold text-foreground">Processing hit a problem</h2>
      <p className="mx-auto mt-2 max-w-md text-sm text-muted-foreground text-pretty">
        {text || 'Something went wrong while processing this video.'}
      </p>
      {onRetry && (
        <div className="mt-5 flex justify-center">
          <Button onClick={onRetry} disabled={retrying}>
            {retrying ? (
              <Loader2 className="me-2 h-4 w-4 animate-spin motion-reduce:animate-none" />
            ) : (
              <RefreshCw className="me-2 h-4 w-4" />
            )}
            Retry
          </Button>
        </div>
      )}
    </section>
  );
}
