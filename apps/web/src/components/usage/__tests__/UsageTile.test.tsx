import { describe, it, expect, vi, afterEach } from 'vitest';
import { screen, within } from '@testing-library/react';
import type { UsageSummary } from '@vie/types';
import { UsageTile } from '../UsageTile';
import * as usageHook from '@/hooks/use-usage';
import { HOUR, MINUTE, freeVideo, freeVideoWithoutCount, inMs, renderIn, usageSummary } from './usage-fixtures';

let usageSpy: ReturnType<typeof vi.spyOn> | undefined;

function mockUsage(data: UsageSummary | undefined, state: { isLoading?: boolean; isError?: boolean } = {}) {
  usageSpy = vi.spyOn(usageHook, 'useUserUsage').mockReturnValue({
    data,
    isLoading: state.isLoading ?? false,
    isError: state.isError ?? false,
  } as unknown as ReturnType<typeof usageHook.useUserUsage>);
}

const PRO = usageSummary({
  plan: { id: 'pro', periodEnd: inMs(200 * 24 * HOUR) },
  planMinutes: { granted: 600, used: 188, remaining: 412, cycleEnd: inMs(12 * 24 * HOUR + 30 * MINUTE) },
  credits: { available: 150, lots: [{ minutes: 150, expiresAt: '2027-10-01T00:00:00.000Z' }] },
});

describe('UsageTile', () => {
  afterEach(() => {
    usageSpy?.mockRestore();
  });

  describe('loading and failure', () => {
    it('should render a loading state while the query is pending', () => {
      mockUsage(undefined, { isLoading: true });

      renderIn(<UsageTile />);

      expect(screen.getByRole('status')).toHaveTextContent(/loading your minutes/i);
    });

    it('should explain that minutes could not be loaded when the query fails and there is nothing to show', () => {
      mockUsage(undefined, { isError: true });

      renderIn(<UsageTile />);

      expect(screen.getByRole('alert')).toHaveTextContent(/could not load your minutes/i);
    });
  });

  describe('free account', () => {
    it('should show the free video as ready with its length limit', () => {
      mockUsage(usageSummary());

      renderIn(<UsageTile />);

      const row = screen.getByTestId('usage-free-video');
      expect(row).toHaveTextContent('Free video');
      expect(row).toHaveTextContent('ready');
      expect(row).toHaveTextContent('Up to 30 minutes per video');
    });

    it('should show when the next free video opens once it is used', () => {
      const nextFreeAt = inMs(7 * HOUR + 30 * MINUTE + 30_000);
      mockUsage(usageSummary({ free: freeVideo({ available: false, nextFreeAt }) }));

      renderIn(<UsageTile />);

      expect(screen.getByTestId('usage-free-video')).toHaveTextContent('next in 7h 30m');
    });

    it('should show the free video as not available when no slot ever opens', () => {
      mockUsage(usageSummary({ free: freeVideo({ available: false }) }));

      renderIn(<UsageTile />);

      expect(screen.getByTestId('usage-free-video')).toHaveTextContent('not available');
    });

    it('should show zero credits and no Pro row', () => {
      mockUsage(usageSummary());

      renderIn(<UsageTile />);

      expect(screen.getByTestId('usage-credits')).toHaveTextContent('0 minutes');
      expect(screen.queryByTestId('usage-plan-minutes')).toBeNull();
    });

    it('should label the plan Free', () => {
      mockUsage(usageSummary());

      renderIn(<UsageTile />);

      expect(within(screen.getByTestId('usage-tile')).getByText('Free')).toBeInTheDocument();
    });
  });

  describe('Pro account', () => {
    it('should show the Pro minutes left in this cycle', () => {
      mockUsage(PRO);

      renderIn(<UsageTile />);

      expect(screen.getByTestId('usage-plan-minutes')).toHaveTextContent('412 of 600 minutes left');
    });

    it('should show how long until the cycle resets', () => {
      mockUsage(PRO);

      renderIn(<UsageTile />);

      expect(screen.getByTestId('usage-plan-minutes')).toHaveTextContent('Resets in 12d');
    });

    it('should draw the remaining share as a progress bar', () => {
      mockUsage(PRO);

      renderIn(<UsageTile />);

      expect(screen.getByRole('progressbar', { name: 'Pro minutes' })).toHaveAttribute('aria-valuenow', '69');
    });

    it('should show credits and the soonest expiry', () => {
      mockUsage(PRO);

      renderIn(<UsageTile />);

      const credits = screen.getByTestId('usage-credits');
      expect(credits).toHaveTextContent('150 minutes');
      expect(credits).toHaveTextContent('Expires Oct 1, 2027');
    });
  });

  describe('runs in progress and blocked accounts', () => {
    it('should total the minutes held by runs in flight', () => {
      mockUsage(
        usageSummary({
          holds: [
            { youtubeId: 'a', minutes: 31 },
            { youtubeId: 'b', minutes: 4 },
          ],
        }),
      );

      renderIn(<UsageTile />);

      expect(screen.getByTestId('usage-holds')).toHaveTextContent('35 minutes');
    });

    it('should warn when the account cannot start videos', () => {
      mockUsage(usageSummary({ blocked: true }));

      renderIn(<UsageTile />);

      expect(screen.getByRole('alert')).toHaveTextContent(/can't start new videos/i);
    });
  });

  describe('free video switched off', () => {
    it('should say not available and name no length when caps turn the free video off', () => {
      mockUsage(usageSummary({ free: freeVideo({ available: false, perWindow: 0 }) }));

      renderIn(<UsageTile />);

      const row = screen.getByTestId('usage-free-video');
      expect(row).toHaveTextContent('not available');
      expect(row).not.toHaveTextContent('per video');
    });

    it('should still name the length for a free video that is used up, not switched off', () => {
      const nextFreeAt = inMs(2 * HOUR + 30_000);
      mockUsage(usageSummary({ free: freeVideo({ available: false, nextFreeAt, perWindow: 1 }) }));

      renderIn(<UsageTile />);

      expect(screen.getByTestId('usage-free-video')).toHaveTextContent('Up to 30 minutes per video');
    });

    it('should treat a server that does not send perWindow as the standard free video', () => {
      mockUsage(usageSummary({ free: freeVideoWithoutCount() }));

      renderIn(<UsageTile />);

      expect(screen.getByTestId('usage-free-video')).toHaveTextContent('Up to 30 minutes per video');
    });

    it('should show the free video ready when several are allowed and one is open', () => {
      mockUsage(usageSummary({ free: freeVideo({ perWindow: 3 }) }));

      renderIn(<UsageTile />);

      expect(screen.getByTestId('usage-free-video')).toHaveTextContent('ready');
    });
  });

  describe('a countdown that has run out before the server agrees', () => {
    it('should say opening shortly, not ready, when the server still says unavailable', () => {
      mockUsage(usageSummary({ free: freeVideo({ available: false, nextFreeAt: inMs(-MINUTE), perWindow: 1 }) }));

      renderIn(<UsageTile />);

      const row = screen.getByTestId('usage-free-video');
      expect(row).toHaveTextContent('opening shortly');
      expect(row).not.toHaveTextContent('ready');
    });
  });

  describe('a failed refresh', () => {
    it('should keep showing the last summary when a refresh fails', () => {
      mockUsage(PRO, { isError: true });

      renderIn(<UsageTile />);

      expect(screen.getByTestId('usage-plan-minutes')).toHaveTextContent('412 of 600 minutes left');
      expect(screen.queryByRole('alert')).toBeNull();
    });
  });

  describe('money and the menu', () => {
    it.each([
      ['free', usageSummary()],
      ['pro', PRO],
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
    it('should use the Hebrew plural forms for credits', () => {
      mockUsage(usageSummary({ credits: { available: 2, lots: [] } }));

      renderIn(<UsageTile />, 'he');

      expect(screen.getByTestId('usage-credits')).toHaveTextContent('שתי דקות');
    });

    it('should use the singular Hebrew form for one minute', () => {
      mockUsage(usageSummary({ credits: { available: 1, lots: [] } }));

      renderIn(<UsageTile />, 'he');

      expect(screen.getByTestId('usage-credits')).toHaveTextContent('דקה אחת');
    });

    it('should write the Pro balance in Hebrew with Latin digits', () => {
      mockUsage(PRO);

      renderIn(<UsageTile />, 'he');

      expect(screen.getByTestId('usage-plan-minutes')).toHaveTextContent('412 מתוך 600 דקות נותרו');
    });

    it('should use the Arabic dual and few forms for credits', () => {
      mockUsage(usageSummary({ credits: { available: 2, lots: [] } }));
      const dual = renderIn(<UsageTile />, 'ar');
      const dualText = screen.getByTestId('usage-credits').textContent;
      dual.unmount();

      mockUsage(usageSummary({ credits: { available: 3, lots: [] } }));
      renderIn(<UsageTile />, 'ar');

      expect([dualText, screen.getByTestId('usage-credits').textContent]).toEqual([
        expect.stringContaining('دقيقتان'),
        expect.stringContaining('3 دقائق'),
      ]);
    });

    it('should word the countdown to the next free video in Arabic', () => {
      mockUsage(
        usageSummary({
          free: freeVideo({ available: false, nextFreeAt: inMs(2 * HOUR + 30_000) }),
        }),
      );

      renderIn(<UsageTile />, 'ar');

      expect(screen.getByTestId('usage-free-video')).toHaveTextContent('بعد 2 س');
    });

    it('should put the Arabic dual in the oblique case after "up to" in the length hint', () => {
      mockUsage(usageSummary({ free: freeVideo({ maxVideoMinutes: 2, perWindow: 1 }) }));

      renderIn(<UsageTile />, 'ar');

      expect(screen.getByTestId('usage-free-video')).toHaveTextContent('حتى دقيقتين لكل فيديو');
    });

    it('should render inside a right-to-left container', () => {
      mockUsage(usageSummary());

      renderIn(<UsageTile />, 'ar');

      expect(screen.getByTestId('usage-tile').closest('[dir="rtl"]')).not.toBeNull();
    });

    it('should print no dollar sign in Hebrew or Arabic', () => {
      mockUsage(PRO);
      const hebrew = renderIn(<UsageTile />, 'he');
      const hebrewText = screen.getByTestId('usage-tile').textContent;
      hebrew.unmount();

      renderIn(<UsageTile />, 'ar');

      expect([hebrewText, screen.getByTestId('usage-tile').textContent].join('')).not.toContain('$');
    });
  });
});
