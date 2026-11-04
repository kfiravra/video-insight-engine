import { describe, it, expect } from 'vitest';
import { screen } from '@testing-library/react';
import { TokensCallout } from '../TokensCallout';
import type { TokensNotice } from '@/lib/billing-errors';
import { renderIn } from './usage-fixtures';

const insufficient = (overrides: Partial<Extract<TokensNotice, { kind: 'insufficient' }>> = {}): TokensNotice => ({
  kind: 'insufficient',
  needed: 840,
  available: 300,
  ...overrides,
});

describe('TokensCallout', () => {
  describe('not enough tokens (402)', () => {
    it("should name the video's estimate and the balance", () => {
      renderIn(<TokensCallout notice={insufficient()} />);

      expect(screen.getByRole('alert')).toHaveTextContent('This video needs ≈ 840 tokens and you have 300 tokens.');
    });

    it('should use the singular for one token', () => {
      renderIn(<TokensCallout notice={insufficient({ needed: 750, available: 1 })} />);

      expect(screen.getByRole('alert')).toHaveTextContent('you have 1 token.');
    });

    it('should group thousands', () => {
      renderIn(<TokensCallout notice={insufficient({ needed: 3_900, available: 1_200 })} />);

      expect(screen.getByRole('alert')).toHaveTextContent('needs ≈ 3,900 tokens and you have 1,200 tokens');
    });

    it('should lead with the shortfall when the balance is below zero', () => {
      renderIn(<TokensCallout notice={insufficient({ available: -120 })} />);

      const alert = screen.getByRole('alert');
      expect(alert).toHaveTextContent("You're 120 tokens short — add tokens to continue.");
      expect(alert).toHaveTextContent('This video needs ≈ 840 tokens on top of that.');
    });

    it('should not print a negative number in the debt wording', () => {
      renderIn(<TokensCallout notice={insufficient({ available: -120 })} />);

      expect(screen.getByRole('alert')).not.toHaveTextContent('-120');
    });

    it('should fall back to a plain sentence when the amounts are not sent', () => {
      renderIn(<TokensCallout notice={insufficient({ needed: null, available: null })} />);

      expect(screen.getByRole('alert')).toHaveTextContent("You don't have enough tokens for this video.");
    });

    it('should link to the billing settings page with "Add tokens"', () => {
      renderIn(<TokensCallout notice={insufficient()} />);

      expect(screen.getByRole('link', { name: 'Add tokens' })).toHaveAttribute('href', '/settings/billing');
    });

    it('should not offer an upgrade or a price', () => {
      renderIn(<TokensCallout notice={insufficient()} />);

      expect(screen.getByRole('alert').textContent).not.toMatch(/\$|upgrade/i);
    });
  });

  describe('too long (422)', () => {
    it('should name the longest video the account can process', () => {
      renderIn(<TokensCallout notice={{ kind: 'too_long', maxMinutes: 180 }} />);

      expect(screen.getByRole('alert')).toHaveTextContent('This account can process videos up to 180 minutes long.');
    });

    it('should fall back to a plain sentence when the limit is not sent', () => {
      renderIn(<TokensCallout notice={{ kind: 'too_long', maxMinutes: null }} />);

      expect(screen.getByRole('alert')).toHaveTextContent('This video is longer than this account can process.');
    });

    it('should not point at the billing page, since tokens cannot lift the length cap', () => {
      renderIn(<TokensCallout notice={{ kind: 'too_long', maxMinutes: 180 }} />);

      expect(screen.queryByRole('link')).toBeNull();
    });
  });

  describe('right-to-left languages', () => {
    it('should put the Arabic dual in the oblique case after "up to"', () => {
      renderIn(<TokensCallout notice={{ kind: 'too_long', maxMinutes: 2 }} />, 'ar');

      expect(screen.getByRole('alert')).toHaveTextContent('حتى دقيقتين');
    });

    it('should word the video length limit in Arabic', () => {
      renderIn(<TokensCallout notice={{ kind: 'too_long', maxMinutes: 180 }} />, 'ar');

      expect(screen.getByRole('alert')).toHaveTextContent('حتى 180 دقيقة');
    });

    it('should keep Latin digits in the Arabic table', () => {
      renderIn(<TokensCallout notice={insufficient({ needed: 3_900, available: 1_200 })} />, 'ar');

      expect(screen.getByRole('alert').textContent).toMatch(/3,900[\s\S]*1,200/);
    });

    it('should render inside a right-to-left container and keep the link on billing', () => {
      renderIn(<TokensCallout notice={insufficient()} />, 'he');

      expect([
        screen.getByTestId('tokens-callout').closest('[dir="rtl"]') !== null,
        screen.getByTestId('tokens-callout-cta').getAttribute('href'),
      ]).toEqual([true, '/settings/billing']);
    });
  });
});
