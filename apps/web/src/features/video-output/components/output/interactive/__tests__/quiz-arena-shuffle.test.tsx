import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock('../../Celebration', () => ({
  Celebration: ({ title }: { title: string }) => <div data-testid="celebration">{title}</div>,
}));

import { QuizArena, type QuizArenaQuestion } from '../QuizArena';

const QUESTION = 'Which camera did the speaker call the great equalizer?';
const questions: QuizArenaQuestion[] = [
  {
    question: QUESTION,
    options: ['Kodak Brownie', 'Leica M3', 'Polaroid SX-70', 'Hasselblad 500'],
    correctIndex: 0,
    explanation: 'The Brownie put cameras in every hand.',
  },
];

function optionTexts(): string[] {
  return questions[0].options.filter((o) => screen.queryByText(o) !== null);
}

describe('QuizArena option order', () => {
  it('should show every stored option once after the shuffle', () => {
    render(<QuizArena questions={questions} />);
    expect(optionTexts()).toHaveLength(4);
  });

  it('should mark the answer correct when the reader clicks the keyed option text', async () => {
    const user = userEvent.setup();
    render(<QuizArena questions={questions} />);
    await user.click(screen.getByText('Kodak Brownie'));
    expect(screen.getByText('The Brownie put cameras in every hand.')).toBeInTheDocument();
    expect(screen.getByTestId('celebration')).toHaveTextContent('Perfect score!');
  });

  it('should name the keyed option as the correct one in the missed summary when the reader picks a distractor', async () => {
    const user = userEvent.setup();
    render(<QuizArena questions={questions} />);
    await user.click(screen.getByText('Leica M3'));
    expect(screen.getByText('Correct: Kodak Brownie')).toBeInTheDocument();
  });

  it('should list options in the same order on a second render', () => {
    const orderOf = () =>
      screen.getAllByRole('button').map((b) => b.textContent ?? '').filter((t) => questions[0].options.some((o) => t.includes(o)));
    const first = render(<QuizArena questions={questions} />);
    const before = orderOf();
    first.unmount();
    render(<QuizArena questions={questions} />);
    expect(orderOf()).toEqual(before);
  });
});
