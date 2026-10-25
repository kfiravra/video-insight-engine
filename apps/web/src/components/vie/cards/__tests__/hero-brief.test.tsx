import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect } from 'vitest';
import { HeroBrief } from '../HeroBrief';

// Long enough to pass both clamp budgets, so the "more" / "Show all" toggles render.
const LONG_BRIEF = Array.from(
  { length: 8 },
  (_, i) => `Sentence number ${i + 1} of a long brief that runs well past five lines.`,
).join(' ');

describe('HeroBrief', () => {
  describe('desktop "more" toggle', () => {
    it('should point aria-controls at the brief text it unrolls', () => {
      render(<HeroBrief masterSummary={LONG_BRIEF} isPhone={false} />);

      const toggle = screen.getByRole('button', { name: 'Read more of the brief' });
      const controlled = document.getElementById(toggle.getAttribute('aria-controls') ?? '');

      expect(controlled).toHaveTextContent('Sentence number 1 of a long brief');
    });

    it('should report the brief as collapsed before the reader asks for more', () => {
      render(<HeroBrief masterSummary={LONG_BRIEF} isPhone={false} />);

      expect(screen.getByRole('button', { name: 'Read more of the brief' })).toHaveAttribute('aria-expanded', 'false');
    });

    it('should report the brief as expanded once it is unrolled', async () => {
      const user = userEvent.setup();
      render(<HeroBrief masterSummary={LONG_BRIEF} isPhone={false} />);

      await user.click(screen.getByRole('button', { name: 'Read more of the brief' }));

      expect(screen.getByRole('button', { name: 'Hide brief' })).toHaveAttribute('aria-expanded', 'true');
    });
  });

  describe('phone "Show all" toggle', () => {
    it('should point aria-controls at the brief text it unrolls', async () => {
      const user = userEvent.setup();
      render(<HeroBrief masterSummary={LONG_BRIEF} isPhone />);
      await user.click(screen.getByRole('button', { name: 'Brief' }));

      const toggle = screen.getByRole('button', { name: /^show all$/i });
      const controlled = document.getElementById(toggle.getAttribute('aria-controls') ?? '');

      expect(controlled).toHaveTextContent('Sentence number 1 of a long brief');
    });

    it('should report the brief as collapsed before the reader asks for all', async () => {
      const user = userEvent.setup();
      render(<HeroBrief masterSummary={LONG_BRIEF} isPhone />);
      await user.click(screen.getByRole('button', { name: 'Brief' }));

      expect(screen.getByRole('button', { name: /^show all$/i })).toHaveAttribute('aria-expanded', 'false');
    });

    it('should report the brief as expanded once it is unrolled', async () => {
      const user = userEvent.setup();
      render(<HeroBrief masterSummary={LONG_BRIEF} isPhone />);
      await user.click(screen.getByRole('button', { name: 'Brief' }));

      await user.click(screen.getByRole('button', { name: /^show all$/i }));

      expect(screen.getByRole('button', { name: 'Hide brief' })).toHaveAttribute('aria-expanded', 'true');
    });
  });
});
