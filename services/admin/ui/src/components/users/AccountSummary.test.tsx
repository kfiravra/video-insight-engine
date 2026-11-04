import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import type { BillingAccountSnapshot } from '../../lib/billing';
import { AccountSummary } from './AccountSummary';

function account(plan: BillingAccountSnapshot['plan']): BillingAccountSnapshot {
  return {
    accountId: 'b'.repeat(24),
    ownerType: 'user',
    ownerId: 'a'.repeat(24),
    plan,
    planTokens: {
      cycleStart: '2026-10-01T00:00:00.000Z',
      cycleEnd: '2026-11-01T00:00:00.000Z',
      granted: 20000,
      used: 5400,
      remaining: 14600,
    },
    credits: { available: 900 },
    debt: 0,
    balance: 15500,
    signupGrantAt: '2026-09-01T00:00:00.000Z',
    caps: {},
    openHolds: 0,
    version: 7,
    updatedAt: '2026-10-27T08:30:15.123Z',
  };
}

describe('AccountSummary', () => {
  it('should show a plan term in UTC, the zone its end was entered in', () => {
    render(
      <AccountSummary
        account={account({
          id: 'pro',
          status: 'active',
          source: 'manual',
          periodStart: '2026-10-01T00:00:00.000Z',
          periodEnd: '2027-10-01T23:59:59.000Z',
        })}
      />,
    );

    expect(screen.getByTestId('account-plan').textContent).toMatch(
      /Oct 1, 2026, 12:00\sAM UTC to Oct 1, 2027, 11:59\sPM UTC/,
    );
  });

  it('should say there is no term on the free plan', () => {
    render(
      <AccountSummary account={account({ id: 'free', status: 'active', source: null, periodStart: null, periodEnd: null })} />,
    );

    expect(screen.getByTestId('account-plan').textContent).toContain('No plan term');
  });

  it('should show the balance and no free-video tile', () => {
    render(<AccountSummary account={account({ id: 'free', status: 'active', source: null, periodStart: null, periodEnd: null })} />);

    expect(screen.getByTestId('account-balance').textContent).toContain('15,500 tokens');
    expect(screen.queryByTestId('account-free-video')).toBeNull();
  });

  it('should spell out debt in the balance tile', () => {
    render(
      <AccountSummary
        account={{
          ...account({ id: 'free', status: 'active', source: null, periodStart: null, periodEnd: null }),
          balance: -120,
          debt: 120,
        }}
      />,
    );

    expect(screen.getByTestId('account-balance').textContent).toContain('−120 tokens (owes 120)');
  });
});
