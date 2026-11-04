import { describe, it, expect, vi, afterEach } from 'vitest';
import { screen } from '@testing-library/react';
import type { UsageSummary } from '@vie/types';
import { DebtIndicator } from '../DebtIndicator';
import * as usageHook from '@/hooks/use-usage';
import { debtSummary, renderIn, usageSummary } from './usage-fixtures';

let usageSpy: ReturnType<typeof vi.spyOn> | undefined;

function mockUsage(data: UsageSummary | undefined) {
  usageSpy = vi.spyOn(usageHook, 'useUserUsage').mockReturnValue({
    data,
    isLoading: data === undefined,
    isError: false,
  } as unknown as ReturnType<typeof usageHook.useUserUsage>);
}

describe('DebtIndicator', () => {
  afterEach(() => {
    usageSpy?.mockRestore();
  });

  it('should say how far short the account is and link to the billing page', () => {
    mockUsage(debtSummary(120));

    renderIn(<DebtIndicator />);

    const link = screen.getByRole('link', { name: "You're 120 tokens short — add tokens to continue." });
    expect(link).toHaveAttribute('href', '/settings/billing');
    expect(link).toHaveTextContent('120 tokens short');
  });

  it('should keep the signed balance for narrow screens', () => {
    mockUsage(debtSummary(1_250));

    renderIn(<DebtIndicator />);

    expect(screen.getByTestId('header-debt')).toHaveTextContent('-1,250');
  });

  it.each([
    ['an account with tokens left', usageSummary()],
    ['an account at exactly zero', usageSummary({ balance: 0, credits: { available: 0, lots: [] } })],
    ['usage that has not loaded', undefined],
  ])('should render nothing for %s', (_name, data) => {
    mockUsage(data);

    renderIn(<DebtIndicator />);

    expect(screen.queryByTestId('header-debt')).toBeNull();
  });

  it('should word the chip in the page language', () => {
    mockUsage(debtSummary(120));

    renderIn(<DebtIndicator />, 'he');

    expect(screen.getByTestId('header-debt')).toHaveTextContent('חסרים 120 טוקנים');
  });
});
