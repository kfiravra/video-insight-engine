import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { TabStateProvider } from '@/features/video-output/contexts/TabStateContext';
import type { TabEntry } from '@vie/types';
import { detectFlowMode } from '../flow-modes';
import type { FlowStepRenderArgs } from '../FlowPlayer';

// ── Tab fixtures (minimal assembled-tab shapes per domain) ──

function checklistTab(itemCount = 3): TabEntry {
  return {
    id: 'ingredients',
    label: `${itemCount} Ingredients`,
    emoji: '🛒',
    component: 'checklist',
    props: {
      items: Array.from({ length: itemCount }, (_, i) => ({ label: `item ${i}` })),
      scalable: true,
      baseServings: 2,
    },
  };
}

function stepTab(stepCount = 4): TabEntry {
  return {
    id: 'steps',
    label: 'Steps',
    emoji: '👨‍🍳',
    component: 'step_player',
    props: {
      steps: Array.from({ length: stepCount }, (_, i) => ({
        number: i + 1,
        title: `Step ${i + 1}`,
        instruction: `Do thing ${i + 1}`,
      })),
    },
  };
}

/** Assembly promotes step_player to step_flow_canvas at 8+ steps, so every
 *  real-length recipe/build ships its steps under this component. */
function flowCanvasTab(stepCount = 9): TabEntry {
  return { ...stepTab(stepCount), component: 'step_flow_canvas' };
}

function workoutTab(): TabEntry {
  return {
    id: 'exercises',
    label: 'Exercises',
    emoji: '🏋️',
    component: 'workout_room',
    props: {
      exercises: [
        { name: 'Push-ups', emoji: '💪', sets: 3, reps: '12', formCues: ['Keep back straight'] },
        { name: 'Squats', emoji: '🦵', sets: 3, reps: '15', formCues: ['Knees over toes'] },
      ],
      warmup: [{ name: 'Jumping jacks', emoji: '🤸', formCues: [] }],
      cooldown: [{ name: 'Stretch', emoji: '🧘', formCues: [] }],
    },
  };
}

function quizTab(): TabEntry {
  return {
    id: 'quizzes',
    label: 'Quiz',
    emoji: '🧪',
    component: 'quiz_arena',
    props: {
      questions: [
        { question: 'What is 2+2?', options: ['3', '4', '5'], correctIndex: 1, explanation: 'Math.' },
        { question: 'Capital of France?', options: ['Berlin', 'Paris'], correctIndex: 1 },
      ],
    },
  };
}

function conceptTab(): TabEntry {
  return {
    id: 'concepts',
    label: 'Concepts',
    emoji: '🧠',
    component: 'concept_canvas',
    props: {
      concepts: [
        { name: 'Recursion', emoji: '🔁', definition: 'A function calling itself', connections: [] },
      ],
    },
  };
}

function spotTab(): TabEntry {
  return {
    id: 'itinerary',
    label: 'Itinerary',
    emoji: '🗺️',
    component: 'spot_explorer',
    props: {
      spots: [
        { name: 'Eiffel Tower', emoji: '🗼', description: 'Iconic landmark', connections: [] },
        { name: 'Louvre', emoji: '🖼️', description: 'Art museum', connections: [] },
      ],
    },
  };
}

function momentTab(withDescription = true): TabEntry {
  return {
    id: 'conversation_map',
    label: 'Conversation Timeline',
    emoji: '🎬',
    component: 'moment_track',
    props: {
      items: [
        { time: '2:28', seconds: 148, label: 'Intelligent life', description: withDescription ? 'Guess: no.' : undefined },
        { time: '5:44', seconds: 344, label: 'Great Filter' },
      ],
    },
  };
}

function renderStep(mode: ReturnType<typeof detectFlowMode>, onSeek = vi.fn()) {
  const args: FlowStepRenderArgs = {
    currentStep: 0,
    onStepChange: vi.fn(),
    onComplete: vi.fn(),
    isStepCompleted: () => false,
  };
  return render(
    <TabStateProvider videoId="flow-modes-test">{mode!.renderStep(args, onSeek)}</TabStateProvider>,
  );
}

describe('detectFlowMode', () => {
  describe('cooking (food)', () => {
    it('detects cooking mode when food has checklist + step_player', () => {
      const mode = detectFlowMode([checklistTab(), stepTab()], 'food');
      expect(mode?.id).toBe('cooking');
    });

    it('uses "Cooking Mode" label so the migrated behavior is unchanged', () => {
      const mode = detectFlowMode([checklistTab(), stepTab()], 'food');
      expect(mode?.label).toBe('Cooking Mode');
    });

    it('exposes ingredients as the context (count from checklist items)', () => {
      const mode = detectFlowMode([checklistTab(5), stepTab(4)], 'food');
      expect(mode?.contextCount).toBe(5);
    });

    it('exposes the steps as the sequence', () => {
      const mode = detectFlowMode([checklistTab(5), stepTab(4)], 'food');
      expect(mode?.sequenceLength).toBe(4);
    });

    it('detects cooking mode when the steps were promoted to step_flow_canvas', () => {
      const mode = detectFlowMode([checklistTab(), flowCanvasTab()], 'food');
      expect(mode?.id).toBe('cooking');
    });

    it('sequences the step_flow_canvas steps', () => {
      const mode = detectFlowMode([checklistTab(), flowCanvasTab(12)], 'food');
      expect(mode?.sequenceLength).toBe(12);
    });

    it('prefers the step_player sequence when both step tabs are present', () => {
      const mode = detectFlowMode([checklistTab(), flowCanvasTab(12), stepTab(4)], 'food');
      expect(mode?.sequenceLength).toBe(4);
    });

    it('returns null for food without a step tab', () => {
      const mode = detectFlowMode([checklistTab()], 'food');
      expect(mode).toBeNull();
    });

    it('returns null for food without a checklist tab', () => {
      const mode = detectFlowMode([stepTab()], 'food');
      expect(mode).toBeNull();
    });
  });

  describe('workout (fitness)', () => {
    it('detects workout mode when fitness has a workout_room with exercises', () => {
      const mode = detectFlowMode([workoutTab()], 'fitness');
      expect(mode?.id).toBe('workout');
    });

    it('sequences the main exercise list', () => {
      const mode = detectFlowMode([workoutTab()], 'fitness');
      expect(mode?.sequenceLength).toBe(2);
    });

    it('returns null for fitness without exercises', () => {
      const empty: TabEntry = { id: 'x', label: 'x', emoji: '🏋️', component: 'workout_room', props: { exercises: [] } };
      expect(detectFlowMode([empty], 'fitness')).toBeNull();
    });
  });

  describe('build (tech/project)', () => {
    it('detects build mode for tech with step_player', () => {
      const mode = detectFlowMode([stepTab(6)], 'tech');
      expect(mode?.id).toBe('build');
    });

    it('detects build mode for project with step_player', () => {
      const mode = detectFlowMode([stepTab(3)], 'project');
      expect(mode?.id).toBe('build');
    });

    it('detects build mode for tech with step_flow_canvas', () => {
      const mode = detectFlowMode([flowCanvasTab()], 'tech');
      expect(mode?.id).toBe('build');
    });

    it('detects build mode for project with step_flow_canvas', () => {
      const mode = detectFlowMode([flowCanvasTab()], 'project');
      expect(mode?.id).toBe('build');
    });

    it('returns null for tech without a step tab', () => {
      expect(detectFlowMode([checklistTab()], 'tech')).toBeNull();
    });
  });

  describe('study (learning/science)', () => {
    it('detects study mode when learning has quiz + concepts', () => {
      const mode = detectFlowMode([conceptTab(), quizTab()], 'learning');
      expect(mode?.id).toBe('study');
    });

    it('sequences the quiz questions', () => {
      const mode = detectFlowMode([conceptTab(), quizTab()], 'learning');
      expect(mode?.sequenceLength).toBe(2);
    });

    it('returns null for learning without a quiz', () => {
      expect(detectFlowMode([conceptTab()], 'learning')).toBeNull();
    });

    it('returns null for learning without concepts/flashcards', () => {
      expect(detectFlowMode([quizTab()], 'learning')).toBeNull();
    });

    it('renders the question with the answer hidden behind Reveal', () => {
      renderStep(detectFlowMode([conceptTab(), quizTab()], 'learning'));
      expect(screen.getByRole('button', { name: /Reveal answer/ })).toBeInTheDocument();
      expect(screen.queryByText(/^Answer:/)).not.toBeInTheDocument();
    });
  });

  describe('listen (podcast)', () => {
    it('detects listen mode when podcast has a moment_track', () => {
      const mode = detectFlowMode([momentTab()], 'podcast');
      expect(mode?.id).toBe('listen');
    });

    it('sequences the segments', () => {
      const mode = detectFlowMode([momentTab()], 'podcast');
      expect(mode?.sequenceLength).toBe(2);
    });

    it('renders the segment description and play range instead of recipe copy', () => {
      renderStep(detectFlowMode([momentTab()], 'podcast'));
      expect(screen.getByText('Guess: no.')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Play 2:28 – 5:44' })).toBeInTheDocument();
    });

    it('renders only the label and range when the segment has no description', () => {
      renderStep(detectFlowMode([momentTab(false)], 'podcast'));
      expect(screen.getAllByText('Intelligent life')).toHaveLength(1);
    });

    it('returns null for podcast without a moment_track', () => {
      expect(detectFlowMode([conceptTab()], 'podcast')).toBeNull();
    });
  });

  describe('explore (travel)', () => {
    it('detects explore mode when travel has spot_explorer', () => {
      const mode = detectFlowMode([spotTab()], 'travel');
      expect(mode?.id).toBe('explore');
    });

    it('sequences the spots as stops', () => {
      const mode = detectFlowMode([spotTab()], 'travel');
      expect(mode?.sequenceLength).toBe(2);
    });
  });

  describe('practice (language)', () => {
    it('detects practice mode when language has drill steps', () => {
      const mode = detectFlowMode([stepTab(5)], 'language');
      expect(mode?.id).toBe('practice');
    });

    it('detects practice mode when the drills were promoted to step_flow_canvas', () => {
      const mode = detectFlowMode([flowCanvasTab()], 'language');
      expect(mode?.id).toBe('practice');
    });
  });

  describe('no mode', () => {
    it('returns null when there are no tabs', () => {
      expect(detectFlowMode([], 'food')).toBeNull();
    });

    it('returns null when the domain is unknown', () => {
      expect(detectFlowMode([checklistTab(), stepTab()], 'music')).toBeNull();
    });

    it('does not detect cooking for a non-food domain even with matching tabs', () => {
      // tech with checklist + step_player resolves to build, never cooking.
      const mode = detectFlowMode([checklistTab(), stepTab()], 'tech');
      expect(mode?.id).toBe('build');
    });
  });
});
