import { memo, useCallback, useState } from 'react';
import { AlertTriangle, Lightbulb, Workflow } from 'lucide-react';

import { CanvasViewToggle } from '@/components/vie/canvas/CanvasShell';
import { GlassCard, SeekChip, TextBlock, VisualEvidence } from '@/components/vie';
import { cn } from '@/lib/utils';
import { useTabState } from '@/features/video-output/contexts/TabStateContext';
import { parseStepDurationSeconds } from '@/features/video-output/lib/step-duration';
import type { StepItem } from '@vie/types';

import { EmptyTabState } from './EmptyTabState';
import { CompleteToggle, DurationChip, StepNumber, type StepData } from './StepFlowChrome';
import { StepFlowMap } from './StepFlowMap';

// ─── Props ───

interface StepFlowCanvasProps {
  steps: StepItem[];
  onSeek?: (seconds: number) => void;
  tabId?: string;
}

type StepView = 'list' | 'flow';
const VIEW_OPTIONS = [
  { value: 'list', label: 'List' },
  { value: 'flow', label: 'Flow' },
] as const satisfies ReadonlyArray<{ value: StepView; label: string }>;

// ─── List row ───

/**
 * One step with its whole instruction, tips and safety note inline, plus the
 * duration and seek chips — the first screen reads without a click (R8).
 * Carries the same `data-slot` as the flow node: it is the step node, listed.
 */
const StepRow = memo(function StepRow({ step, index, completed, durationSeconds, onToggle, onSeek }: StepData) {
  const { timestamp } = step;
  const framed = Boolean(step.thumbnailUrl || step.frameCaption || step.frameOcr);
  return (
    <li data-slot="vie-step-node" data-view="list">
      <GlassCard variant={completed ? 'subtle' : 'default'} className={cn('p-3', completed && 'opacity-80')}>
        <div className="flex items-start gap-3">
          <StepNumber number={step.number ?? index + 1} completed={completed} />
          <div className="flex-1 min-w-0 space-y-2">
            {step.title && (
              <h4 className={cn('text-sm font-semibold leading-snug', completed && 'line-through')}>
                {step.title}
              </h4>
            )}
            <p className={cn('text-sm leading-snug text-foreground/90', completed && 'line-through')}>
              {step.instruction}
            </p>
            <div className="flex flex-wrap items-center gap-2">
              <DurationChip seconds={durationSeconds} />
              {!framed && timestamp != null && <SeekChip seconds={timestamp} onSeek={onSeek} />}
            </div>
            {framed && (
              <VisualEvidence
                variant="compact"
                thumbnailUrl={step.thumbnailUrl}
                caption={step.frameCaption}
                ocr={step.frameOcr}
                sceneType={step.frameSceneType}
                timestamp={timestamp}
                onSeek={onSeek}
              />
            )}
            {step.tips && (
              <TextBlock intent="tip" icon={<Lightbulb />}>
                {step.tips}
              </TextBlock>
            )}
            {step.safetyNote && (
              <TextBlock intent="warning" icon={<AlertTriangle />}>
                {step.safetyNote}
              </TextBlock>
            )}
            <CompleteToggle stepNumber={step.number ?? index + 1} index={index} completed={completed} onToggle={onToggle} />
          </div>
        </div>
      </GlassCard>
    </li>
  );
});

// ─── Canvas ───

/** Completed steps of THIS list: the shared set is keyed by index across the
 *  video's step tabs, so a longer tab's completions must not count here. */
function countCompletedSteps(completedSteps: ReadonlySet<number>, stepCount: number): number {
  let done = 0;
  for (const index of completedSteps) if (index >= 0 && index < stepCount) done += 1;
  return done;
}

/**
 * Step-by-step recipe / tutorial flow. Opens as a list on every viewport —
 * full instruction, tips, safety note and seek chip per step (R8 / D8); the
 * xyflow zigzag (`StepFlowMap`) is a toggle and mounts only when chosen. Both
 * views share one completion state through `useTabState`, so this tab and
 * StepByStepInteractive stay in sync.
 */
export const StepFlowCanvas = memo(function StepFlowCanvas({
  steps,
  onSeek,
  tabId = 'steps',
}: StepFlowCanvasProps) {
  // Completion lives in the shared TabStateContext (single source of truth) so
  // this canvas and StepByStepInteractive stay in sync, and pre-existing
  // completions are reflected the moment the canvas mounts.
  const { completedSteps, completeStep, uncompleteStep } = useTabState();
  const [view, setView] = useState<StepView>('list');

  // Depends on the provider's stable actions only, never the whole tab state:
  // a new toggle per completion would rebuild every flow node (and drop
  // React Flow's measurements — the nodes blink).
  const toggleComplete = useCallback(
    (index: number, isCompleted: boolean) => {
      if (isCompleted) {
        uncompleteStep(index);
      } else {
        completeStep(index);
      }
    },
    [completeStep, uncompleteStep],
  );

  if (!steps?.length) {
    return <EmptyTabState message="No steps were extracted for this video." icon={Workflow} />;
  }

  const progress = `${countCompletedSteps(completedSteps, steps.length)}/${steps.length} done`;

  return (
    <div className="space-y-3" data-tab-id={tabId}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs font-medium tabular-nums text-muted-foreground">{progress}</p>
        <CanvasViewToggle label="Steps view" value={view} options={VIEW_OPTIONS} onChange={setView} />
      </div>

      {view === 'flow' ? (
        <StepFlowMap steps={steps} completedSteps={completedSteps} onToggle={toggleComplete} onSeek={onSeek} />
      ) : (
        <ol className="space-y-2" aria-label="Steps">
          {steps.map((step, index) => (
            <StepRow
              key={index}
              step={step}
              index={index}
              completed={completedSteps.has(index)}
              durationSeconds={parseStepDurationSeconds(step.duration)}
              onToggle={toggleComplete}
              onSeek={onSeek}
            />
          ))}
        </ol>
      )}
    </div>
  );
});
