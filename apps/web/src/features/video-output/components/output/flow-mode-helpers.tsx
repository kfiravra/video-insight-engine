/**
 * Shared helpers for enter-mode definitions (flow-modes.tsx): tab lookup,
 * label cleanup, and the normalizers that turn per-domain payloads into the
 * StepItem sequence FlowPlayer runs.
 *
 * Extracted from flow-modes.tsx in the 4.4 follow-up (500-line rule). Tabs
 * reaching these helpers are already schema-validated at the ComposableOutput
 * boundary (tab-prop-schemas.ts), so typed casts replace the old asArray
 * coercion.
 */

import type { ReactNode } from 'react';
import { RecipeStepView, StudyStepView, ListenStepView, type ListenSegment } from './RecipeStepView';
import type { FlowStepRenderArgs } from './FlowPlayer';
import type { TabEntry, StepItem, FitnessExercise, SpotItem, QuizArenaQuestion, MomentItem } from '@vie/types';

/** A context-panel entry — a checkable orientation item (ingredient, material,
 *  vocabulary word, packing item, …). Mirrors the cooking ingredient shape so
 *  the cooking path stays byte-for-byte identical. */
export interface FlowContextItem {
  label: string;
  note?: string;
  emoji?: string;
  amount?: number;
  displayAmount?: string;
  unit?: string;
  essential?: boolean;
  group?: string;
}

/** Render signature for one step of a resolved enter-mode. */
export type FlowStepRenderer = (
  args: FlowStepRenderArgs,
  onSeek?: (s: number) => void,
) => ReactNode;

export function findComponent(tabs: TabEntry[], component: string): TabEntry | undefined {
  return tabs.find((t) => t.component === component);
}

/** The tab carrying an ordered step sequence. Assembly promotes step_player to
 *  step_flow_canvas at 8+ steps (and the planner picks the canvas directly for
 *  8+ stages) — same `steps` contract — so a mode keyed on step_player alone
 *  disappears for nearly every real recipe or build. step_player wins when both
 *  exist so outputs that already resolved a mode keep the same sequence. */
export function findStepTab(tabs: TabEntry[]): TabEntry | undefined {
  return findComponent(tabs, 'step_player') ?? findComponent(tabs, 'step_flow_canvas');
}

// One count-claim rule for the whole app (domain-meta's LEADING_COUNT) — the
// enter-mode context labels and the registry's tab labels strip it alike.
export { stripCountPrefix } from '@/components/vie/cards/domain-meta';

/** Build a RecipeStepView-backed step renderer from a StepItem list. Every
 *  step-based mode (cooking, build, workout, practice, explore) shares this so
 *  the run pane behaves identically across domains. */
export function makeStepRenderer(
  steps: StepItem[],
  contextItems?: Array<{ label: string }>,
): FlowStepRenderer {
  return (args, onSeek) => (
    <RecipeStepView
      steps={steps}
      currentStep={args.currentStep}
      onStepChange={args.onStepChange}
      onComplete={args.onComplete}
      onSeek={onSeek}
      ingredients={contextItems}
    />
  );
}

/** Normalize an exercise list into ordered StepItems for the run pane. */
export function exercisesToSteps(exercises: FitnessExercise[]): StepItem[] {
  return exercises.map((ex, i) => {
    const reps = ex.sets && ex.reps ? `${ex.sets} × ${ex.reps}` : ex.reps;
    const instruction = [reps, ex.formCues?.join('. ')].filter(Boolean).join(' — ');
    return {
      number: i + 1,
      title: `${ex.emoji ? `${ex.emoji} ` : ''}${ex.name}`,
      instruction: instruction || ex.name,
      duration: ex.duration,
      tips: ex.formCues?.length ? ex.formCues.join(' · ') : undefined,
      timestamp: ex.timestamp,
    };
  });
}

/** Build the Study run pane: one quiz question at a time, answer hidden until
 *  "Reveal" (or until the question is marked done). */
export function makeStudyStepRenderer(questions: QuizArenaQuestion[]): FlowStepRenderer {
  return (args, onSeek) => (
    <StudyStepView
      questions={questions}
      currentStep={args.currentStep}
      onStepChange={args.onStepChange}
      onComplete={args.onComplete}
      onSeek={onSeek}
    />
  );
}

/** Normalize moment_track items into listen segments. A segment without an
 *  explicit end (or with one that doesn't come after its start) plays until
 *  the next segment starts; the last one is open-ended. */
export function momentsToListenSegments(items: MomentItem[]): ListenSegment[] {
  return items.map((item, i) => {
    const next = items[i + 1]?.seconds;
    const explicitEnd = item.endSeconds != null && item.endSeconds > item.seconds ? item.endSeconds : undefined;
    const endSeconds = explicitEnd ?? (next != null && next > item.seconds ? next : undefined);
    return {
      label: item.label || `Segment ${i + 1}`,
      description: item.description || undefined,
      speaker: item.speaker || undefined,
      seconds: item.seconds,
      endSeconds,
    };
  });
}

/** Build the Listen run pane: segment description + play range per segment. */
export function makeListenStepRenderer(segments: ListenSegment[]): FlowStepRenderer {
  return (args, onSeek) => (
    <ListenStepView
      segments={segments}
      currentStep={args.currentStep}
      onStepChange={args.onStepChange}
      onComplete={args.onComplete}
      onSeek={onSeek}
    />
  );
}

/** Normalize travel spots into a day-by-day explore sequence. */
export function spotsToSteps(spots: SpotItem[]): StepItem[] {
  return spots.map((s, i) => ({
    number: i + 1,
    title: `${s.emoji ? `${s.emoji} ` : ''}${s.name}`,
    instruction: s.description,
    duration: s.duration,
    tips: s.tips || undefined,
  }));
}

/** Pull a checklist tab's items into context items. */
export function checklistContext(tab: TabEntry | undefined): FlowContextItem[] {
  return (tab?.props?.items as FlowContextItem[] | undefined) ?? [];
}
