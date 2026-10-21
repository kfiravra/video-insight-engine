import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';

import { SpotExplorer } from '../SpotExplorer';

// Key ideas (output-quality 1.1, p1 review R2): the assembler ships an idea's kind as
// `category` ("Thesis", "Ask") — the card's badge beside the claim.
const ideas = [
  { name: 'Hand-written code stops being productive this year', description: 'Agents write more', category: 'Thesis', timestamp: 1245 },
  { name: 'Try an agent on one real task this week', description: 'The close', category: 'Ask' },
];

describe('SpotExplorer', () => {
  describe('kind badge', () => {
    it('should badge the card with its kind when the spot carries a category', () => {
      render(<SpotExplorer spots={ideas} />);

      expect(screen.getByText('Thesis')).toBeInTheDocument();
    });

    it('should badge every card with its own kind when the ideas differ in kind', () => {
      render(<SpotExplorer spots={ideas} />);

      expect(screen.getByText('Ask')).toBeInTheDocument();
    });
  });
});
