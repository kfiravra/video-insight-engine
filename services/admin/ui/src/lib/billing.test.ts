import { describe, it, expect } from 'vitest';
import type { InfiniteData } from '@tanstack/react-query';
import {
  describeSource,
  formatCents,
  describeBalance,
  formatSignedAmount,
  formatSignedTokens,
  formatTokens,
  mergeLedgerPages,
} from './billing';
import type { BillingAccountDetail, BillingLedgerEntry } from './billing';

function entry(id: string): BillingLedgerEntry {
  return {
    id,
    userId: 'u',
    type: 'grant',
    bucket: 'credit',
    unit: 'tokens',
    amount: 1,
    source: { kind: 'admin', ref: 'r' },
    idempotencyKey: id,
    createdAt: '2026-10-01T00:00:00.000Z',
  };
}

function page(ledger: BillingLedgerEntry[], next: string | null): BillingAccountDetail {
  return { account: null, lots: [], holds: [], ledger, recurringGrants: [], ledgerNextBefore: next };
}

describe('formatSignedTokens', () => {
  it.each([
    [1500, '+1,500 tokens'],
    [-1200, '−1,200 tokens'],
    [-1, '−1 token'],
    [0, '0 tokens'],
  ])('should format %i as %s', (tokens, expected) => {
    expect(formatSignedTokens(tokens)).toBe(expected);
  });
});

describe('formatTokens', () => {
  it('should separate thousands', () => {
    expect(formatTokens(1250)).toBe('1,250 tokens');
  });
});

describe('formatSignedAmount', () => {
  it('should show a token row in tokens', () => {
    expect(formatSignedAmount(-1200, 'tokens')).toBe('−1,200 tokens');
  });

  it('should keep a pre-migration minute row readable as minutes', () => {
    expect(formatSignedAmount(-12, 'minutes')).toBe('−12 min');
    expect(formatSignedAmount(150, 'minutes')).toBe('+150 min');
  });
});

describe('describeBalance', () => {
  it('should show a positive balance as plain tokens', () => {
    expect(describeBalance(2500, 0)).toBe('2,500 tokens');
  });

  it('should spell out debt', () => {
    expect(describeBalance(-120, 120)).toBe('−120 tokens (owes 120)');
  });
});

describe('formatCents', () => {
  it('should format cents as dollars', () => {
    expect(formatCents(500)).toBe('$5.00');
  });
});

describe('describeSource', () => {
  it('should join kind and ref', () => {
    expect(describeSource({ kind: 'run', ref: 'req-9' })).toBe('run:req-9');
  });

  it('should shorten a long ref', () => {
    expect(describeSource({ kind: 'admin', ref: 'c'.repeat(24) })).toBe('admin:cccccccc…');
  });
});

describe('mergeLedgerPages', () => {
  it('should concatenate ledgers in page order and keep the last cursor', () => {
    const data: InfiniteData<BillingAccountDetail, string | undefined> = {
      pages: [page([entry('a'), entry('b')], 'c1'), page([entry('c')], null)],
      pageParams: [undefined, 'c1'],
    };

    const merged = mergeLedgerPages(data);

    expect(merged.ledger.map((e) => e.id)).toEqual(['a', 'b', 'c']);
    expect(merged.ledgerNextBefore).toBeNull();
  });

  it('should take everything but the ledger from the first page', () => {
    const lot = {
      lotId: 'l',
      tokensGranted: 1,
      tokensRemaining: 1,
      source: { kind: 'admin' as const, ref: 'r' },
      expiresAt: null,
      expired: false,
      createdAt: '2026-10-01T00:00:00.000Z',
    };
    const data: InfiniteData<BillingAccountDetail, string | undefined> = {
      pages: [{ ...page([entry('a')], 'c1'), lots: [lot] }, page([entry('b')], null)],
      pageParams: [undefined, 'c1'],
    };

    expect(mergeLedgerPages(data).lots).toEqual([lot]);
  });
});
