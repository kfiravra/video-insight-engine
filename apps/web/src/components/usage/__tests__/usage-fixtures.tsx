import { render } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { ReactElement } from 'react';
import type { UsageSummary } from '@vie/types';
import { DirectionProvider } from '@/contexts/DirectionContext';

const RTL_LANGUAGES = new Set(['he', 'ar']);

/** A free account with its free video open, nothing else. */
export function usageSummary(overrides: Partial<UsageSummary> = {}): UsageSummary {
  return {
    plan: { id: 'free', periodEnd: null },
    free: { available: true, nextFreeAt: null, maxVideoMinutes: 30, perWindow: 1 },
    planMinutes: { granted: 0, used: 0, remaining: 0, cycleEnd: null },
    credits: { available: 0, lots: [] },
    holds: [],
    maxVideoMinutes: 30,
    blocked: false,
    ...overrides,
  };
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

interface FreeVideoOptions {
  available?: boolean;
  nextFreeAt?: string | null;
  maxVideoMinutes?: number;
  /** Effective free videos per window; 1 by default, 0 when caps switch the free video off. */
  perWindow?: number;
}

/** A `free` block of the summary. */
export function freeVideo(options: FreeVideoOptions = {}): UsageSummary['free'] {
  const { available = true, nextFreeAt = null, maxVideoMinutes = 30, perWindow = 1 } = options;
  return { available, nextFreeAt, maxVideoMinutes, perWindow };
}

/** A `free` block as a server that predates `perWindow` sends it. */
export function freeVideoWithoutCount(options: Omit<FreeVideoOptions, 'perWindow'> = {}): UsageSummary['free'] {
  const free: Partial<UsageSummary['free']> = freeVideo(options);
  delete free.perWindow;
  return free as UsageSummary['free'];
}

/** ISO time `ms` from now. */
export function inMs(ms: number): string {
  return new Date(Date.now() + ms).toISOString();
}

export const MINUTE = 60_000;
export const HOUR = 60 * MINUTE;
