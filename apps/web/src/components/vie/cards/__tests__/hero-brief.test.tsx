import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect } from 'vitest';
import { HeroBrief } from '../HeroBrief';

// Long enough to pass the five-line clamp budget, so the "Show all" toggle renders.
const LONG_BRIEF = Array.from(
  { length: 8 },
  (_, i) => `Sentence number ${i + 1} of a long brief that runs well past five lines.`,
).join(' ');

describe('HeroBrief', () => {
  describe('Show all toggle', () => {
    it('should point aria-controls at the brief text it unrolls', () => {
      render(<HeroBrief masterSummary={LONG_BRIEF} isPhone={false} />);

      const toggle = screen.getByRole('button', { name: /^show all$/i });
      const controlled = document.getElementById(toggle.getAttribute('aria-controls') ?? '');

      expect(controlled).toHaveTextContent('Sentence number 1 of a long brief');
    });

    it('should report the brief as collapsed before the reader asks for all', () => {
      render(<HeroBrief masterSummary={LONG_BRIEF} isPhone={false} />);

      expect(screen.getByRole('button', { name: /^show all$/i })).toHaveAttribute('aria-expanded', 'false');
    });

    it('should report the brief as expanded once it is unrolled', async () => {
      const user = userEvent.setup();
      render(<HeroBrief masterSummary={LONG_BRIEF} isPhone={false} />);

      await user.click(screen.getByRole('button', { name: /^show all$/i }));

      expect(screen.getByRole('button', { name: 'Hide brief' })).toHaveAttribute('aria-expanded', 'true');
    });
  });
});
