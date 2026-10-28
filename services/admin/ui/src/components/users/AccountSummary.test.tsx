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
    free: { perWindow: 1, usedInWindow: 0, available: true, nextFreeAt: null, maxVideoMinutes: 30 },
    planMinutes: {
      cycleStart: '2026-10-01T00:00:00.000Z',
      cycleEnd: '2026-11-01T00:00:00.000Z',
      granted: 600,
      used: 140,
      remaining: 460,
    },
    credits: { available: 90 },
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
});
