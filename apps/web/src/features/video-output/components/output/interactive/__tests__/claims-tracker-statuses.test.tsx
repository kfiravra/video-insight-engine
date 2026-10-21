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
      { claim: 'Hand-written code is not economically productive', status: 'stated' },
      { claim: 'P-doom is overstated', status: 'contested', timestamp: 3506 },
    ];

    render(<ClaimsTracker claims={claims} />);

    expect(badges()).toEqual(['Stated', 'Supported', 'Contested']);
  });

  it('should never print "Verified" or "Disputed" when a stored row carries the old words', () => {
    const claims: ClaimItem[] = [
      { claim: 'Fares rise 15%', source: 'Transit Authority', status: 'verified' },
      { claim: 'The plan creates 1,200 jobs', source: 'Mayor', status: 'disputed' },
      { claim: 'A structural deficit', status: 'context' },
    ];

    render(<ClaimsTracker claims={claims} />);

    expect(badges()).toEqual(['Stated', 'Supported', 'Contested']);
  });

  it('should print no "Claimed by" line when the video attributes the claim to no one', () => {
    render(<ClaimsTracker claims={[{ claim: 'A', status: 'stated' }, { claim: 'B', status: 'stated' }]} />);

    expect(screen.queryByText(/claimed by/i)).not.toBeInTheDocument();
  });

  it('should read an unknown status as stated', () => {
    expect(claimStatus(undefined)).toBe('stated');
  });

  it('should accept claims without a source when the tab props are validated', () => {
    expect(parsesClaims([{ claim: 'A', status: 'stated' }, { claim: 'B', status: 'supported' }])).toBe(true);
  });

  it('should reject a claim whose status is outside both vocabularies', () => {
    expect(parsesClaims([{ claim: 'A', status: 'confirmed' }])).toBe(false);
  });
});
