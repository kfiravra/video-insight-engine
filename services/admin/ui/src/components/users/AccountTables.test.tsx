import { describe, it, expect } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import type { BillingCreditLot } from '../../lib/billing';
import { LotsTable } from './AccountTables';

const LOT_ID = '1'.repeat(24);

function lot(overrides: Partial<BillingCreditLot> = {}): BillingCreditLot {
  return {
    lotId: LOT_ID,
    minutesGranted: 150,
    minutesRemaining: 90,
    source: { kind: 'payment', ref: 'txn_1' },
    priceUsdCents: 500,
    expiresAt: '2027-10-01T23:59:59.000Z',
    expired: false,
    createdAt: '2026-10-01T00:00:00.000Z',
    ...overrides,
  };
}

function renderRow(overrides: Partial<BillingCreditLot>): HTMLElement {
  render(<LotsTable lots={[lot(overrides)]} />);
  return screen.getByTestId(`lot-row-${LOT_ID}`);
}

describe('LotsTable', () => {
  describe('status', () => {
    it.each([
      ['Open', { minutesRemaining: 90, expired: false }],
      ['Used up', { minutesRemaining: 0, expired: false }],
      ['Expired', { minutesRemaining: 90, expired: true }],
      // The repair job zeroes the minutes of a lot when it expires: that lot
      // expired, it was not used up (every monthly recurring lot ends this way).
      ['Expired', { minutesRemaining: 0, expired: true }],
    ])('should mark a lot as %s', (status, overrides) => {
      expect(within(renderRow(overrides)).getByText(status)).toBeTruthy();
    });
  });

  describe('expiry', () => {
    it('should show a lot\'s expiry in UTC, the zone an admin picked it in', () => {
      expect(renderRow({ expiresAt: '2027-10-01T23:59:59.000Z' }).textContent).toMatch(/Oct 1, 2027, 11:59\sPM UTC/);
    });

    it('should show Never for a lot that does not expire', () => {
      expect(within(renderRow({ expiresAt: null })).getByText('Never')).toBeTruthy();
    });
  });

  it('should shorten a long lot id', () => {
    expect(renderRow({}).textContent).toContain('111111…1111');
  });
});
