/**
 * Step panes of the enter-mode runner (FlowPlayer). `FlowStepFrame` is the
 * shared chrome — "N of M" header, scrollable body, Prev / Done / Next — and
 * each mode supplies its own body: RecipeStepView (cooking, build, workout,
 * explore, practice), StudyStepView (quiz questions, answer hidden until
 * "Reveal") and ListenStepView (podcast segments with their play range).
 */
import { memo, useMemo, useState, type ReactNode } from 'react';
import { Check, AlertTriangle, Lightbulb, Clock, ChevronLeft, ChevronRight, Eye, Play } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { Timer, Badge } from '@/components/vie';
import { useLabels } from '@/lib/i18n';
import { formatDuration, formatDurationHuman } from '@/lib/string-utils';
import { useTabState } from '@/features/video-output/contexts/TabStateContext';
import { buildStepIngredientMap } from '@/features/video-output/components/output/lib/ingredient-step-matcher';
import { parseStepDurationSeconds } from '@/features/video-output/lib/step-duration';
import type { StepItem, QuizArenaQuestion } from '@vie/types';

import { QuizExplanation } from './interactive/QuizExplanation';

// ─── Shared chrome ───

interface FlowStepFrameProps {
  /** Word for one sequence entry in the header counter ("Step", "Question", "Segment"). */
  noun: string;
  currentStep: number;
  /** The number printed in the header — the step's own number when the
   *  source carries one, else the position (index + 1). */
  ordinal?: number;
  total: number;
  isDone: boolean;
  /** Extra header content on the trailing side (the cooking readiness pill). */
  headerExtra?: ReactNode;
  onStepChange: (index: number) => void;
  onComplete: (index: number) => void;
  children: ReactNode;
}

interface FlowStepNavProps {
  currentStep: number;
  total: number;
  isDone: boolean;
  onStepChange: (index: number) => void;
  onComplete: (index: number) => void;
}

/** Prev / Done / Next footer of a step pane. */
function FlowStepNav({ currentStep, total, isDone, onStepChange, onComplete }: FlowStepNavProps) {
  const t = useLabels();
  return (
    <div className="flex items-center justify-between px-4 py-3 border-t border-border/30">
      <Button
        variant="ghost"
        size="sm"
        onClick={() => onStepChange(currentStep - 1)}
        disabled={currentStep === 0}
        className="gap-1.5"
      >
        <ChevronLeft className="h-4 w-4 rtl:rotate-180" aria-hidden="true" />
        Prev
      </Button>

      <Button
        variant={isDone ? 'outline' : 'default'}
        size="sm"
        onClick={() => onComplete(currentStep)}
        className="gap-1.5 min-h-[44px]"
      >
        <Check className="h-4 w-4" aria-hidden="true" />
        {isDone ? t.undo : t.done}
      </Button>

      <Button
        variant="ghost"
        size="sm"
        onClick={() => onStepChange(currentStep + 1)}
        disabled={currentStep === total - 1}
        className="gap-1.5"
      >
        Next
        <ChevronRight className="h-4 w-4 rtl:rotate-180" aria-hidden="true" />
      </Button>
    </div>
  );
}

export function FlowStepFrame({
  noun,
  currentStep,
  ordinal,
  total,
  isDone,
  headerExtra,
  onStepChange,
  onComplete,
  children,
}: FlowStepFrameProps) {
  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between px-4 py-3 border-b border-border/30">
        <div className="flex items-center gap-2">
          <span className="text-xs text-muted-foreground">
            {noun} {ordinal ?? currentStep + 1} of {total}
          </span>
          {isDone && (
            <Badge variant="success" className="text-xs">Done</Badge>
          )}
        </div>
        {headerExtra}
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-4">{children}</div>

      <FlowStepNav
        currentStep={currentStep}
        total={total}
        isDone={isDone}
        onStepChange={onStepChange}
        onComplete={onComplete}
      />
    </div>
  );
}

interface SeekButtonProps {
  seconds: number;
  label: string;
  onSeek: (seconds: number) => void;
}

function SeekButton({ seconds, label, onSeek }: SeekButtonProps) {
  return (
    <Button variant="outline" size="sm" onClick={() => onSeek(seconds)} className="gap-1.5">
      <Play className="h-3.5 w-3.5" aria-hidden="true" />
      <span dir="ltr">{label}</span>
    </Button>
  );
}

// ─── Recipe / build / workout steps ───

interface RecipeStepViewProps {
  steps: StepItem[];
  currentStep: number;
  onStepChange: (index: number) => void;
  onComplete: (index: number) => void;
  onSeek?: (seconds: number) => void;
  ingredients?: Array<{ label: string }>;
}

export const RecipeStepView = memo(function RecipeStepView({
  steps,
  currentStep,
  onStepChange,
  onComplete,
  onSeek,
  ingredients,
}: RecipeStepViewProps) {
  const tabState = useTabState();
  const step = steps[currentStep];

  // Pre-compute ingredient→step mapping
  const stepIngredientMap = useMemo(() => {
    if (!ingredients || ingredients.length === 0) return new Map<number, number[]>();
    return buildStepIngredientMap(steps, ingredients);
  }, [steps, ingredients]);

  // Get checked ingredients for current step
  const stepReadiness = useMemo(() => {
    const matched = stepIngredientMap.get(currentStep);
    if (!matched || matched.length === 0) return null;
    const checkedCount = matched.filter(i => tabState.isChecked('checklist', i)).length;
    return { total: matched.length, checked: checkedCount, allReady: checkedCount === matched.length };
  }, [currentStep, stepIngredientMap, tabState]);

  if (!step) return null;

  const isDone = tabState.isStepCompleted(currentStep);
  const durationSecs = parseStepDurationSeconds(step.duration);

  const readinessPill = stepReadiness && (
    <span className={cn(
      'text-xs px-2 py-0.5 rounded-full transition-colors',
      stepReadiness.allReady
        ? 'bg-success/10 text-success'
        : 'bg-warning/10 text-warning',
    )}>
      {stepReadiness.checked}/{stepReadiness.total} ingredients ready
    </span>
  );

  return (
    <FlowStepFrame
      noun="Step"
      currentStep={currentStep}
      ordinal={step.number}
      total={steps.length}
      isDone={isDone}
      headerExtra={readinessPill}
      onStepChange={onStepChange}
      onComplete={onComplete}
    >
      {step.title && (
        <h3 className={cn('text-lg font-semibold', isDone && 'line-through opacity-60')}>
          {step.title}
        </h3>
      )}

      <p className={cn('text-base leading-relaxed', isDone && 'line-through opacity-60')}>
        {step.instruction}
      </p>

      {/* Duration + seek */}
      <div className="flex items-center gap-3 flex-wrap">
        {step.duration && (
          <span className="inline-flex items-center gap-1 text-sm text-muted-foreground bg-muted/50 px-3 py-1 rounded-lg">
            <Clock className="h-4 w-4" aria-hidden="true" />
            {step.duration}
          </span>
        )}
        {step.timestamp != null && onSeek && (
          <SeekButton seconds={step.timestamp} label="Watch this step" onSeek={onSeek} />
        )}
      </div>

      {/* Timer */}
      {durationSecs > 0 && !isDone && (
        <Timer
          // Timer seeds its countdown once; a per-step key restarts it from this step's duration.
          key={currentStep}
          duration={durationSecs}
          onComplete={() => onComplete(currentStep)}
          className="mt-2"
        />
      )}

      <StepNotes tips={step.tips} safetyNote={step.safetyNote} />
    </FlowStepFrame>
  );
});

/** The tip and safety callouts under a step. */
function StepNotes({ tips, safetyNote }: { tips?: string; safetyNote?: string }) {
  return (
    <>
      {tips && (
        <div className="flex items-start gap-2 text-sm text-info bg-info/5 rounded-lg px-3 py-2">
          <Lightbulb className="h-4 w-4 shrink-0 mt-0.5" aria-hidden="true" />
          <span>{tips}</span>
        </div>
      )}
      {safetyNote && (
        <div className="flex items-start gap-2 text-sm text-warning bg-warning-soft rounded-lg px-3 py-2">
          <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" aria-hidden="true" />
          <span>{safetyNote}</span>
        </div>
      )}
    </>
  );
}

// ─── Study questions ───

interface StudyStepViewProps {
  questions: QuizArenaQuestion[];
  currentStep: number;
  onStepChange: (index: number) => void;
  onComplete: (index: number) => void;
  onSeek?: (seconds: number) => void;
}

/** One quiz question at a time: the question and its options, the answer kept
 *  hidden until "Reveal answer" (or until the question is marked done). */
export const StudyStepView = memo(function StudyStepView({
  questions,
  currentStep,
  onStepChange,
  onComplete,
  onSeek,
}: StudyStepViewProps) {
  const tabState = useTabState();
  const question = questions[currentStep];
  if (!question) return null;

  const isDone = tabState.isStepCompleted(currentStep);

  return (
    <FlowStepFrame
      noun="Question"
      currentStep={currentStep}
      total={questions.length}
      isDone={isDone}
      onStepChange={onStepChange}
      onComplete={onComplete}
    >
      {/* Keyed per question so the reveal state never carries over to the next one. */}
      <StudyQuestion key={currentStep} question={question} forceRevealed={isDone} onSeek={onSeek} />
    </FlowStepFrame>
  );
});

interface StudyQuestionProps {
  question: QuizArenaQuestion;
  forceRevealed: boolean;
  onSeek?: (seconds: number) => void;
}

/** The lettered options; the correct one is marked once the answer is shown. */
function StudyOptionList({ question, shown }: { question: QuizArenaQuestion; shown: boolean }) {
  if (question.options.length === 0) return null;
  return (
    <ol className="space-y-1.5">
      {question.options.map((option, i) => {
        const isAnswer = shown && i === question.correctIndex;
        return (
          <li
            key={i}
            className={cn(
              'flex items-start gap-2 rounded-lg border border-border/30 px-3 py-2 text-sm',
              isAnswer && 'border-success/40 bg-success/10 font-medium text-success',
            )}
          >
            <span className="w-5 shrink-0 text-xs text-muted-foreground tabular-nums">
              {String.fromCharCode(65 + i)}.
            </span>
            <span>{option}</span>
            {isAnswer && <Check className="ms-auto h-4 w-4 shrink-0" aria-hidden="true" />}
          </li>
        );
      })}
    </ol>
  );
}

function StudyQuestion({ question, forceRevealed, onSeek }: StudyQuestionProps) {
  const [revealed, setRevealed] = useState(false);
  const shown = revealed || forceRevealed;
  const answer = question.options[question.correctIndex];
  const canSeek = question.timestamp != null && onSeek != null;
  // Nothing behind the button → no button.
  const hasReveal = Boolean(answer || question.explanation || canSeek);

  return (
    <>
      <h3 className="text-lg font-semibold">{question.question}</h3>

      <StudyOptionList question={question} shown={shown} />

      {/* Live region: the reveal swaps the button for the answer, so the
          swap is announced instead of silently moving focus to the body. */}
      {hasReveal && (
        <div aria-live="polite" className="space-y-3">
          {!shown ? (
            <Button variant="outline" size="sm" onClick={() => setRevealed(true)} className="gap-1.5">
              <Eye className="h-3.5 w-3.5" aria-hidden="true" />
              Reveal answer
            </Button>
          ) : (
            <>
              {answer && (
                <p className="text-sm">
                  <span className="font-semibold">Answer:</span> {answer}
                </p>
              )}
              {question.explanation && (
                <div className="flex items-start gap-2 text-sm text-info bg-info/5 rounded-lg px-3 py-2">
                  <Lightbulb className="h-4 w-4 shrink-0 mt-0.5" aria-hidden="true" />
                  {/* Same seek chips as the quiz tab: each "(m:ss)" in the explanation jumps the video. */}
                  <QuizExplanation
                    text={question.explanation}
                    onSeek={onSeek}
                    className="min-w-0 leading-5 text-info"
                  />
                </div>
              )}
              {canSeek && question.timestamp != null && onSeek && (
                <SeekButton seconds={question.timestamp} label="Watch this part" onSeek={onSeek} />
              )}
            </>
          )}
        </div>
      )}
    </>
  );
}

// ─── Listen segments ───

/** One conversation segment of a podcast, as Listen mode walks it. */
export interface ListenSegment {
  label: string;
  description?: string;
  speaker?: string;
  seconds?: number;
  /** Where the segment ends — the next segment's start when not stated. */
  endSeconds?: number;
}

/** "2:28 – 5:44", or "from 58:12" when the end is unknown. */
function formatPlayRange(segment: ListenSegment): string | null {
  if (segment.seconds == null) return null;
  const start = formatDuration(segment.seconds);
  if (segment.endSeconds == null) return `Play from ${start}`;
  return `Play ${start} – ${formatDuration(segment.endSeconds)}`;
}

/** The play-range control plus the segment length chip. */
function PlayRangeRow({ segment, onSeek }: { segment: ListenSegment; onSeek?: (seconds: number) => void }) {
  const range = formatPlayRange(segment);
  const length =
    segment.seconds != null && segment.endSeconds != null && segment.endSeconds > segment.seconds
      ? formatDurationHuman(segment.endSeconds - segment.seconds)
      : null;
  return (
    <div className="flex items-center gap-3 flex-wrap">
      {range && segment.seconds != null && onSeek ? (
        <SeekButton seconds={segment.seconds} label={range} onSeek={onSeek} />
      ) : range ? (
        <span dir="ltr" className="inline-flex items-center gap-1 text-sm text-muted-foreground bg-muted/50 px-3 py-1 rounded-lg">
          <Play className="h-4 w-4" aria-hidden="true" />
          {range}
        </span>
      ) : null}
      {length && (
        <span className="inline-flex items-center gap-1 text-sm text-muted-foreground bg-muted/50 px-3 py-1 rounded-lg">
          <Clock className="h-4 w-4" aria-hidden="true" />
          {length}
        </span>
      )}
    </div>
  );
}

interface ListenStepViewProps {
  segments: ListenSegment[];
  currentStep: number;
  onStepChange: (index: number) => void;
  onComplete: (index: number) => void;
  onSeek?: (seconds: number) => void;
}

/** One segment at a time: its label, the segment description when the source
 *  has one, and the play range — no recipe copy, no "Segment N" filler. */
export const ListenStepView = memo(function ListenStepView({
  segments,
  currentStep,
  onStepChange,
  onComplete,
  onSeek,
}: ListenStepViewProps) {
  const tabState = useTabState();
  const segment = segments[currentStep];
  if (!segment) return null;

  const isDone = tabState.isStepCompleted(currentStep);

  return (
    <FlowStepFrame
      noun="Segment"
      currentStep={currentStep}
      total={segments.length}
      isDone={isDone}
      onStepChange={onStepChange}
      onComplete={onComplete}
    >
      <h3 className={cn('text-lg font-semibold', isDone && 'line-through opacity-60')}>
        {segment.label}
      </h3>

      {segment.speaker && (
        <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">{segment.speaker}</p>
      )}

      {segment.description && (
        <p className={cn('text-base leading-relaxed', isDone && 'line-through opacity-60')}>
          {segment.description}
        </p>
      )}

      <PlayRangeRow segment={segment} onSeek={onSeek} />
    </FlowStepFrame>
  );
});
