import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { rawConfig, QUIZ_TIMER } from '@vie/shared/config';

vi.mock('../Celebration', () => ({
  Celebration: ({ title }: { title: string }) => <div data-testid="celebration">{title}</div>,
}));

import { COMPONENT_REGISTRY, type NavProps } from '../component-registry';
import { TAB_PROP_SCHEMAS } from '@/features/video-output/lib/tab-prop-schemas';

// output-quality 1.5 (D9, R12): the quiz is untimed — domains.json
// `quizPolicy.timer` is the single source the registry passes to QuizArena —
// and every question may carry the second its answer is said.

const nav: NavProps = { onNavigateTab: () => undefined, tabId: 'quiz' };
const questions = [
  { question: 'Why did 37signals put pencils down?', options: ['Cost', 'Agents'], correctIndex: 1, timestamp: 1262 },
  { question: 'What does P-bloom mean?', options: ['Optimism', 'Doom'], correctIndex: 0, timestamp: 3506 },
];

describe('quiz registry policy', () => {
  it('should read the timer from quizPolicy when the registry renders a quiz', () => {
    expect(QUIZ_TIMER).toBe(rawConfig.quizPolicy?.timer);
  });

  it('should keep the quiz untimed when quizPolicy turns the timer off', () => {
    expect(QUIZ_TIMER).toBe(false);
  });

  it('should render no countdown when the quiz_arena tab renders', () => {
    render(<>{COMPONENT_REGISTRY.quiz_arena({ questions }, nav)}</>);

    expect(screen.queryByRole('timer')).not.toBeInTheDocument();
  });

  it('should render no countdown when the quick_quiz strip renders', () => {
    render(<>{COMPONENT_REGISTRY.quick_quiz({ questions: questions.slice(0, 1) }, nav)}</>);

    expect(screen.queryByRole('timer')).not.toBeInTheDocument();
  });

  it('should accept a question timestamp when the quiz props are validated', () => {
    expect(TAB_PROP_SCHEMAS.quiz_arena.schema.safeParse({ questions }).success).toBe(true);
  });

  it('should size the quiz 3 / 5 / 8 by length when the policy bands are read', () => {
    expect(rawConfig.quizPolicy?.questionsByDuration.map((band) => band.questions)).toEqual([3, 5, 8]);
  });
});
