import type { ClaimItem, ClaimStatus, LegacyClaimStatus } from '@vie/types';

/** What the video does with a claim (output-quality D11) — never a verdict of ours. */
const CLAIM_STATUSES: readonly ClaimStatus[] = ['stated', 'supported', 'contested'];

/** Rows stored before D11 carry the old words; they read in the new ones (D7). */
const LEGACY_STATUS: Record<LegacyClaimStatus, ClaimStatus> = {
  verified: 'supported',
  disputed: 'contested',
  context: 'stated',
};

function isClaimStatus(value: string): value is ClaimStatus {
  return (CLAIM_STATUSES as readonly string[]).includes(value);
}

function isLegacyStatus(value: string): value is LegacyClaimStatus {
  return Object.keys(LEGACY_STATUS).includes(value);
}

/** A claim's status in D11's words: a legacy word is mapped, anything unknown is
 *  "stated" — the non-asserting word. No status, or no timestamp → null: a status
 *  nobody can check is not shown (D13), so it gets no badge and no filter count.
 *  The summarizer drops it now; rows stored before v11 still carry one beside a
 *  missing timestamp, and are fixed here on render (D7). */
export function claimStatus(claim: Pick<ClaimItem, 'status' | 'timestamp'>): ClaimStatus | null {
  const { status, timestamp } = claim;
  if (!status || timestamp == null) return null;
  if (isClaimStatus(status)) return status;
  return isLegacyStatus(status) ? LEGACY_STATUS[status] : 'stated';
}
