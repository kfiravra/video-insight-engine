import { memo } from 'react';
import { Check } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { STREAM_PHASE_LABELS, type StreamPhase } from '@/features/video-output/hooks/use-summary-stream';
import { STAGE_INDEX, stagesFor, type StageDef } from '@/features/video-output/lib/stream-stages';

type StageState = 'complete' | 'active' | 'pending';

function stageState(index: number, activeIndex: number): StageState {
  if (index < activeIndex) return 'complete';
  return index === activeIndex ? 'active' : 'pending';
}

function CompactStage({ stage, state }: { stage: StageDef; state: StageState }) {
  return (
    <li
      aria-current={state === 'active' ? 'step' : undefined}
      className={cn(
        'flex shrink-0 items-center gap-1.5 text-xs',
        state === 'active' && 'font-medium text-foreground',
        state === 'complete' && 'text-muted-foreground max-sm:hidden',
        state === 'pending' && 'text-muted-foreground/50 max-sm:hidden',
      )}
    >
      {state === 'complete' ? (
        <Check className="size-3 text-[var(--vie-accent)]" aria-hidden="true" />
      ) : (
        <span
          aria-hidden="true"
          className={cn(
            'size-1.5 rounded-full',
            state === 'active'
              ? 'bg-[var(--vie-accent)] animate-[stage-pulse_1.6s_ease-in-out_infinite] motion-reduce:animate-none'
              : 'border border-muted-foreground/40',
          )}
        />
      )}
      <span>{stage.shortLabel}</span>
    </li>
  );
}

/** The pipeline stages in one line; only the active one shows on phones. */
function StageRow({ phase }: { phase: StreamPhase }) {
  const activeIndex = STAGE_INDEX[phase];
  return (
    <ol aria-label="Processing stages" className="flex min-w-0 flex-1 items-center justify-end gap-x-3.5 overflow-hidden">
      {stagesFor(phase).map((stage, idx) => (
        <CompactStage key={stage.id} stage={stage} state={stageState(idx, activeIndex)} />
      ))}
    </ol>
  );
}

/** Progress arc: fill scales on tab arrival (transform, never width —
 *  compositor-only); a travelling shimmer reads the wait as ambient activity.
 *  Reduced-motion strips the shimmer and keeps the transition. */
function TabProgressBar({ renderedCount, expectedCount }: { renderedCount: number; expectedCount: number }) {
  const progressPercent = expectedCount > 0
    ? Math.min(100, Math.max(0, (renderedCount / expectedCount) * 100))
    : 0;
  return (
    <div
      className="relative h-0.5 w-full overflow-hidden rounded-full bg-muted/40"
      role="progressbar"
      aria-valuenow={renderedCount}
      aria-valuemin={0}
      aria-valuemax={expectedCount}
      aria-label="Tabs generated"
    >
      <div
        className="relative h-full w-full origin-left overflow-hidden rounded-full rtl:origin-right"
        style={{
          transform: `scaleX(${progressPercent / 100})`,
          background: 'var(--vie-accent, var(--primary))',
          transition: 'transform 600ms var(--ease-out-quint)',
        }}
      >
        <div
          aria-hidden="true"
          className="progress-shimmer absolute inset-y-0 left-0 w-full motion-reduce:hidden"
          style={{
            background: 'linear-gradient(90deg, transparent 0%, oklch(100% 0 0 / 0.5) 50%, transparent 100%)',
          }}
        />
      </div>
    </div>
  );
}

interface TabProgressStripProps {
  renderedCount: number;
  expectedCount: number;
  streamPhase?: StreamPhase;
  /** Abort the in-flight stream. */
  onCancel?: () => void;
}

/**
 * The live-run row above the tab panel: "Generating N of M", the pipeline
 * stages in one compact line and Cancel — so while tabs already stream in,
 * the user still sees where the run is and can still walk away from it.
 * Rendered only while the stream is live.
 */
export const TabProgressStrip = memo(function TabProgressStrip({
  renderedCount,
  expectedCount,
  streamPhase,
  onCancel,
}: TabProgressStripProps) {
  return (
    <div className="flex flex-col gap-1.5 px-1 pt-1 animate-[fadeUp_0.35s_var(--ease-out-expo)_both]">
      {streamPhase && (
        <p className="sr-only" role="status" aria-live="polite">
          {STREAM_PHASE_LABELS[streamPhase]}
        </p>
      )}
      <div className="flex min-h-8 items-center justify-between gap-4">
        <div className="flex shrink-0 items-baseline gap-2.5">
          <span className="type-eyebrow text-[var(--vie-accent)]">Generating</span>
          <span className="text-xs font-medium tabular-nums text-muted-foreground">
            {renderedCount} <span className="text-muted-foreground/50">of</span> {expectedCount}
          </span>
        </div>
        {streamPhase && <StageRow phase={streamPhase} />}
        {onCancel && (
          <Button
            variant="ghost"
            size="sm"
            onClick={onCancel}
            aria-label="Cancel processing"
            // 28 px visual height; the pseudo-element extends the tap target
            // to 44 px without growing the row.
            className="relative h-7 shrink-0 px-2 text-xs text-muted-foreground hover:text-foreground before:absolute before:inset-x-0 before:-inset-y-2 before:content-['']"
          >
            Cancel
          </Button>
        )}
      </div>
      <TabProgressBar renderedCount={renderedCount} expectedCount={expectedCount} />
    </div>
  );
});
