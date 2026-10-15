import { useState } from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { TabStateProvider, useTabState } from '@/features/video-output/contexts/TabStateContext';
import { StudyStepView } from '../RecipeStepView';
import type { QuizArenaQuestion } from '@vie/types';

const QUESTIONS: QuizArenaQuestion[] = [
  {
    question: 'What analogy does DHH use for Opus 4.5?',
    options: ['Kodak Brownie', 'Leica camera', 'Polaroid'],
    correctIndex: 0,
    explanation: 'He compares it to the Brownie democratizing photography (11:20).',
    timestamp: 680,
  },
  { question: 'Capital of France?', options: ['Berlin', 'Paris'], correctIndex: 1 },
];

interface HarnessProps {
  onSeek?: (s: number) => void;
  questions?: QuizArenaQuestion[];
}

/** Inside the provider: "Done" marks the question complete in shared tab state. */
function StudyHarnessInner({ onSeek, questions = QUESTIONS }: HarnessProps) {
  const [currentStep, setCurrentStep] = useState(0);
  const tabState = useTabState();
  return (
    <StudyStepView
      questions={questions}
      currentStep={currentStep}
      onStepChange={setCurrentStep}
      onComplete={(i) => tabState.completeStep(i)}
      onSeek={onSeek}
    />
  );
}

function StudyHarness(props: HarnessProps) {
  return (
    <TabStateProvider videoId="study-step-test">
      <StudyHarnessInner {...props} />
    </TabStateProvider>
  );
}

describe('StudyStepView', () => {
  it('should show the question and its options before reveal', () => {
    render(<StudyHarness />);
    expect(screen.getByRole('heading', { name: /What analogy does DHH use/ })).toBeInTheDocument();
    expect(screen.getByText('Leica camera')).toBeInTheDocument();
  });

  it('should hide the answer and explanation until Reveal is clicked', () => {
    render(<StudyHarness />);
    expect(screen.queryByText(/^Answer:/)).not.toBeInTheDocument();
    expect(screen.queryByText(/democratizing photography/)).not.toBeInTheDocument();
  });

  it('should count questions, not steps, in the header', () => {
    render(<StudyHarness />);
    expect(screen.getByText('Question 1 of 2')).toBeInTheDocument();
  });

  it('should show the answer after Reveal is clicked', async () => {
    const user = userEvent.setup();
    render(<StudyHarness />);
    await user.click(screen.getByRole('button', { name: /Reveal answer/ }));
    expect(screen.getByText('Answer:')).toBeInTheDocument();
  });

  it('should show the explanation after Reveal', async () => {
    const user = userEvent.setup();
    render(<StudyHarness />);
    await user.click(screen.getByRole('button', { name: /Reveal answer/ }));
    expect(screen.getByText(/democratizing photography/)).toBeInTheDocument();
  });

  it('should seek to the question timestamp from "Watch this part" after Reveal', async () => {
    const user = userEvent.setup();
    const onSeek = vi.fn();
    render(<StudyHarness onSeek={onSeek} />);
    await user.click(screen.getByRole('button', { name: /Reveal answer/ }));
    await user.click(screen.getByRole('button', { name: /Watch this part/ }));
    expect(onSeek).toHaveBeenCalledWith(680);
  });

  it('should show the answer of a question marked Done without a Reveal click', async () => {
    const user = userEvent.setup();
    render(<StudyHarness />);
    await user.click(screen.getByRole('button', { name: /^Done$/ }));
    expect(screen.getByText('Answer:')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Reveal answer/ })).not.toBeInTheDocument();
  });

  it('should announce the revealed answer through a polite live region', async () => {
    const user = userEvent.setup();
    render(<StudyHarness />);
    await user.click(screen.getByRole('button', { name: /Reveal answer/ }));
    expect(screen.getByText('Answer:').closest('[aria-live="polite"]')).not.toBeNull();
  });

  it('should not offer a Reveal button when there is nothing to reveal', () => {
    render(<StudyHarness questions={[{ question: 'Open question?', options: [], correctIndex: 0 }]} />);
    expect(screen.queryByRole('button', { name: /Reveal answer/ })).not.toBeInTheDocument();
  });

  it('should hide the next question answer again after moving on from a revealed one', async () => {
    const user = userEvent.setup();
    render(<StudyHarness />);
    await user.click(screen.getByRole('button', { name: /Reveal answer/ }));
    await user.click(screen.getByRole('button', { name: /Next/ }));
    expect(screen.getByRole('button', { name: /Reveal answer/ })).toBeInTheDocument();
  });
});
