import { describe, it, expect } from 'vitest';
import { render, screen, within } from '@testing-library/react';

import { ClaimsTracker } from '../ClaimsTracker';
import { claimStatus } from '../claim-status';
import { TAB_PROP_SCHEMAS } from '@/features/video-output/lib/tab-prop-schemas';
import type { ClaimItem } from '@vie/types';

// output-quality 1.4 (D11): a claim's badge says what the video does with it —
// stated / supported / contested — never "verified" or "disputed"; rows stored
// before it carry the old words and read in the new ones (D7).

function parsesClaims(claims: Array<Record<string, unknown>>): boolean {
  return TAB_PROP_SCHEMAS.claims_tracker.schema.safeParse({ claims }).success;
}

function badges(): string[] {
  const list = screen.getByRole('list', { name: /tracked claims/i });
  return ['Stated', 'Supported', 'Contested', 'Verified', 'Disputed', 'Context'].filter(
    (word) => within(list).queryAllByText(word).length > 0,
  );
}

describe('ClaimsTracker statuses (D11)', () => {
  it('should badge each claim with what the video does with it when the status is a D11 word', () => {
    const claims: ClaimItem[] = [
      { claim: 'Agents wrote 150,000 lines in August', status: 'supported', timestamp: 660 },
      { claim: 'Hand-written code is not economically productive', status: 'stated', timestamp: 120 },
      { claim: 'P-doom is overstated', status: 'contested', timestamp: 3506 },
    ];

    render(<ClaimsTracker claims={claims} />);

    expect(badges()).toEqual(['Stated', 'Supported', 'Contested']);
  });

  it('should never print "Verified" or "Disputed" when a stored row carries the old words', () => {
    const claims: ClaimItem[] = [
      { claim: 'Fares rise 15%', source: 'Transit Authority', status: 'verified', timestamp: 30 },
      { claim: 'The plan creates 1,200 jobs', source: 'Mayor', status: 'disputed', timestamp: 95 },
      { claim: 'A structural deficit', status: 'context', timestamp: 140 },
    ];

    render(<ClaimsTracker claims={claims} />);

    expect(badges()).toEqual(['Stated', 'Supported', 'Contested']);
  });

  it('should print no "Claimed by" line when the video attributes the claim to no one', () => {
    render(<ClaimsTracker claims={[{ claim: 'A', status: 'stated' }, { claim: 'B', status: 'stated' }]} />);

    expect(screen.queryByText(/claimed by/i)).not.toBeInTheDocument();
  });

  it('should read a missing status as no status', () => {
    expect(claimStatus({ status: undefined, timestamp: 60 })).toBeNull();
  });

  it('should accept claims without a source when the tab props are validated', () => {
    expect(parsesClaims([{ claim: 'A', status: 'stated' }, { claim: 'B', status: 'supported' }])).toBe(true);
  });

  // p1 review R9: a claim with no timestamp ships without a status — nobody could
  // check it, so it shows no badge and counts toward no status filter.
  describe('claim without a status', () => {
    const claims: ClaimItem[] = [
      { claim: 'Agents wrote 150,000 lines in August', status: 'supported', timestamp: 660 },
      { claim: 'Ruby is a joy to write' },
    ];

    it('should accept a claim without a status when the tab props are validated', () => {
      expect(parsesClaims([{ claim: 'A' }, { claim: 'B', status: null }])).toBe(true);
    });

    it('should badge only the claims that carry a status', () => {
      render(<ClaimsTracker claims={claims} />);

      expect(badges()).toEqual(['Supported']);
    });

    it('should count a status-less claim under All only', () => {
      render(<ClaimsTracker claims={claims} />);

      expect(screen.queryByRole('button', { name: /^Stated/ })).not.toBeInTheDocument();
    });

    it('should still list a status-less claim', () => {
      render(<ClaimsTracker claims={claims} />);

      expect(screen.getByText('Ruby is a joy to write')).toBeInTheDocument();
    });
  });

  // pC review: a v10 row's status was required and its timestamp optional, so a
  // stored claim nobody can check still carries "verified" / "disputed" — the
  // tracker drops it on render, as the summarizer now does (D13).
  describe('stored claim with a status but no timestamp', () => {
    const v10: ClaimItem[] = [
      { claim: 'Fares rise 15%', status: 'verified', timestamp: 30 },
      { claim: 'The plan creates 1,200 jobs', status: 'disputed' },
    ];

    it('should badge no status when the claim carries no timestamp', () => {
      render(<ClaimsTracker claims={v10} />);

      expect(badges()).toEqual(['Supported']);
    });

    it('should count no filter for a status nobody can check', () => {
      render(<ClaimsTracker claims={v10} />);

      expect(screen.queryByRole('button', { name: /^Contested/ })).not.toBeInTheDocument();
    });

    it('should read the status as none when the timestamp is missing', () => {
      expect(claimStatus({ status: 'disputed', timestamp: null })).toBeNull();
    });
  });

  it('should reject a claim whose status is outside both vocabularies', () => {
    expect(parsesClaims([{ claim: 'A', status: 'confirmed' }])).toBe(false);
  });
});
