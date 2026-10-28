import { describe, it, expect, vi, afterEach } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactNode } from 'react';
import type { UsageSummary } from '@vie/types';
import { BillingPage } from '../BillingPage';
import * as usageHook from '@/hooks/use-usage';
import { HOUR, MINUTE, inMs, renderIn, usageSummary } from '@/components/usage/__tests__/usage-fixtures';

vi.mock('@/components/layout/Layout', () => ({
  Layout: ({ children }: { children: ReactNode }) => <div data-testid="layout">{children}</div>,
}));

interface QueryState {
  isLoading?: boolean;
  isError?: boolean;
  refetch?: () => void;
}

let usageSpy: ReturnType<typeof vi.spyOn> | undefined;

function mockUsage(data: UsageSummary | undefined, state: QueryState = {}) {
  usageSpy = vi.spyOn(usageHook, 'useUserUsage').mockReturnValue({
    data,
    isLoading: state.isLoading ?? false,
    isError: state.isError ?? false,
    refetch: state.refetch ?? vi.fn(),
  } as unknown as ReturnType<typeof usageHook.useUserUsage>);
}

const PRO = usageSummary({
  plan: { id: 'pro', periodEnd: '2027-10-01T12:00:00.000Z' },
  planMinutes: { granted: 600, used: 188, remaining: 412, cycleEnd: '2026-11-01T12:00:00.000Z' },
  credits: {
    available: 175,
    lots: [
      { minutes: 150, expiresAt: '2027-03-01T12:00:00.000Z' },
      { minutes: 25, expiresAt: null },
    ],
  },
  holds: [{ youtubeId: 'dQw4w9WgXcQ', minutes: 31 }],
});

describe('BillingPage', () => {
  afterEach(() => {
    usageSpy?.mockRestore();
  });

  describe('states', () => {
    it('should show a loading state while the minutes load', () => {
      mockUsage(undefined, { isLoading: true });

      renderIn(<BillingPage />);

      expect(screen.getByRole('status')).toHaveTextContent(/loading your minutes/i);
    });

    it('should offer a retry when the minutes cannot be loaded', async () => {
      const refetch = vi.fn();
      mockUsage(undefined, { isError: true, refetch });
      renderIn(<BillingPage />);

      await userEvent.setup().click(screen.getByRole('button', { name: 'Try Again' }));

      expect(refetch).toHaveBeenCalledOnce();
    });
  });

  describe('free account', () => {
    it('should title the page and name the free plan', () => {
      mockUsage(usageSummary());

      renderIn(<BillingPage />);

      expect(screen.getByRole('heading', { level: 1, name: 'Minutes & plan' })).toBeInTheDocument();
      expect(screen.getByTestId('billing-plan')).toHaveTextContent('Free');
    });

    it('should describe the free video allowance', () => {
      mockUsage(usageSummary());

      renderIn(<BillingPage />);

      expect(screen.getByTestId('billing-plan')).toHaveTextContent('One free video every 24 hours.');
    });

    it('should show the free video and zero credits', () => {
      mockUsage(usageSummary());

      renderIn(<BillingPage />);

      const minutes = screen.getByTestId('billing-minutes');
      expect(minutes).toHaveTextContent('ready');
      expect(minutes).toHaveTextContent('0 minutes');
    });

    it('should show when the next free video opens', () => {
      const nextFreeAt = inMs(7 * HOUR + 30 * MINUTE + 30_000);
      mockUsage(usageSummary({ free: { available: false, nextFreeAt, maxVideoMinutes: 30 } }));

      renderIn(<BillingPage />);

      expect(screen.getByTestId('billing-minutes')).toHaveTextContent('next in 7h 30m');
    });

    it('should say there are no credits and nothing running', () => {
      mockUsage(usageSummary());

      renderIn(<BillingPage />);

      expect(screen.getByTestId('billing-lots')).toHaveTextContent('No credits yet.');
      expect(screen.getByTestId('billing-holds')).toHaveTextContent('Nothing running right now.');
    });
  });

  describe('Pro account', () => {
    it('should show the plan term and the cycle reset date', () => {
      mockUsage(PRO);

      renderIn(<BillingPage />);

      const plan = screen.getByTestId('billing-plan');
      expect(plan).toHaveTextContent('Pro');
      expect(plan).toHaveTextContent('Current term ends Oct 1, 2027');
      expect(plan).toHaveTextContent('Monthly minutes reset on Nov 1, 2026');
    });

    it('should show Pro minutes left this cycle', () => {
      mockUsage(PRO);

      renderIn(<BillingPage />);

      expect(screen.getByTestId('billing-minutes')).toHaveTextContent('412 of 600 minutes left');
    });

    it('should list each credit lot with its expiry', () => {
      mockUsage(PRO);

      renderIn(<BillingPage />);

      const rows = within(screen.getByTestId('billing-lots')).getAllByRole('listitem');
      expect(rows.map((row) => row.textContent)).toEqual([
        '150 minutesExpires Mar 1, 2027',
        '25 minutesNever expires',
      ]);
    });

    it('should list the runs in progress with the minutes they hold', () => {
      mockUsage(PRO);

      renderIn(<BillingPage />);

      const [row] = within(screen.getByTestId('billing-holds')).getAllByRole('listitem');
      expect(row).toHaveTextContent('dQw4w9WgXcQ');
      expect(row).toHaveTextContent('31 minutes');
    });
  });

  describe('what the page does not do', () => {
    it('should tell the user that the team adds minutes for now', () => {
      mockUsage(usageSummary());

      renderIn(<BillingPage />);

      expect(screen.getByTestId('billing-team-note')).toHaveTextContent('Minutes are added by the team for now.');
    });

    it('should offer no checkout, purchase or upgrade control', () => {
      mockUsage(PRO);

      renderIn(<BillingPage />);

      expect(screen.queryByRole('button', { name: /buy|checkout|subscribe|upgrade|pay/i })).toBeNull();
    });

    it.each([
      ['free', usageSummary()],
      ['pro', PRO],
    ])('should print no dollar sign for a %s account', (_name, summary) => {
      mockUsage(summary);

      renderIn(<BillingPage />);

      expect(screen.getByTestId('layout').textContent).not.toContain('$');
    });
  });

  it('should warn when the account is blocked', () => {
    mockUsage(usageSummary({ blocked: true }));

    renderIn(<BillingPage />);

    expect(screen.getByTestId('billing-plan')).toHaveTextContent("can't start new videos");
  });

  it('should link back to the library', () => {
    mockUsage(usageSummary());

    renderIn(<BillingPage />);

    expect(screen.getByRole('link', { name: 'Back to library' })).toHaveAttribute('href', '/board');
  });

  describe('right-to-left languages', () => {
    it('should title the page and give the team note in Hebrew', () => {
      mockUsage(usageSummary());

      renderIn(<BillingPage />, 'he');

      expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('דקות ומסלול');
      expect(screen.getByTestId('billing-team-note')).toHaveTextContent('בינתיים הצוות מוסיף דקות.');
    });

    it('should use the Hebrew plural forms for the lots', () => {
      mockUsage(
        usageSummary({
          credits: {
            available: 3,
            lots: [
              { minutes: 1, expiresAt: null },
              { minutes: 2, expiresAt: null },
            ],
          },
        }),
      );

      renderIn(<BillingPage />, 'he');

      const rows = within(screen.getByTestId('billing-lots')).getAllByRole('listitem');
      expect(rows.map((row) => row.textContent)).toEqual(['דקה אחתללא תפוגה', 'שתי דקותללא תפוגה']);
    });

    it('should use the Arabic dual, few and many forms for the lots', () => {
      mockUsage(
        usageSummary({
          credits: {
            available: 16,
            lots: [
              { minutes: 2, expiresAt: null },
              { minutes: 3, expiresAt: null },
              { minutes: 11, expiresAt: null },
            ],
          },
        }),
      );

      renderIn(<BillingPage />, 'ar');

      const rows = within(screen.getByTestId('billing-lots')).getAllByRole('listitem');
      expect(rows.map((row) => row.textContent?.split('بلا')[0])).toEqual(['دقيقتان', '3 دقائق', '11 دقيقة']);
    });

    it('should write the Pro balance in Arabic', () => {
      mockUsage(PRO);

      renderIn(<BillingPage />, 'ar');

      expect(screen.getByTestId('billing-minutes')).toHaveTextContent('تبقّى 412 من 600 دقيقة');
    });

    it.each(['he', 'ar'])('should render %s inside a right-to-left container', (language) => {
      mockUsage(PRO);

      renderIn(<BillingPage />, language);

      expect(screen.getByTestId('billing-plan').closest('[dir="rtl"]')).not.toBeNull();
    });

    it.each(['he', 'ar'])('should print no dollar sign in %s', (language) => {
      mockUsage(PRO);

      renderIn(<BillingPage />, language);

      expect(screen.getByTestId('layout').textContent).not.toContain('$');
    });
  });
});
