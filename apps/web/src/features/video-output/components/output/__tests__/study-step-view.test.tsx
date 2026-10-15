import { useState } from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { TabStateProvider } from '@/features/video-output/contexts/TabStateContext';
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

function StudyHarness({ onSeek }: { onSeek?: (s: number) => void }) {
  const [currentStep, setCurrentStep] = useState(0);
  return (
    <TabStateProvider videoId="study-step-test">
      <StudyStepView
        questions={QUESTIONS}
        currentStep={currentStep}
        onStepChange={setCurrentStep}
        onComplete={vi.fn()}
        onSeek={onSeek}
      />
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

  it('should hide the next question answer again after moving on from a revealed one', async () => {
    const user = userEvent.setup();
    render(<StudyHarness />);
    await user.click(screen.getByRole('button', { name: /Reveal answer/ }));
    await user.click(screen.getByRole('button', { name: /Next/ }));
    expect(screen.getByRole('button', { name: /Reveal answer/ })).toBeInTheDocument();
  });
});
