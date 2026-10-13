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
import { RecipeStepView } from './RecipeStepView';
import type { FlowStepRenderArgs } from './FlowPlayer';
import type { TabEntry, StepItem, FitnessExercise, SpotItem, QuizArenaQuestion } from '@vie/types';

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

/** Strip an assembler-produced count prefix ("8 Steps" → "Steps"). Mirrors the
 *  helper in component-registry so context labels read cleanly. */
export function stripCountPrefix(label: string): string {
  return label.replace(/^\d+\s+(?=\p{L})/u, '');
}

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

/** Normalize quiz questions into a study run sequence (reveal-the-answer). */
export function questionsToSteps(questions: QuizArenaQuestion[]): StepItem[] {
  return questions.map((q, i) => {
    const answer = q.options?.[q.correctIndex];
    return {
      number: i + 1,
      title: q.question,
      instruction: answer ? `Answer: ${answer}` : 'Recall the answer, then mark done.',
      tips: q.explanation || undefined,
      timestamp: q.timestamp,
    };
  });
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
