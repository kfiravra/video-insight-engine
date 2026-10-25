import type { ReactNode } from 'react';
import { RecipeIngredientPanel } from './RecipeIngredientPanel';
import { FlowContextPanel } from './FlowContextPanel';
import {
  findComponent,
  findStepTab,
  stripCountPrefix,
  makeStepRenderer,
  makeStudyStepRenderer,
  makeListenStepRenderer,
  momentsToListenSegments,
  exercisesToSteps,
  spotsToSteps,
  checklistContext,
  type FlowContextItem,
  type FlowStepRenderer,
} from './flow-mode-helpers';
import type { TabEntry, StepItem, FitnessExercise, SpotItem, ConceptItem, TechSnippet, FlashcardItem, QuizArenaQuestion, MomentItem } from '@vie/types';

// Re-exported so FlowContextPanel / ModesShowcase keep their import path
// after the helper extraction (4.4 follow-up, 500-line rule).
export type { FlowContextItem } from './flow-mode-helpers';

/** A fully-resolved enter-mode ready for FlowPlayer. `renderContext` and
 *  `renderStep` are produced per detected mode so FlowPlayer stays generic.
 *
 *  Tabs reaching detect/resolve are schema-validated at the ComposableOutput
 *  boundary (tab-prop-schemas.ts), so prop reads below use typed casts —
 *  the old private asArray coercion was deleted with the 4.4 follow-up. */
export interface ResolvedFlowMode {
  id: string;
  label: string;
  emoji: string;
  contextLabel: string;
  stepNoun: string;
  completionMessage: string;
  contextCount: number;
  sequenceLength: number;
  renderContext: () => ReactNode;
  renderStep: FlowStepRenderer;
}

interface FlowModeDef {
  id: string;
  label: string;
  emoji: string;
  contextLabel: string;
  stepNoun: string;
  completionMessage: string;
  /** True when this mode's required tabs/props are present. */
  detect: (tabs: TabEntry[], primaryTag?: string) => boolean;
  resolve: (tabs: TabEntry[]) => ResolvedFlowMode | null;
}

// ─── Mode definitions ───

/**
 * Cooking — the original behavior, migrated verbatim. Context = the checklist
 * (ingredients) rendered through RecipeIngredientPanel; sequence = the steps of
 * the step_player / step_flow_canvas tab rendered through RecipeStepView.
 */
const COOKING: FlowModeDef = {
  id: 'cooking',
  label: 'Cooking Mode',
  emoji: '🍳',
  contextLabel: 'Ingredients',
  stepNoun: 'steps',
  completionMessage: 'All steps complete! Enjoy your meal!',
  detect: (tabs, primaryTag) => {
    if (primaryTag !== 'food') return false;
    const checklist = findComponent(tabs, 'checklist');
    const step = findStepTab(tabs);
    return Boolean(
      Array.isArray(checklist?.props?.items) &&
      Array.isArray(step?.props?.steps),
    );
  },
  resolve: (tabs) => {
    const checklistTab = findComponent(tabs, 'checklist');
    const stepTab = findStepTab(tabs);
    const ingredients = checklistContext(checklistTab);
    const steps = (stepTab?.props?.steps as StepItem[] | undefined) ?? [];
    if (ingredients.length === 0 || steps.length === 0) return null;
    const tabLabel = stripCountPrefix(checklistTab?.label ?? '') || 'Ingredients';
    const scalable = typeof checklistTab?.props?.scalable === 'boolean' ? checklistTab.props.scalable : undefined;
    const baseServings = typeof checklistTab?.props?.baseServings === 'number' ? checklistTab.props.baseServings : undefined;
    return {
      id: COOKING.id,
      label: COOKING.label,
      emoji: COOKING.emoji,
      contextLabel: tabLabel,
      stepNoun: COOKING.stepNoun,
      completionMessage: COOKING.completionMessage,
      contextCount: ingredients.length,
      sequenceLength: steps.length,
      renderContext: () => (
        <RecipeIngredientPanel
          items={ingredients}
          tabLabel={tabLabel}
          scalable={scalable}
          baseServings={baseServings}
        />
      ),
      renderStep: makeStepRenderer(steps, ingredients),
    };
  },
};

/**
 * Workout — context = warmup/cooldown exercises; sequence = the main exercise
 * list run one exercise at a time.
 */
const WORKOUT: FlowModeDef = {
  id: 'workout',
  label: 'Workout Mode',
  emoji: '💪',
  contextLabel: 'Warmup & Cooldown',
  stepNoun: 'exercises',
  completionMessage: 'Workout complete! Great session!',
  detect: (tabs, primaryTag) => {
    if (primaryTag !== 'fitness') return false;
    const room = findComponent(tabs, 'workout_room');
    return Array.isArray(room?.props?.exercises) && (room!.props!.exercises as unknown[]).length > 0;
  },
  resolve: (tabs) => {
    const room = findComponent(tabs, 'workout_room');
    const exercises = (room?.props?.exercises as FitnessExercise[] | undefined) ?? [];
    if (exercises.length === 0) return null;
    const warmup = (room?.props?.warmup as FitnessExercise[] | undefined) ?? [];
    const cooldown = (room?.props?.cooldown as FitnessExercise[] | undefined) ?? [];
    const context: FlowContextItem[] = [
      ...warmup.map((e) => ({ label: e.name, emoji: e.emoji, note: e.duration, group: 'Warmup' })),
      ...cooldown.map((e) => ({ label: e.name, emoji: e.emoji, note: e.duration, group: 'Cooldown' })),
    ];
    const steps = exercisesToSteps(exercises);
    return {
      id: WORKOUT.id,
      label: WORKOUT.label,
      emoji: WORKOUT.emoji,
      contextLabel: context.length > 0 ? WORKOUT.contextLabel : 'Exercises',
      stepNoun: WORKOUT.stepNoun,
      completionMessage: WORKOUT.completionMessage,
      contextCount: context.length || exercises.length,
      sequenceLength: steps.length,
      renderContext: () =>
        context.length > 0
          ? <FlowContextPanel items={context} label={WORKOUT.contextLabel} />
          : <FlowContextPanel items={exercises.map((e) => ({ label: e.name, emoji: e.emoji }))} label="Exercises" />,
      renderStep: makeStepRenderer(steps),
    };
  },
};

/**
 * Build — context = materials checklist + code snippets; sequence = the
 * step_player / step_flow_canvas steps. Covers tech/project domains.
 */
const BUILD: FlowModeDef = {
  id: 'build',
  label: 'Build Mode',
  emoji: '🔧',
  contextLabel: 'Materials',
  stepNoun: 'steps',
  completionMessage: 'Build complete! Nicely done!',
  detect: (tabs, primaryTag) => {
    if (primaryTag !== 'tech' && primaryTag !== 'project') return false;
    const step = findStepTab(tabs);
    return Array.isArray(step?.props?.steps) && (step!.props!.steps as unknown[]).length > 0;
  },
  resolve: (tabs) => {
    const stepTab = findStepTab(tabs);
    const steps = (stepTab?.props?.steps as StepItem[] | undefined) ?? [];
    if (steps.length === 0) return null;
    const checklistTab = findComponent(tabs, 'checklist');
    const materials = checklistContext(checklistTab);
    const codeTab = findComponent(tabs, 'code_playground');
    const snippets = (codeTab?.props?.snippets as TechSnippet[] | undefined) ?? [];
    const context: FlowContextItem[] = [
      ...materials,
      ...snippets.map((s) => ({ label: s.filename || `${s.language} snippet`, note: s.explanation, group: 'Code' })),
    ];
    const contextLabel = materials.length > 0
      ? (stripCountPrefix(checklistTab?.label ?? '') || 'Materials')
      : 'Reference';
    return {
      id: BUILD.id,
      label: BUILD.label,
      emoji: BUILD.emoji,
      contextLabel,
      stepNoun: BUILD.stepNoun,
      completionMessage: BUILD.completionMessage,
      contextCount: context.length,
      sequenceLength: steps.length,
      renderContext: () => <FlowContextPanel items={context} label={contextLabel} />,
      renderStep: makeStepRenderer(steps, materials.length > 0 ? materials : undefined),
    };
  },
};

/**
 * Study — context = concept list (or flash_deck cards); sequence = the
 * quiz_arena questions, one at a time with the answer hidden until "Reveal".
 */
const STUDY: FlowModeDef = {
  id: 'study',
  label: 'Study Mode',
  emoji: '🎓',
  contextLabel: 'Concepts',
  stepNoun: 'questions',
  completionMessage: 'Session complete! Knowledge locked in!',
  detect: (tabs, primaryTag) => {
    if (primaryTag !== 'learning' && primaryTag !== 'science') return false;
    const quiz = findComponent(tabs, 'quiz_arena');
    const concepts = findComponent(tabs, 'concept_canvas') ?? findComponent(tabs, 'flash_deck');
    const hasQuiz = Array.isArray(quiz?.props?.questions) && (quiz!.props!.questions as unknown[]).length > 0;
    const hasConcepts = Boolean(concepts);
    return hasQuiz && hasConcepts;
  },
  resolve: (tabs) => {
    const quizTab = findComponent(tabs, 'quiz_arena');
    const questions = (quizTab?.props?.questions as QuizArenaQuestion[] | undefined) ?? [];
    if (questions.length === 0) return null;
    const conceptTab = findComponent(tabs, 'concept_canvas');
    const flashTab = findComponent(tabs, 'flash_deck');
    const concepts = (conceptTab?.props?.concepts as ConceptItem[] | undefined) ?? [];
    const cards = (flashTab?.props?.cards as FlashcardItem[] | undefined) ?? [];
    const context: FlowContextItem[] = concepts.length > 0
      ? concepts.map((c) => ({ label: c.name, emoji: c.emoji, note: c.definition }))
      : cards.map((c) => ({ label: c.front, note: c.back }));
    const contextLabel = concepts.length > 0 ? 'Concepts' : 'Flashcards';
    return {
      id: STUDY.id,
      label: STUDY.label,
      emoji: STUDY.emoji,
      contextLabel,
      stepNoun: STUDY.stepNoun,
      completionMessage: STUDY.completionMessage,
      contextCount: context.length,
      sequenceLength: questions.length,
      renderContext: () => <FlowContextPanel items={context} label={contextLabel} />,
      renderStep: makeStudyStepRenderer(questions),
    };
  },
};

/**
 * Explore — context = budget/packing reference; sequence = the spot_explorer
 * spots walked through as a day-by-day itinerary.
 */
const EXPLORE: FlowModeDef = {
  id: 'explore',
  label: 'Explore Mode',
  emoji: '🧭',
  contextLabel: 'Packing & Budget',
  stepNoun: 'stops',
  completionMessage: 'Itinerary complete! Safe travels!',
  detect: (tabs, primaryTag) => {
    if (primaryTag !== 'travel') return false;
    const spots = findComponent(tabs, 'spot_explorer');
    return Array.isArray(spots?.props?.spots) && (spots!.props!.spots as unknown[]).length > 0;
  },
  resolve: (tabs) => {
    const spotTab = findComponent(tabs, 'spot_explorer');
    const spots = (spotTab?.props?.spots as SpotItem[] | undefined) ?? [];
    if (spots.length === 0) return null;
    const packingTab = findComponent(tabs, 'packing_mission') ?? findComponent(tabs, 'checklist');
    const budgetTab = findComponent(tabs, 'budget');
    const packing = (packingTab?.props?.items as { item?: string; label?: string; emoji?: string }[] | undefined) ?? [];
    const breakdown = (budgetTab?.props?.breakdown as { label?: string; amount?: number }[] | undefined) ?? [];
    const context: FlowContextItem[] = [
      ...packing.map((p) => ({ label: p.item ?? p.label ?? '', emoji: p.emoji, group: 'Packing' })).filter((p) => p.label),
      ...breakdown.map((b) => ({ label: b.label ?? '', note: b.amount != null ? String(b.amount) : undefined, group: 'Budget' })).filter((b) => b.label),
    ];
    const steps = spotsToSteps(spots);
    return {
      id: EXPLORE.id,
      label: EXPLORE.label,
      emoji: EXPLORE.emoji,
      contextLabel: context.length > 0 ? EXPLORE.contextLabel : 'Itinerary',
      stepNoun: EXPLORE.stepNoun,
      completionMessage: EXPLORE.completionMessage,
      contextCount: context.length || spots.length,
      sequenceLength: steps.length,
      renderContext: () =>
        context.length > 0
          ? <FlowContextPanel items={context} label={EXPLORE.contextLabel} />
          : <FlowContextPanel items={spots.map((s) => ({ label: s.name, emoji: s.emoji }))} label="Stops" />,
      renderStep: makeStepRenderer(steps),
    };
  },
};

/**
 * Practice — language domain. Context = phrases/vocabulary; sequence = drills
 * run as step_player / step_flow_canvas steps.
 */
const PRACTICE: FlowModeDef = {
  id: 'practice',
  label: 'Practice Mode',
  emoji: '🗣️',
  contextLabel: 'Vocabulary',
  stepNoun: 'drills',
  completionMessage: 'Practice complete! Keep it up!',
  detect: (tabs, primaryTag) => {
    if (primaryTag !== 'language') return false;
    const drills = findStepTab(tabs);
    return Array.isArray(drills?.props?.steps) && (drills!.props!.steps as unknown[]).length > 0;
  },
  resolve: (tabs) => {
    const drillTab = findStepTab(tabs);
    const steps = (drillTab?.props?.steps as StepItem[] | undefined) ?? [];
    if (steps.length === 0) return null;
    // Vocabulary/phrases live in a flash_deck, spot_explorer, or info_grid tab.
    const flashTab = findComponent(tabs, 'flash_deck');
    const gridTab = findComponent(tabs, 'info_grid');
    const cards = (flashTab?.props?.cards as FlashcardItem[] | undefined) ?? [];
    const gridItems = (gridTab?.props?.items as { key?: string; value?: string }[] | undefined) ?? [];
    const context: FlowContextItem[] = cards.length > 0
      ? cards.map((c) => ({ label: c.front, note: c.back }))
      : gridItems.map((g) => ({ label: g.key ?? '', note: g.value })).filter((g) => g.label);
    const contextLabel = cards.length > 0 ? 'Phrases' : 'Vocabulary';
    return {
      id: PRACTICE.id,
      label: PRACTICE.label,
      emoji: PRACTICE.emoji,
      contextLabel: context.length > 0 ? contextLabel : 'Drills',
      stepNoun: PRACTICE.stepNoun,
      completionMessage: PRACTICE.completionMessage,
      contextCount: context.length || steps.length,
      sequenceLength: steps.length,
      renderContext: () =>
        context.length > 0
          ? <FlowContextPanel items={context} label={contextLabel} />
          : <FlowContextPanel items={steps.map((s) => ({ label: s.title || `Drill ${s.number}` }))} label="Drills" />,
      renderStep: makeStepRenderer(steps),
    };
  },
};

/**
 * Listen — podcast domain. Context = guests + topics (who's talking, what about);
 * sequence = the moment_track segments walked one at a time — each with its
 * description (when the source has one) and play range — so the listener can
 * move through the conversation with progress tracking.
 */
const LISTEN: FlowModeDef = {
  id: 'listen',
  label: 'Listen Mode',
  emoji: '🎙️',
  contextLabel: 'Guests & Topics',
  stepNoun: 'segments',
  completionMessage: 'Episode complete! Thanks for listening!',
  detect: (tabs, primaryTag) => {
    if (primaryTag !== 'podcast') return false;
    const segments = findComponent(tabs, 'moment_track');
    return Array.isArray(segments?.props?.items) && (segments!.props!.items as unknown[]).length > 0;
  },
  resolve: (tabs) => {
    const segmentTab = findComponent(tabs, 'moment_track');
    const items = (segmentTab?.props?.items as MomentItem[] | undefined) ?? [];
    if (items.length === 0) return null;
    const segments = momentsToListenSegments(items);
    const guestTab = findComponent(tabs, 'spot_explorer');
    const topicTab = findComponent(tabs, 'info_grid');
    const guests = (guestTab?.props?.spots as SpotItem[] | undefined) ?? [];
    const topics = (topicTab?.props?.items as { key?: string; value?: string }[] | undefined) ?? [];
    const context: FlowContextItem[] = [
      ...guests.map((g) => ({ label: g.name, emoji: g.emoji, note: g.description, group: 'Guests' })),
      ...topics.map((t) => ({ label: t.key ?? '', note: t.value, group: 'Topics' })).filter((t) => t.label),
    ];
    const contextLabel = context.length > 0 ? LISTEN.contextLabel : 'Segments';
    return {
      id: LISTEN.id,
      label: LISTEN.label,
      emoji: LISTEN.emoji,
      contextLabel,
      stepNoun: LISTEN.stepNoun,
      completionMessage: LISTEN.completionMessage,
      contextCount: context.length || segments.length,
      sequenceLength: segments.length,
      renderContext: () =>
        context.length > 0
          ? <FlowContextPanel items={context} label={contextLabel} />
          : <FlowContextPanel items={segments.map((s) => ({ label: s.label }))} label="Segments" />,
      renderStep: makeListenStepRenderer(segments),
    };
  },
};

/** Ordered registry. `detectFlowMode` returns the first mode whose `detect`
 *  passes — domains are mutually exclusive by `primaryTag`, so order only
 *  matters as a tie-break (cooking stays first to preserve its priority). */
export const FLOW_MODE_REGISTRY: readonly FlowModeDef[] = [
  COOKING,
  WORKOUT,
  BUILD,
  STUDY,
  EXPLORE,
  PRACTICE,
  LISTEN,
];

/**
 * Detect which enter-mode (if any) applies to the assembled tabs. Returns a
 * fully-resolved mode ready to hand to FlowPlayer, or null when no mode's
 * required tabs/props are present — in which case no enter-mode button shows.
 */
export function detectFlowMode(
  tabs: TabEntry[] | null | undefined,
  primaryTag: string | undefined,
): ResolvedFlowMode | null {
  if (!tabs || tabs.length === 0) return null;
  for (const mode of FLOW_MODE_REGISTRY) {
    if (mode.detect(tabs, primaryTag)) {
      const resolved = mode.resolve(tabs);
      if (resolved) return resolved;
    }
  }
  return null;
}
