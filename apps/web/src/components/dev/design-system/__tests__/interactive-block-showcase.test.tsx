import { describe, it, expect } from 'vitest';
import { render, screen, within } from '@testing-library/react';

import { InteractiveBlockShowcase } from '../InteractiveBlockShowcase';

// p1 review R9: the showcase demos the claims tracker in D11's words — stated /
// supported / contested — and never names a "Reporter" as a claim's source.
function claimsSection(): HTMLElement {
  render(<InteractiveBlockShowcase />);
  return screen.getByTestId('showcase-claims-tracker');
}

describe('InteractiveBlockShowcase', () => {
  describe('ClaimsTracker demo', () => {
    it('should describe the statuses in the D11 words', () => {
      expect(within(claimsSection()).getByText(/stated \/ supported \/ contested/)).toBeInTheDocument();
    });

    it('should never print the pre-D11 status words', () => {
      expect(within(claimsSection()).queryByText(/verified|disputed/i)).not.toBeInTheDocument();
    });

    it('should attribute no claim to a "Reporter"', () => {
      expect(within(claimsSection()).queryByText(/Reporter/)).not.toBeInTheDocument();
    });
  });
});
