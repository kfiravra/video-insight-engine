import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';

import { SpotExplorer } from '../SpotExplorer';

// Podcast positions (output-quality 1.3 / 1.11): who holds each claim lives in `speaker`.
const mixed = [
  { name: 'Name the future you want first', description: 'why', quote: 'start with the goals', speaker: 'Max Tegmark' },
  { name: 'AI is the moon shot of the century', description: 'why', speaker: 'Lex Fridman' },
];
const oneGuest = [
  { name: 'Quattro is the future of the PC', description: 'why', speaker: 'DHH' },
  { name: 'The foundation has $8M pledged', description: 'why', speaker: 'DHH' },
];

describe('SpotExplorer', () => {
  describe('speaker attribution', () => {
    it('should name who holds each position when the tab mixes speakers', () => {
      render(<SpotExplorer spots={mixed} />);

      expect(screen.getByText('— Max Tegmark')).toBeInTheDocument();
    });

    it('should name the host too when the host holds a position', () => {
      render(<SpotExplorer spots={mixed} />);

      expect(screen.getByText('— Lex Fridman')).toBeInTheDocument();
    });

    it('should not repeat the name when one guest holds every position', () => {
      render(<SpotExplorer spots={oneGuest} />);

      expect(screen.queryByText('— DHH')).not.toBeInTheDocument();
    });
  });
});
