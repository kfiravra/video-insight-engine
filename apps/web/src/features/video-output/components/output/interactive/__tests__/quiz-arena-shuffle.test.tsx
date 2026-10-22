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

// p3 review A1: quiz v2 dealt these keys to positions 2, 0, 1 — the page keeps them there.
const SERVER_DEALT: QuizArenaQuestion[] = [
  { question: 'Why pay the card first?', options: ['Cards are forgiven', 'Funds never grow', 'Its rate beats the fund', 'The bank asks'], correctIndex: 2 },
  { question: 'What goes into the buffer?', options: ['One month of expenses', 'A year of rent', 'Every spare dollar', 'Nothing at all'], correctIndex: 0 },
  { question: 'When does the dollar go to the fund?', options: ['Before the card', 'After the card is paid', 'Every second month', 'Only in a bad year'], correctIndex: 1 },
];

function shownOptions(stored: string[]): string[] {
  return screen.getAllByRole('button').map((b) => b.textContent ?? '').filter((text) => stored.includes(text));
}

describe('QuizArena with a server-dealt quiz', () => {
  it('should show the options in their stored order when the server dealt the keys', () => {
    render(<QuizArena questions={SERVER_DEALT} />);
    expect(shownOptions(SERVER_DEALT[0].options)).toEqual(SERVER_DEALT[0].options);
  });
});

// p3 review A4: translation keeps the order and correctIndex; the language toggle swaps
// `questions` under the mounted arena, so the answers given before it must still count.
const LEGACY_EN: QuizArenaQuestion[] = [
  { question: QUESTION, options: ['Kodak Brownie', 'Leica M3', 'Polaroid SX-70', 'Hasselblad 500'], correctIndex: 0, timestamp: 95 },
  { question: 'What did the speaker say made film cheap?', options: ['Mass production', 'Tax cuts', 'Silver mines', 'Digital sensors'], correctIndex: 0, timestamp: 410 },
];
const LEGACY_ES: QuizArenaQuestion[] = [
  { question: '¿Qué cámara llamó el orador el gran igualador?', options: ['Kodak Brownie (es)', 'Leica M3 (es)', 'Polaroid SX-70 (es)', 'Hasselblad 500 (es)'], correctIndex: 0, timestamp: 95 },
  { question: '¿Qué dijo el orador que abarató la película?', options: ['Producción en masa', 'Recortes de impuestos', 'Minas de plata', 'Sensores digitales'], correctIndex: 0, timestamp: 410 },
];

describe('QuizArena language switch', () => {
  it('should keep every answer marked right when the quiz switches language after it was answered', async () => {
    const user = userEvent.setup();
    const { rerender } = render(<QuizArena questions={LEGACY_EN} />);
    await user.click(screen.getByRole('button', { name: 'Kodak Brownie' }));
    await user.click(screen.getByRole('button', { name: 'Next' }));
    await user.click(screen.getByRole('button', { name: 'Mass production' }));
    rerender(<QuizArena questions={LEGACY_ES} />);
    expect(screen.getByTestId('celebration')).toHaveTextContent('Perfect score!');
  });

  it('should show a server-dealt question in the same order when its language switches', () => {
    const spanish = SERVER_DEALT.map((q) => ({ ...q, options: q.options.map((o) => `${o} (es)`) }));
    const { rerender } = render(<QuizArena questions={SERVER_DEALT} />);
    rerender(<QuizArena questions={spanish} />);
    expect(shownOptions(spanish[0].options)).toEqual(spanish[0].options);
  });
});
