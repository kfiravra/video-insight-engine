import { memo, useState } from 'react';
import { Check, AlertTriangle, Lightbulb, Clock, ListOrdered } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { GlassCard, FadeIn, Timer, BackForward, Stepper, Timestamp, TextBlock, VisualEvidence } from '@/components/vie';
import { EmptyTabState } from './EmptyTabState';
import { Celebration } from '../Celebration';
import { useLabels } from '@/lib/i18n';

import { useTabState } from '@/features/video-output/contexts/TabStateContext';
import { useTabCoordination } from '../TabCoordinationContext';
import { parseStepDurationSeconds } from '@/features/video-output/lib/step-duration';
import type { StepItem } from '@vie/types';

interface StepByStepInteractiveProps {
  steps: StepItem[];
  mode?: 'scrollable' | 'one_at_a_time';
  timers?: boolean;
  tabId?: string;
  nextTab?: string;
  onNavigateTab?: (id: string) => void;
  onSeek?: (seconds: number) => void;
}

/**
 * Single-accent step badge styling (Color discipline — one hue per tab).
 * Steps are distinguished by FILL vs OUTLINE vs neutral state, never by hue.
 */
function stepBadgeClass(isActive: boolean): string {
  return isActive
    ? 'border-[var(--vie-accent)] bg-[var(--vie-accent)] text-[var(--vie-accent-foreground)]'
    : 'border-border bg-muted text-muted-foreground';
}

export const StepByStepInteractive = memo(function StepByStepInteractive({
  steps,
  mode = 'scrollable',
  timers,
  tabId = 'steps',
  nextTab,
  onNavigateTab,
  onSeek,
}: StepByStepInteractiveProps) {
  const t = useLabels();
  const [currentStep, setCurrentStep] = useState(0);
  const [completedSteps, setCompletedSteps] = useState<Set<number>>(new Set());
  const tabState = useTabState();
  const tabCoord = useTabCoordination();

  const allDone = completedSteps.size === steps.length && steps.length > 0;

  const toggleComplete = (index: number) => {
    setCompletedSteps((prev) => {
      const next = new Set(prev);
      if (next.has(index)) {
        next.delete(index);
        tabState.uncompleteStep(index);
      } else {
        next.add(index);
        tabState.completeStep(index);
        const nextIncomplete = steps.findIndex((_, i) => i > index && !next.has(i));
        if (nextIncomplete !== -1) setCurrentStep(nextIncomplete);
      }
      // Check if all steps done
      if (next.size === steps.length && steps.length > 0) {
        tabCoord?.markTabCompleted(tabId);
      }
      return next;
    });
  };

  if (steps.length === 0) return <EmptyTabState message="No steps were extracted for this video." icon={ListOrdered} />;

  // Default to one-at-a-time for manageable step counts
  const effectiveMode = mode ?? (steps.length <= 10 ? 'one_at_a_time' : 'scrollable');
  const isOneAtATime = effectiveMode === 'one_at_a_time';
  const visibleSteps = isOneAtATime ? [{ step: steps[currentStep], index: currentStep }] : steps.map((step, index) => ({ step, index }));

  return (
    <div className="space-y-4">
      {/* Step X of Y header for one-at-a-time */}
      {isOneAtATime && (
        <div className="text-center text-xs font-medium text-muted-foreground">
          Step {currentStep + 1} of {steps.length}
        </div>
      )}

      {/* Progress track with milestones */}
      <div className="space-y-2">
        <div className="flex items-center justify-between text-xs text-muted-foreground">
          <span>Progress</span>
          <span>{completedSteps.size}/{steps.length} steps</span>
        </div>
        <div className="relative">
          {/* Track */}
          <div className="relative h-2 w-full rounded-full bg-muted/30 overflow-hidden">
            <div
              className="h-full bg-[color:var(--vie-accent)] transition-all duration-500 motion-reduce:transition-none"
              style={{ width: `${steps.length > 0 ? ((currentStep + 1) / steps.length) * 100 : 0}%` }}
            />
          </div>
          {/* Milestone dots */}
          {steps.length > 1 && steps.length <= 20 && (
            <div className="absolute inset-x-0 -top-1 h-4 pointer-events-none">
              {steps.map((_, i) => {
                const leftPercent = steps.length === 1 ? 50 : (i / (steps.length - 1)) * 100;
                const isDone = completedSteps.has(i);
                const isCurrent = i === currentStep;
                return (
                  <span
                    key={i}
                    className={cn(
                      'absolute top-0 -translate-x-1/2 w-2 h-2 rounded-full border transition-all duration-200 motion-reduce:transition-none',
                      isDone
                        ? 'bg-[color:var(--vie-accent)] border-[color:var(--vie-accent)] scale-110'
                        : isCurrent
                          ? 'bg-background border-[color:var(--vie-accent)] ring-2 ring-[color:var(--vie-accent-muted)]'
                          : 'bg-background border-muted-foreground/30',
                    )}
                    style={{ left: `${leftPercent}%` }}
                    aria-hidden="true"
                  />
                );
              })}
            </div>
          )}
        </div>
      </div>

      {/* One-at-a-time navigation */}
      {isOneAtATime && steps.length > 1 && (
        <BackForward
          onBack={() => setCurrentStep((p) => p - 1)}
          onForward={() => setCurrentStep((p) => p + 1)}
          backDisabled={currentStep === 0}
          forwardDisabled={currentStep === steps.length - 1}
          backLabel={t.previous}
          forwardLabel={t.next}
        />
      )}

      {/* Step list */}
      <div className="space-y-3">
        {visibleSteps.map(({ step, index }, fadeIdx) => {
          if (!step) return null;
          const isActive = index === currentStep;
          const isDone = completedSteps.has(index);
          const durationSecs = parseStepDurationSeconds(step.duration);

          return (
            <FadeIn key={index} index={fadeIdx}>
              <GlassCard
                variant={isActive ? 'interactive' : 'default'}
                className={cn(
                  'cursor-pointer transition duration-200 overflow-hidden',
                  isActive && 'border-[var(--vie-accent)]/40',
                  isDone && 'opacity-60',
                )}
              >
                {/* Frame-as-Hero: the supporting frame leads the step full-width
                    at aspect-video (DESIGN.md §6), never a sidekick thumbnail. */}
                {step.thumbnailUrl && (
                  <VisualEvidence
                    variant="figure"
                    thumbnailUrl={step.thumbnailUrl}
                    caption={step.frameCaption}
                    ocr={step.frameOcr}
                    className="mb-3"
                  />
                )}

                <div
                  className="flex gap-3"
                  onClick={() => setCurrentStep(index)}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      setCurrentStep(index);
                    }
                  }}
                >
                  {/* Number circle / check — single accent, fill vs outline vs neutral */}
                  <div
                    className={cn(
                      'shrink-0 w-7 h-7 rounded-full border-2 flex items-center justify-center text-xs font-bold transition-colors',
                      isDone
                        ? 'border-[var(--vie-accent)] bg-[var(--vie-accent)]/15 text-[var(--vie-accent)]'
                        : stepBadgeClass(isActive),
                    )}
                  >
                    {isDone ? <Check className="h-4 w-4" /> : step.number}
                  </div>

                  <div className="flex-1 min-w-0 space-y-1.5">
                    {step.title && (
                      <h4 className={cn('text-sm font-medium', isDone && 'line-through')}>
                        {step.title}
                      </h4>
                    )}
                    <p className={cn(
                      'text-sm text-muted-foreground',
                      isDone && 'line-through',
                    )}>
                      {step.instruction}
                    </p>

                    <div className="flex items-center gap-3 flex-wrap">
                      {step.duration && (
                        <span className="inline-flex items-center gap-1 text-xs text-muted-foreground/70 bg-muted/50 px-2 py-0.5 rounded">
                          <Clock className="h-3 w-3" aria-hidden="true" />
                          {step.duration}
                        </span>
                      )}
                      {step.timestamp != null && onSeek && (
                        <Timestamp seconds={step.timestamp} onClick={() => onSeek(step.timestamp!)} />
                      )}
                    </div>

                    {/* Timer integration */}
                    {timers && durationSecs > 0 && isActive && !isDone && (
                      <Timer
                        duration={durationSecs}
                        onComplete={() => toggleComplete(index)}
                        className="mt-2"
                      />
                    )}

                    {step.tips && (
                      <TextBlock intent="tip" icon={<Lightbulb />} className="mt-1">
                        {step.tips}
                      </TextBlock>
                    )}

                    {step.safetyNote && (
                      <TextBlock intent="warning" icon={<AlertTriangle />} className="mt-1">
                        {step.safetyNote}
                      </TextBlock>
                    )}
                  </div>

                  {/* Complete button */}
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={(e) => { e.stopPropagation(); toggleComplete(index); }}
                    className={cn('shrink-0 rounded-full', isDone && 'text-[var(--vie-accent)] hover:text-[var(--vie-accent)]')}
                    aria-label={isDone ? `Mark step ${step.number} incomplete` : `Mark step ${step.number} complete`}
                  >
                    <Check className="h-4 w-4" />
                  </Button>
                </div>
              </GlassCard>
            </FadeIn>
          );
        })}
      </div>

      {/* Stepper dots for one-at-a-time */}
      {isOneAtATime && steps.length > 1 && (
        <Stepper
          total={steps.length}
          current={currentStep}
          completedSteps={completedSteps}
          onStepClick={setCurrentStep}
        />
      )}

      {allDone && (
        <Celebration
          emoji="🎉"
          title="All steps complete!"
          subtitle="Great job following through."
          nextTabId={nextTab}
          nextLabel={nextTab ? 'Continue' : undefined}
          onNavigateTab={onNavigateTab}
        />
      )}

    </div>
  );
});
