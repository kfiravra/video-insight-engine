import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

vi.mock('../../Celebration', () => ({
  Celebration: ({ title }: { title: string }) => <div data-testid="celebration">{title}</div>,
}));

import { FlashDeckInteractive } from '../FlashDeckInteractive';

const cards = [
  { front: 'What is React?', back: 'A UI library', emoji: '⚛️' },
  { front: 'What is JSX?', back: 'JavaScript XML syntax', category: 'Syntax' },
];

describe('FlashDeckInteractive', () => {
  it('should render the first card front', () => {
    render(<FlashDeckInteractive cards={cards} />);
    expect(screen.getByText('What is React?')).toBeInTheDocument();
    expect(screen.getByText('1 of 2')).toBeInTheDocument();
    // The flip hint is "Tap <kbd>Space</kbd> to flip" — verify the kbd renders
    const spaceHint = screen.getByText('Space');
    expect(spaceHint.tagName).toBe('KBD');
  });

  it('renders an empty state for empty cards', () => {
    const { container } = render(<FlashDeckInteractive cards={[]} />);
    expect(container.textContent).toContain('No flashcards were extracted');
  });

  it('should flip card on click', () => {
    render(<FlashDeckInteractive cards={cards} />);
    const flipTarget = screen.getByLabelText('What is React?');
    fireEvent.click(flipTarget);
    expect(screen.getByText('A UI library')).toBeInTheDocument();
  });

  it('should show Got it / Review again buttons after flip', () => {
    render(<FlashDeckInteractive cards={cards} />);
    fireEvent.click(screen.getByLabelText('What is React?'));
    expect(screen.getByText('Got it!')).toBeInTheDocument();
    expect(screen.getByText('Review again')).toBeInTheDocument();
  });

  it('should show shuffle button when shuffleable', () => {
    render(<FlashDeckInteractive cards={cards} shuffleable />);
    expect(screen.getByLabelText('Shuffle cards')).toBeInTheDocument();
  });

  it('should let a quote card read in its own direction when its front is a Hebrew line', () => {
    const quoteCard = { front: 'אנחנו לא יודעים.', back: 'We do not know. — רן לוי · 43:16' };
    render(<FlashDeckInteractive cards={[quoteCard]} />);
    expect(screen.getByText(quoteCard.front)).toHaveAttribute('dir', 'auto');
  });

  it("should let a card's back read in its own direction when it is flipped", () => {
    render(<FlashDeckInteractive cards={cards} />);
    fireEvent.click(screen.getByLabelText('What is React?'));
    expect(screen.getByText('A UI library')).toHaveAttribute('dir', 'auto');
  });

  it('should show the English of a quote card on its back when the card carries quoteEn', () => {
    const quoteCard = { front: 'אנחנו לא יודעים.', back: '— רן לוי · 43:16', quoteEn: 'We do not know.' };
    render(<FlashDeckInteractive cards={[quoteCard]} />);
    fireEvent.click(screen.getByLabelText(quoteCard.front));
    expect(screen.getByText('We do not know.')).toHaveAttribute('lang', 'en');
  });

  it('should show navigation for multiple cards', () => {
    render(<FlashDeckInteractive cards={cards} />);
    // BackForward component renders buttons
    const buttons = screen.getAllByRole('button');
    expect(buttons.length).toBeGreaterThan(0);
  });
});
