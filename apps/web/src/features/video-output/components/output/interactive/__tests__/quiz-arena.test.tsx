import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock('../../Celebration', () => ({
  Celebration: ({ title }: { title: string }) => <div data-testid="celebration">{title}</div>,
}));

import { QuizArena, type QuizArenaQuestion } from '../QuizArena';

const questions: QuizArenaQuestion[] = [
  {
    question: 'What is 2+2?',
    options: ['3', '4', '5'],
    correctIndex: 1,
    explanation: 'Simple math.',
  },
  {
    question: 'Capital of France?',
    options: ['London', 'Paris', 'Berlin'],
    correctIndex: 1,
    explanation: 'It is Paris.',
  },
  {
    question: 'Largest planet?',
    options: ['Earth', 'Mars', 'Jupiter'],
    correctIndex: 2,
    explanation: 'Jupiter is largest.',
  },
];

const scenarioQuestion: QuizArenaQuestion = {
  question: 'What do you do?',
  options: ['Run', 'Hide', 'Help'],
  correctIndex: 2,
  context: 'A customer is asking for support during the keynote demo.',
  kind: 'scenario',
  explanation: 'Always help.',
};

describe('QuizArena', () => {
  it('should render the first question', () => {
    render(<QuizArena questions={questions} withTimer={false} />);
    expect(screen.getByText('What is 2+2?')).toBeInTheDocument();
    expect(screen.getByText('Q 1 of 3')).toBeInTheDocument();
  });

  it('renders an empty state for empty questions', () => {
    const { container } = render(<QuizArena questions={[]} />);
    expect(container.textContent).toContain('No quiz questions were generated');
  });

  it('should show explanation after selecting correct answer', () => {
    render(<QuizArena questions={questions} withTimer={false} />);
    fireEvent.click(screen.getByText('4'));
    expect(screen.getByText('Explanation')).toBeInTheDocument();
    expect(screen.getByText('Simple math.')).toBeInTheDocument();
  });

  it('should show explanation after selecting a wrong answer', () => {
    render(<QuizArena questions={questions} withTimer={false} />);
    fireEvent.click(screen.getByText('3'));
    expect(screen.getByText('Simple math.')).toBeInTheDocument();
  });

  it('should increment streak counter on consecutive correct answers', () => {
    render(<QuizArena questions={questions} withTimer={false} />);
    // Answer Q1 correctly
    fireEvent.click(screen.getByText('4'));
    // No streak badge yet (streak >= 2 threshold)
    expect(screen.queryByText(/streak!/)).toBeNull();
    // Move to Q2
    fireEvent.click(screen.getByText('Next'));
    fireEvent.click(screen.getByText('Paris'));
    // Streak should now show "2 streak!"
    expect(screen.getByText(/2 streak!/)).toBeInTheDocument();
  });

  it('should persist best score to localStorage when videoId is provided', () => {
    render(<QuizArena questions={questions} withTimer={false} videoId="abc-123" />);
    // Answer all 3 correctly
    fireEvent.click(screen.getByText('4'));
    fireEvent.click(screen.getByText('Next'));
    fireEvent.click(screen.getByText('Paris'));
    fireEvent.click(screen.getByText('Next'));
    fireEvent.click(screen.getByText('Jupiter'));
    expect(window.localStorage.getItem('vie:quiz:abc-123')).toBe('3');
  });

  it('should NOT persist best score when videoId is omitted', () => {
    render(<QuizArena questions={questions.slice(0, 1)} withTimer={false} />);
    fireEvent.click(screen.getByText('4'));
    // No videoId → no key stored.
    expect(window.localStorage.getItem('vie:quiz:undefined')).toBeNull();
  });

  it('should restart the quiz when Try Again is clicked', () => {
    render(<QuizArena questions={questions.slice(0, 1)} withTimer={false} />);
    fireEvent.click(screen.getByText('4'));
    // Single-question quiz reaches completion immediately
    const tryAgain = screen.getByRole('button', { name: /try again/i });
    fireEvent.click(tryAgain);
    // After reset, the explanation is no longer in the DOM.
    expect(screen.queryByText('Explanation')).toBeNull();
    expect(screen.getByText('What is 2+2?')).toBeInTheDocument();
  });

  it('should render the scenario context blockquote when provided', () => {
    render(<QuizArena questions={[scenarioQuestion]} withTimer={false} />);
    expect(
      screen.getByText('A customer is asking for support during the keynote demo.'),
    ).toBeInTheDocument();
  });

  it('should be untimed by default (output-quality R12/D9)', () => {
    render(<QuizArena questions={questions} />);
    expect(screen.queryByRole('timer')).not.toBeInTheDocument();
  });

  it('should render the timer ring only when withTimer is opted in', () => {
    render(<QuizArena questions={questions} withTimer timerSeconds={10} />);
    expect(screen.getByRole('timer', { name: '10 seconds remaining' })).toBeInTheDocument();
  });

  describe('explanation seek chips (output-quality 0a.7)', () => {
    const timedQuestion: QuizArenaQuestion = {
      question: 'What does DHH compare Opus 4.5 to?',
      options: ['A typewriter', 'The Kodak Brownie', 'A calculator'],
      correctIndex: 1,
      explanation: 'DHH compares Opus 4.5 to the Kodak Brownie (11:20) as an inflection point.',
    };

    it('should render "(11:20)" as a chip that seeks the player', async () => {
      const user = userEvent.setup();
      const onSeek = vi.fn();
      render(<QuizArena questions={[timedQuestion]} onSeek={onSeek} />);
      await user.click(screen.getByText('The Kodak Brownie'));
      await user.click(screen.getByRole('button', { name: 'Jump to 11:20' }));
      expect(onSeek).toHaveBeenCalledWith(680);
    });

    it('should keep the surrounding explanation text around the chip', () => {
      render(<QuizArena questions={[timedQuestion]} onSeek={vi.fn()} />);
      fireEvent.click(screen.getByText('The Kodak Brownie'));
      expect(screen.getByText(/compares Opus 4\.5 to the Kodak Brownie/)).toBeInTheDocument();
      expect(screen.getByText(/as an inflection point\./)).toBeInTheDocument();
      expect(screen.queryByText(/\(11:20\)/)).not.toBeInTheDocument();
    });

    it('should render a static chip when no seek handler is given', () => {
      render(<QuizArena questions={[timedQuestion]} />);
      fireEvent.click(screen.getByText('The Kodak Brownie'));
      expect(screen.getByText('11:20')).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /jump to/i })).not.toBeInTheDocument();
    });

    it('should render no chip when the explanation has no time reference', () => {
      render(<QuizArena questions={questions.slice(0, 1)} onSeek={vi.fn()} />);
      fireEvent.click(screen.getByText('4'));
      expect(screen.queryByRole('button', { name: /jump to/i })).not.toBeInTheDocument();
    });
  });

  describe('question timestamp chip (output-quality 1.13)', () => {
    const stampedQuestion: QuizArenaQuestion = {
      question: 'Why does paying the card beat investing the same dollar?',
      options: ['A 24% APR costs more than a 7% fund returns', 'Card debt is forgiven', 'Funds never return 7%'],
      correctIndex: 0,
      explanation: 'Every dollar on the card loses 24 cents a year.',
      timestamp: 312,
    };

    it('should seek to the answer timestamp once the question is answered', async () => {
      const user = userEvent.setup();
      const onSeek = vi.fn();
      render(<QuizArena questions={[stampedQuestion]} onSeek={onSeek} />);
      await user.click(screen.getByText('Card debt is forgiven'));
      await user.click(screen.getByRole('button', { name: 'Jump to 5:12' }));
      expect(onSeek).toHaveBeenCalledWith(312);
    });

    it('should not reveal the answer timestamp before the question is answered', () => {
      render(<QuizArena questions={[stampedQuestion]} onSeek={vi.fn()} />);
      expect(screen.queryByRole('button', { name: 'Jump to 5:12' })).not.toBeInTheDocument();
    });
  });

  describe('aria-live answer feedback', () => {
    it('should expose an initially empty polite live region', () => {
      render(<QuizArena questions={questions} withTimer={false} />);
      const region = screen.getByRole('status');
      expect(region).toHaveAttribute('aria-live', 'polite');
      expect(region).toHaveTextContent('');
    });

    it('should announce "Correct!" when the right answer is selected', () => {
      render(<QuizArena questions={questions} withTimer={false} />);
      fireEvent.click(screen.getByText('4'));
      expect(screen.getByRole('status')).toHaveTextContent('Correct!');
    });

    it('should announce the correct answer when a wrong answer is selected', () => {
      render(<QuizArena questions={questions} withTimer={false} />);
      fireEvent.click(screen.getByText('3'));
      expect(screen.getByRole('status')).toHaveTextContent(
        'Incorrect. The correct answer is 4.',
      );
    });

    it('should clear the announcement when the quiz is reset', () => {
      render(<QuizArena questions={questions.slice(0, 1)} withTimer={false} />);
      fireEvent.click(screen.getByText('4'));
      fireEvent.click(screen.getByRole('button', { name: /try again/i }));
      expect(screen.getByRole('status')).toHaveTextContent('');
    });
  });
});
