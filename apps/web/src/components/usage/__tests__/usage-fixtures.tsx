import { render } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { ReactElement } from 'react';
import type { UsageSummary } from '@vie/types';
import { DirectionProvider } from '@/contexts/DirectionContext';

const RTL_LANGUAGES = new Set(['he', 'ar']);

/** A new free account: the 1,000-token signup grant as one never-expiring lot, nothing else. */
export function usageSummary(overrides: Partial<UsageSummary> = {}): UsageSummary {
  return {
    plan: { id: 'free', periodEnd: null },
    balance: 1000,
    planTokens: null,
    credits: { available: 1000, lots: [{ tokens: 1000, expiresAt: null }] },
    debt: 0,
    holds: [],
    maxVideoMinutes: 180,
    blocked: false,
    signupGrantTokens: 1000,
    ...overrides,
  };
}

interface ProOptions {
  remaining?: number;
  cycleEnd?: string | null;
  periodEnd?: string | null;
}

/** A Pro account mid-cycle (20,000 a month), with one 5,000-token pack lot on top. */
export function proSummary(options: ProOptions = {}, overrides: Partial<UsageSummary> = {}): UsageSummary {
  const { remaining = 12_400, cycleEnd = inMs(12 * DAY + 30 * MINUTE), periodEnd = inMs(200 * DAY) } = options;
  const granted = 20_000;
  return usageSummary({
    plan: { id: 'pro', periodEnd },
    balance: remaining + 5_000,
    planTokens: { granted, used: granted - remaining, remaining, cycleEnd },
    credits: { available: 5_000, lots: [{ tokens: 5_000, expiresAt: '2027-10-01T00:00:00.000Z' }] },
    ...overrides,
  });
}

/** An account a run overdrew: the balance is `-debt` and every new execution waits for a top-up. */
export function debtSummary(debt = 120, overrides: Partial<UsageSummary> = {}): UsageSummary {
  return usageSummary({ balance: -debt, credits: { available: 0, lots: [] }, debt, ...overrides });
}

/** Render inside the router, a query client and the direction context of `language`. */
export function renderIn(ui: ReactElement, language = 'en') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <DirectionProvider language={language} isRTL={RTL_LANGUAGES.has(language)}>
          {ui}
        </DirectionProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** ISO time `ms` from now. */
export function inMs(ms: number): string {
  return new Date(Date.now() + ms).toISOString();
}

export const MINUTE = 60_000;
export const HOUR = 60 * MINUTE;
export const DAY = 24 * HOUR;
