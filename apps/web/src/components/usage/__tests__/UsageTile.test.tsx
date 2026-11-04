import { describe, it, expect, vi, afterEach } from 'vitest';
import { screen, within } from '@testing-library/react';
import type { UsageSummary } from '@vie/types';
import { UsageTile } from '../UsageTile';
import * as usageHook from '@/hooks/use-usage';
import { debtSummary, proSummary, renderIn, usageSummary } from './usage-fixtures';

let usageSpy: ReturnType<typeof vi.spyOn> | undefined;

function mockUsage(data: UsageSummary | undefined, state: { isLoading?: boolean; isError?: boolean } = {}) {
  usageSpy = vi.spyOn(usageHook, 'useUserUsage').mockReturnValue({
    data,
    isLoading: state.isLoading ?? false,
    isError: state.isError ?? false,
  } as unknown as ReturnType<typeof usageHook.useUserUsage>);
}

describe('UsageTile', () => {
  afterEach(() => {
    usageSpy?.mockRestore();
  });

  describe('loading and failure', () => {
    it('should render a loading state while the query is pending', () => {
      mockUsage(undefined, { isLoading: true });

      renderIn(<UsageTile />);

      expect(screen.getByRole('status')).toHaveTextContent(/loading your tokens/i);
    });

    it('should explain that tokens could not be loaded when the query fails and there is nothing to show', () => {
      mockUsage(undefined, { isError: true });

      renderIn(<UsageTile />);

      expect(screen.getByRole('alert')).toHaveTextContent(/could not load your tokens/i);
    });

    it('should keep showing the last summary when a refresh fails', () => {
      mockUsage(proSummary(), { isError: true });

      renderIn(<UsageTile />);

      expect(screen.getByTestId('usage-plan-tokens')).toHaveTextContent('12,400 of 20,000 left');
      expect(screen.queryByRole('alert')).toBeNull();
    });
  });

  describe('a new free account', () => {
    it('should title the tile "Your tokens" and show the signup balance', () => {
      mockUsage(usageSummary());

      renderIn(<UsageTile />);

      expect(screen.getByTestId('usage-tile')).toHaveTextContent('Your tokens');
      expect(screen.getByTestId('usage-balance')).toHaveTextContent('1,000 tokens');
    });

    it('should label the plan Free in a pill', () => {
      mockUsage(usageSummary());

      renderIn(<UsageTile />);

      expect(within(screen.getByTestId('usage-tile')).getByTestId('plan-pill')).toHaveTextContent('Free');
    });

    it('should show the credits and no Pro row', () => {
      mockUsage(usageSummary());

      renderIn(<UsageTile />);

      expect(screen.getByTestId('usage-credits')).toHaveTextContent('1,000 tokens');
      expect(screen.queryByTestId('usage-plan-tokens')).toBeNull();
    });

    it('should not show a debt notice or runs in progress', () => {
      mockUsage(usageSummary());

      renderIn(<UsageTile />);

      expect([screen.queryByTestId('usage-debt'), screen.queryByTestId('usage-holds')]).toEqual([null, null]);
    });

    it('should use the singular for one token', () => {
      mockUsage(usageSummary({ balance: 1, credits: { available: 1, lots: [] } }));

      renderIn(<UsageTile />);

      expect(screen.getByTestId('usage-balance')).toHaveTextContent(/^1 token$/);
    });
  });

  describe('Pro account', () => {
    it('should show the Pro cycle left of 20,000 and when it resets', () => {
      mockUsage(proSummary());

      renderIn(<UsageTile />);

      expect(screen.getByTestId('usage-plan-tokens')).toHaveTextContent('12,400 of 20,000 left · resets in 12d');
    });

    it('should drop the countdown when the cycle end is unknown', () => {
      mockUsage(proSummary({ cycleEnd: null }));

      renderIn(<UsageTile />);

      expect(screen.getByTestId('usage-plan-tokens')).not.toHaveTextContent('resets');
    });

    it('should draw the remaining share as a progress bar', () => {
      mockUsage(proSummary());

      renderIn(<UsageTile />);

      expect(screen.getByRole('progressbar', { name: 'Pro tokens' })).toHaveAttribute('aria-valuenow', '62');
    });

    it('should count the Pro cycle and the credits in the balance', () => {
      mockUsage(proSummary());

      renderIn(<UsageTile />);

      expect(screen.getByTestId('usage-balance')).toHaveTextContent('17,400 tokens');
    });

    it('should show credits with the soonest expiry', () => {
      mockUsage(proSummary());

      renderIn(<UsageTile />);

      const credits = screen.getByTestId('usage-credits');
      expect(credits).toHaveTextContent('5,000 tokens');
      expect(credits).toHaveTextContent('Expires Oct 1, 2027');
    });

    it('should label the plan Pro', () => {
      mockUsage(proSummary());

      renderIn(<UsageTile />);

      expect(screen.getByTestId('plan-pill')).toHaveTextContent('Pro');
    });
  });

  describe('debt', () => {
    it('should show the negative balance', () => {
      mockUsage(debtSummary(120));

      renderIn(<UsageTile />);

      expect(screen.getByTestId('usage-balance')).toHaveTextContent('-120 tokens');
    });

    it('should say how many tokens short the account is and that adding tokens continues', () => {
      mockUsage(debtSummary(120));

      renderIn(<UsageTile />);

      expect(screen.getByTestId('usage-debt')).toHaveTextContent("You're 120 tokens short — add tokens to continue.");
    });
  });

  describe('runs in progress and blocked accounts', () => {
    it('should total the estimates held by runs in flight', () => {
      mockUsage(
        usageSummary({
          holds: [
            { youtubeId: 'a', tokens: 840 },
            { youtubeId: 'b', tokens: 1_020 },
          ],
        }),
      );

      renderIn(<UsageTile />);

      expect(screen.getByTestId('usage-holds')).toHaveTextContent('≈ 1,860 tokens');
    });

    it('should warn when the account cannot start videos', () => {
      mockUsage(usageSummary({ blocked: true }));

      renderIn(<UsageTile />);

      expect(screen.getByRole('alert')).toHaveTextContent(/can't start new videos/i);
    });
  });

  describe('money and the menu', () => {
    it.each([
      ['free', usageSummary()],
      ['pro', proSummary()],
      ['in debt', debtSummary()],
    ])('should print no dollar sign for a %s account', (_name, summary) => {
      mockUsage(summary);

      renderIn(<UsageTile />);

      expect(screen.getByTestId('usage-tile').textContent).not.toContain('$');
    });

    it('should leave the link to the billing page to the menu item, not a link inside the tile', () => {
      mockUsage(usageSummary());

      renderIn(<UsageTile />);

      expect(screen.queryByRole('link')).toBeNull();
    });
  });

  describe('right-to-left languages', () => {
    it('should render inside a right-to-left container', () => {
      mockUsage(usageSummary());

      renderIn(<UsageTile />, 'ar');

      expect(screen.getByTestId('usage-tile').closest('[dir="rtl"]')).not.toBeNull();
    });

    it.each(['he', 'ar'])('should keep Latin digits with grouping in %s', (language) => {
      mockUsage(proSummary());

      renderIn(<UsageTile />, language);

      expect(screen.getByTestId('usage-plan-tokens')).toHaveTextContent(/12,400.*20,000/);
    });

    it('should word the Pro reset countdown with the Arabic unit', () => {
      mockUsage(proSummary());

      renderIn(<UsageTile />, 'ar');

      expect(screen.getByTestId('usage-plan-tokens')).toHaveTextContent('12 ي');
    });

    it('should print no dollar sign in Hebrew or Arabic', () => {
      mockUsage(proSummary());
      const hebrew = renderIn(<UsageTile />, 'he');
      const hebrewText = screen.getByTestId('usage-tile').textContent;
      hebrew.unmount();

      renderIn(<UsageTile />, 'ar');

      expect([hebrewText, screen.getByTestId('usage-tile').textContent].join('')).not.toContain('$');
    });
  });
});
