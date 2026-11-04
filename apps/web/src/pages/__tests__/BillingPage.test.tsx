import { describe, it, expect, vi, afterEach } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactNode } from 'react';
import type { UsageSummary } from '@vie/types';
import { BillingPage } from '../BillingPage';
import * as usageHook from '@/hooks/use-usage';
import { getLabels } from '@/lib/i18n';
import { debtSummary, proSummary, renderIn, usageSummary } from '@/components/usage/__tests__/usage-fixtures';

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

const PRO = proSummary(
  { periodEnd: '2027-10-01T12:00:00.000Z', cycleEnd: '2026-12-01T12:00:00.000Z' },
  {
    credits: {
      available: 5_250,
      lots: [
        { tokens: 5_000, expiresAt: '2027-03-01T12:00:00.000Z' },
        { tokens: 250, expiresAt: null },
      ],
    },
    holds: [{ youtubeId: 'dQw4w9WgXcQ', tokens: 840 }],
  },
);

describe('BillingPage', () => {
  afterEach(() => {
    usageSpy?.mockRestore();
  });

  describe('states', () => {
    it('should show a loading state while the tokens load', () => {
      mockUsage(undefined, { isLoading: true });

      renderIn(<BillingPage />);

      expect(screen.getByRole('status')).toHaveTextContent(/loading your tokens/i);
    });

    it('should offer a retry when the tokens cannot be loaded', async () => {
      const refetch = vi.fn();
      mockUsage(undefined, { isError: true, refetch });
      renderIn(<BillingPage />);

      await userEvent.setup().click(screen.getByRole('button', { name: 'Try Again' }));

      expect(refetch).toHaveBeenCalledOnce();
    });

    it('should keep showing the last summary when a refresh fails', () => {
      mockUsage(PRO, { isError: true });

      renderIn(<BillingPage />);

      expect(screen.getByTestId('billing-balance')).toHaveTextContent('12,400 of 20,000 left');
      expect(screen.queryByRole('button', { name: 'Try Again' })).toBeNull();
    });
  });

  describe('a new free account', () => {
    it('should title the page "Tokens & plan" and show the balance with a Free pill', () => {
      mockUsage(usageSummary());

      renderIn(<BillingPage />);

      expect(screen.getByRole('heading', { level: 1, name: 'Tokens & plan' })).toBeInTheDocument();
      const balance = screen.getByTestId('billing-balance');
      expect(within(balance).getByTestId('usage-balance')).toHaveTextContent('1,000 tokens');
      expect(within(balance).getByTestId('plan-pill')).toHaveTextContent('Free');
    });

    it('should list the signup grant as a lot that never expires and say nothing is running', () => {
      mockUsage(usageSummary());

      renderIn(<BillingPage />);

      const rows = within(screen.getByTestId('billing-lots')).getAllByRole('listitem');
      expect(rows.map((row) => row.textContent)).toEqual(['1,000 tokensNever expires']);
      expect(screen.getByTestId('billing-holds')).toHaveTextContent('Nothing running right now.');
    });

    it('should say there are no credits when there are no lots', () => {
      mockUsage(usageSummary({ balance: 0, credits: { available: 0, lots: [] } }));

      renderIn(<BillingPage />);

      expect(screen.getByTestId('billing-lots')).toHaveTextContent('No credits yet.');
    });
  });

  describe('the Pro card', () => {
    it('should offer Pro at $20 a month for 20,000 tokens', () => {
      mockUsage(usageSummary());

      renderIn(<BillingPage />);

      const card = screen.getByTestId('plan-card');
      expect(card).toHaveTextContent('$20/month');
      expect(card).toHaveTextContent('20,000 tokens every month');
    });

    it('should open the payments notice inline when "Go Pro" is pressed', async () => {
      mockUsage(usageSummary());
      renderIn(<BillingPage />);
      const card = screen.getByTestId('plan-card');
      const button = within(card).getByRole('button', { name: 'Go Pro' });

      await userEvent.setup().click(button);

      const notice = within(card).getByTestId('payments-placeholder');
      expect(notice).toHaveTextContent("Payments aren't live yet — tokens are added by the team.");
      // Announced: the live region exists before the click fills it.
      expect(notice.parentElement).toHaveAttribute('aria-live', 'polite');
      expect(button).toHaveAttribute('aria-expanded', 'true');
    });

    it('should keep the notice closed until a button is pressed', () => {
      mockUsage(usageSummary());

      renderIn(<BillingPage />);

      expect(screen.queryByTestId('payments-placeholder')).toBeNull();
    });

    it('should use the call-to-action colour for "Go Pro"', () => {
      mockUsage(usageSummary());

      renderIn(<BillingPage />);

      expect(screen.getByRole('button', { name: 'Go Pro' }).className).toContain('bg-cta');
    });

    it('should mark Pro as the current plan, with its term and reset dates, and offer no "Go Pro"', () => {
      mockUsage(PRO);

      renderIn(<BillingPage />);

      const card = screen.getByTestId('plan-card');
      expect(card).toHaveTextContent('Your plan');
      expect(card).toHaveTextContent('Current term ends Oct 1, 2027');
      expect(card).toHaveTextContent('Monthly tokens reset on Dec 1, 2026');
      expect(within(card).queryByRole('button')).toBeNull();
    });
  });

  describe('the pack card', () => {
    it('should offer the $5 pack of 5,000 tokens that expires 12 months after purchase', () => {
      mockUsage(usageSummary());

      renderIn(<BillingPage />);

      const card = screen.getByTestId('pack-card-pack_5');
      expect(card).toHaveTextContent('$5');
      expect(card).toHaveTextContent('5,000 tokens');
      expect(card).toHaveTextContent('Expires 12 months after purchase');
    });

    it('should open the payments notice inline when "Buy tokens" is pressed, on Pro too', async () => {
      mockUsage(PRO);
      renderIn(<BillingPage />);
      const card = screen.getByTestId('pack-card-pack_5');

      await userEvent.setup().click(within(card).getByRole('button', { name: 'Buy tokens' }));

      expect(within(card).getByTestId('payments-placeholder')).toBeInTheDocument();
      expect(within(screen.getByTestId('plan-card')).queryByTestId('payments-placeholder')).toBeNull();
    });

    it('should use the call-to-action colour for "Buy tokens"', () => {
      mockUsage(usageSummary());

      renderIn(<BillingPage />);

      expect(screen.getByRole('button', { name: 'Buy tokens' }).className).toContain('bg-cta');
    });
  });

  describe('Pro account', () => {
    it('should show the Pro cycle left and the balance across cycle and credits', () => {
      mockUsage(PRO);

      renderIn(<BillingPage />);

      const balance = screen.getByTestId('billing-balance');
      expect(balance).toHaveTextContent('12,400 of 20,000 left');
      expect(within(balance).getByTestId('usage-balance')).toHaveTextContent('17,400 tokens');
      expect(within(balance).getByTestId('plan-pill')).toHaveTextContent('Pro');
    });

    it('should list each credit lot with its expiry', () => {
      mockUsage(PRO);

      renderIn(<BillingPage />);

      const rows = within(screen.getByTestId('billing-lots')).getAllByRole('listitem');
      expect(rows.map((row) => row.textContent)).toEqual([
        '5,000 tokensExpires Mar 1, 2027',
        '250 tokensNever expires',
      ]);
    });

    it('should list the runs in progress with the estimate they hold', () => {
      mockUsage(PRO);

      renderIn(<BillingPage />);

      const [row] = within(screen.getByTestId('billing-holds')).getAllByRole('listitem');
      expect(row).toHaveTextContent('dQw4w9WgXcQ');
      expect(row).toHaveTextContent('≈ 840 tokens');
    });
  });

  describe('debt', () => {
    it('should show the negative balance and the shortfall', () => {
      mockUsage(debtSummary(120));

      renderIn(<BillingPage />);

      const balance = screen.getByTestId('billing-balance');
      expect(within(balance).getByTestId('usage-balance')).toHaveTextContent('-120 tokens');
      expect(within(balance).getByTestId('usage-debt')).toHaveTextContent("You're 120 tokens short — add tokens to continue.");
    });
  });

  describe('team note and money', () => {
    it('should tell the user that the team adds tokens for now', () => {
      mockUsage(usageSummary());

      renderIn(<BillingPage />);

      expect(screen.getByTestId('billing-team-note')).toHaveTextContent('Tokens are added by the team for now.');
    });

    it('should print a dollar sign only on the Pro and pack cards', () => {
      mockUsage(PRO);
      renderIn(<BillingPage />);

      const outsideCards = [
        screen.getByTestId('billing-balance'),
        screen.getByTestId('billing-lots'),
        screen.getByTestId('billing-holds'),
        screen.getByTestId('billing-team-note'),
      ].map((section) => section.textContent);

      expect(outsideCards.join('')).not.toContain('$');
    });
  });

  it('should warn when the account is blocked', () => {
    mockUsage(usageSummary({ blocked: true }));

    renderIn(<BillingPage />);

    expect(screen.getByTestId('billing-balance')).toHaveTextContent("can't start new videos");
  });

  it('should link back to the library', () => {
    mockUsage(usageSummary());

    renderIn(<BillingPage />);

    expect(screen.getByRole('link', { name: 'Back to library' })).toHaveAttribute('href', '/board');
  });

  describe('right-to-left languages', () => {
    it.each(['he', 'ar'])('should title the page and give the team note from the %s table', (language) => {
      mockUsage(usageSummary());

      renderIn(<BillingPage />, language);

      const { pageTitle, teamNote } = getLabels(language).billing;
      expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(pageTitle);
      expect(screen.getByTestId('billing-team-note')).toHaveTextContent(teamNote);
    });

    it('should say "valid until" in Hebrew for a lot with an expiry, not that it expired', () => {
      mockUsage(usageSummary({ credits: { available: 5_000, lots: [{ tokens: 5_000, expiresAt: '2027-03-01T12:00:00.000Z' }] } }));

      renderIn(<BillingPage />, 'he');

      const [row] = within(screen.getByTestId('billing-lots')).getAllByRole('listitem');
      expect(row.textContent).toContain('בתוקף עד');
      expect(row.textContent).not.toContain('פג תוקף');
    });

    it.each(['he', 'ar'])('should keep Latin digits and the price as written in %s', (language) => {
      mockUsage(usageSummary());

      renderIn(<BillingPage />, language);

      expect(screen.getByTestId('plan-card')).toHaveTextContent('$20');
      expect(screen.getByTestId('plan-card').textContent).toMatch(/20,000/);
    });

    it.each(['he', 'ar'])('should render %s inside a right-to-left container', (language) => {
      mockUsage(PRO);

      renderIn(<BillingPage />, language);

      expect(screen.getByTestId('billing-balance').closest('[dir="rtl"]')).not.toBeNull();
    });
  });
});
