import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock('../../Celebration', () => ({
  Celebration: ({ title }: { title: string }) => <div data-testid="celebration">{title}</div>,
}));

import { QuizArena, type QuizArenaQuestion } from '../QuizArena';
import { QuizExplanation } from '../QuizExplanation';
import { TAB_PROP_SCHEMAS } from '@/features/video-output/lib/tab-prop-schemas';

// output-quality 2.1b (quiz v2): every question carries the speaker's words that settle
// its answer — one quiet line under the explanation, in the quote style the spot cards
// and moments use, carrying the answer's seek chip. A translated line is prose (2.0 d).
const QUOTE = 'A card at 24% costs you more than any index fund returns at 7%.';
const EXPLANATION = 'The card costs more than the fund earns.';

const question: QuizArenaQuestion = {
  question: 'Why does the speaker pay the card before investing?',
  options: ['The card costs more than the fund returns', 'Card debt is forgiven', 'Funds never grow'],
  correctIndex: 0,
  explanation: EXPLANATION,
  timestamp: 30,
  quote: QUOTE,
};

function quoteElement(text: string): Element | null {
  return screen.getByText(text).closest('blockquote');
}

describe('quiz evidence quote', () => {
  describe('QuizExplanation', () => {
    it('should set the evidence apart as a quotation when the question carries a quote', () => {
      render(<QuizExplanation text={EXPLANATION} timestamp={30} quote={QUOTE} />);

      expect(screen.getByRole('blockquote')).toHaveTextContent(QUOTE);
    });

    it('should render the evidence as prose when the quote is flagged translated', () => {
      render(<QuizExplanation text={EXPLANATION} timestamp={30} quote={QUOTE} quoteTranslated />);

      expect(quoteElement(QUOTE)).toBeNull();
    });

    it('should seek to the answer from the chip on the evidence line', async () => {
      const user = userEvent.setup();
      const onSeek = vi.fn();
      render(<QuizExplanation text={EXPLANATION} timestamp={30} quote={QUOTE} onSeek={onSeek} />);

      await user.click(screen.getByRole('button', { name: 'Jump to 0:30' }));

      expect(onSeek).toHaveBeenCalledWith(30);
    });

    it('should show the answer time once when the explanation and the quote are both given', () => {
      render(<QuizExplanation text={EXPLANATION} timestamp={30} quote={QUOTE} onSeek={vi.fn()} />);

      expect(screen.getAllByRole('button', { name: /jump to/i })).toHaveLength(1);
    });

    it('should keep the explanation\'s own cited time as the only chip when it cites one', () => {
      render(<QuizExplanation text="Said at (0:30) exactly." timestamp={30} quote={QUOTE} onSeek={vi.fn()} />);

      expect(screen.getAllByRole('button', { name: /jump to/i })).toHaveLength(1);
    });

    it('should render no evidence line when the quote is blank', () => {
      render(<QuizExplanation text={EXPLANATION} timestamp={30} quote="  " />);

      expect(screen.queryByRole('blockquote')).not.toBeInTheDocument();
    });
  });

  // pC review (D12): a non-English lecture's evidence line is verbatim in the
  // source language — it reads in its own direction, with its English beneath.
  describe('non-English evidence line', () => {
    const HEBREW = 'הבעיה האמיתית היא לא מה שהוא החליט, אלא מי נתן לו להחליט לבד.';
    const ENGLISH = "The real problem isn't what he decided, but who let it decide on its own.";

    it('should read the evidence line in its own direction when it is verbatim', () => {
      render(<QuizExplanation text={EXPLANATION} timestamp={30} quote={HEBREW} />);

      expect(screen.getByRole('blockquote')).toHaveAttribute('dir', 'auto');
    });

    it('should show the English beneath a verbatim line when the question carries it', () => {
      render(<QuizExplanation text={EXPLANATION} timestamp={30} quote={HEBREW} quoteEn={ENGLISH} />);

      expect(screen.getByText(ENGLISH)).toHaveAttribute('lang', 'en');
    });

    it('should keep the chip out of the right-to-left line when the quote is Hebrew', () => {
      render(<QuizExplanation text={EXPLANATION} timestamp={30} quote={HEBREW} onSeek={vi.fn()} />);

      expect(screen.getByRole('blockquote')).not.toContainElement(screen.getByRole('button', { name: 'Jump to 0:30' }));
    });

    it('should carry the English through the quiz once the question is answered', async () => {
      const user = userEvent.setup();
      render(<QuizArena questions={[{ ...question, quote: HEBREW, quoteEn: ENGLISH }]} onSeek={vi.fn()} />);

      await user.click(screen.getByText('Card debt is forgiven'));

      expect(screen.getByText(ENGLISH)).toBeInTheDocument();
    });
  });

  describe('QuizArena', () => {
    it('should not reveal the evidence quote before the question is answered', () => {
      render(<QuizArena questions={[question]} onSeek={vi.fn()} />);

      expect(screen.queryByText(QUOTE)).not.toBeInTheDocument();
    });

    it('should show the evidence quote once the question is answered', async () => {
      const user = userEvent.setup();
      render(<QuizArena questions={[question]} onSeek={vi.fn()} />);

      await user.click(screen.getByText('Card debt is forgiven'));

      expect(quoteElement(QUOTE)).not.toBeNull();
    });
  });

  describe('quiz_arena prop schema', () => {
    it('should accept a question with an evidence quote and its translated flag', () => {
      const props = { questions: [{ ...question, quoteTranslated: true }] };

      expect(TAB_PROP_SCHEMAS.quiz_arena.schema.safeParse(props).success).toBe(true);
    });

    it('should refuse a question whose quote is not text', () => {
      const props = { questions: [{ ...question, quote: ['not', 'text'] }] };

      expect(TAB_PROP_SCHEMAS.quiz_arena.schema.safeParse(props).success).toBe(false);
    });
  });
});
