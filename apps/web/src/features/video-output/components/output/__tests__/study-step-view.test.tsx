import { useState } from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
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

  it('should turn an (m:ss) reference in the explanation into a seek chip', async () => {
    const user = userEvent.setup();
    const onSeek = vi.fn();
    const unstamped = { ...QUESTIONS[0], explanation: 'Covered at (2:05).', timestamp: undefined };
    render(<StudyHarness onSeek={onSeek} questions={[unstamped]} />);
    await user.click(screen.getByRole('button', { name: /Reveal answer/ }));
    await user.click(screen.getByRole('button', { name: 'Jump to 2:05' }));
    expect(onSeek).toHaveBeenCalledWith(125);
  });

  // p1 review R6: Study checks the explanation's time against the question's, like the quiz tab.
  it('should seek the explanation chip to the checked timestamp when the cited time is off', async () => {
    const user = userEvent.setup();
    const onSeek = vi.fn();
    render(<StudyHarness onSeek={onSeek} questions={[{ ...QUESTIONS[0], explanation: 'Covered at (2:05).' }]} />);
    await user.click(screen.getByRole('button', { name: /Reveal answer/ }));
    await user.click(screen.getByRole('button', { name: /^Jump to/ }));
    expect(onSeek).toHaveBeenCalledWith(680);
  });

  it('should offer one seek control when the explanation cites no time', async () => {
    const user = userEvent.setup();
    render(<StudyHarness onSeek={vi.fn()} questions={[{ ...QUESTIONS[0], explanation: 'He compares it to the Brownie.' }]} />);
    await user.click(screen.getByRole('button', { name: /Reveal answer/ }));
    expect(screen.queryByRole('button', { name: /^Jump to/ })).not.toBeInTheDocument();
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

/** The option rows, in the order shown. */
function shownRows(): HTMLElement[] {
  return screen.getAllByRole('listitem');
}

/** The rows announced as the correct answer. */
function markedRows(): HTMLElement[] {
  return shownRows().filter((li) => within(li).queryByText('(correct answer)') !== null);
}

// Legacy rows (flag-off / pre-3.0): every key stored at option 0, so the page deals them.
const LEGACY_EN: QuizArenaQuestion[] = [
  { question: 'Which camera did the speaker call the great equalizer?', options: ['Kodak Brownie', 'Leica M3', 'Polaroid SX-70', 'Hasselblad 500'], correctIndex: 0, timestamp: 95 },
  { question: 'What did the speaker say made film cheap?', options: ['Mass production', 'Tax cuts', 'Silver mines', 'Digital sensors'], correctIndex: 0, timestamp: 410 },
];
const LEGACY_ES: QuizArenaQuestion[] = [
  { question: '¿Qué cámara llamó el orador el gran igualador?', options: ['Kodak Brownie (es)', 'Leica M3 (es)', 'Polaroid SX-70 (es)', 'Hasselblad 500 (es)'], correctIndex: 0, timestamp: 95 },
  { question: '¿Qué dijo el orador que abarató la película?', options: ['Producción en masa', 'Recortes de impuestos', 'Minas de plata', 'Sensores digitales'], correctIndex: 0, timestamp: 410 },
];

describe('StudyStepView option order', () => {
  it('should mark the keyed option as the answer after reveal regardless of its position', async () => {
    const user = userEvent.setup();
    render(<StudyHarness questions={LEGACY_EN} />);
    await user.click(screen.getByRole('button', { name: /reveal answer/i }));
    expect(markedRows().map((li) => li.textContent)).toEqual([expect.stringContaining('Kodak Brownie')]);
  });

  // p3 review A1: QUESTIONS' keys sit at positions 0 and 1 — the server's deal.
  it('should show the options in their stored order when the server dealt the keys', () => {
    render(<StudyHarness />);
    expect(shownRows().map((li) => li.textContent)).toEqual(['A.Kodak Brownie', 'B.Leica camera', 'C.Polaroid']);
  });

  // p3 review A4: the same question keeps its key's row when the language switches.
  it('should keep the answer on the same row when a legacy quiz switches language', async () => {
    const user = userEvent.setup();
    const { rerender } = render(<StudyHarness questions={LEGACY_EN} />);
    await user.click(screen.getByRole('button', { name: /reveal answer/i }));
    const before = shownRows().indexOf(markedRows()[0]);
    rerender(<StudyHarness questions={LEGACY_ES} />);
    expect(shownRows().indexOf(markedRows()[0])).toBe(before);
  });
});
