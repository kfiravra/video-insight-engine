import { describe, it, expect } from 'vitest';
import type { InfiniteData } from '@tanstack/react-query';
import {
  describeSource,
  formatCents,
  formatMinutes,
  formatSignedMinutes,
  mergeLedgerPages,
} from './billing';
import type { BillingAccountDetail, BillingLedgerEntry } from './billing';

function entry(id: string): BillingLedgerEntry {
  return {
    id,
    userId: 'u',
    type: 'grant',
    bucket: 'credit',
    minutes: 1,
    source: { kind: 'admin', ref: 'r' },
    idempotencyKey: id,
    createdAt: '2026-10-01T00:00:00.000Z',
  };
}

function page(ledger: BillingLedgerEntry[], next: string | null): BillingAccountDetail {
  return { account: null, lots: [], holds: [], ledger, recurringGrants: [], ledgerNextBefore: next };
}

describe('formatSignedMinutes', () => {
  it.each([
    [150, '+150 min'],
    [-12, '-12 min'],
    [0, '0 min'],
    [-1500, '-1,500 min'],
  ])('should format %i as %s', (minutes, expected) => {
    expect(formatSignedMinutes(minutes)).toBe(expected);
  });
});

describe('formatMinutes', () => {
  it('should separate thousands', () => {
    expect(formatMinutes(1250)).toBe('1,250 min');
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
      minutesGranted: 1,
      minutesRemaining: 1,
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
