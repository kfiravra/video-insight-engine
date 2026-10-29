import { FREE_WINDOW_MS } from './minutes.js';
import type {
  BillingAccountDocument,
  CreditLotDocument,
  LedgerEntryDocument,
} from '../../repositories/billing.repository.js';

// The ledger is the truth; account buckets, open holds and lot balances are
// projections of it. This rebuilds them from the entries and lists every
// difference. Used by the tests now and by the repair job's drift check (P1.6).

export interface ProjectionDrift {
  field: string;
  expected: number | string;
  actual: number | string;
}

export interface ProjectionCheck {
  ok: boolean;
  drift: ProjectionDrift[];
}

/** Σ minutes, debits excluded: a debit posts a hold whose minutes already moved the balance. */
function balance(entries: LedgerEntryDocument[]): number {
  return entries.filter((e) => e.type !== 'debit').reduce((sum, e) => sum + e.minutes, 0);
}

function runRefsByType(entries: LedgerEntryDocument[], type: LedgerEntryDocument['type']): Set<string> {
  return new Set(entries.filter((e) => e.source.kind === 'run' && e.type === type).map((e) => e.source.ref));
}

/** requestIds with hold entries but no debit and no release. */
function openHoldRefs(entries: LedgerEntryDocument[]): string[] {
  const finished = new Set([...runRefsByType(entries, 'debit'), ...runRefsByType(entries, 'release')]);
  return [...runRefsByType(entries, 'hold')].filter((ref) => !finished.has(ref)).sort();
}

/** Free videos still counting: unreleased free holds younger than the window. */
function freeVideoTimes(entries: LedgerEntryDocument[], now: Date): string[] {
  const released = new Set(
    entries.filter((e) => e.type === 'release' && e.bucket === 'free').map((e) => e.source.ref),
  );
  const windowStart = now.getTime() - FREE_WINDOW_MS;
  return entries
    .filter((e) => e.type === 'hold' && e.bucket === 'free' && !released.has(e.source.ref))
    .filter((e) => e.createdAt.getTime() > windowStart)
    .map((e) => e.createdAt.toISOString())
    .sort();
}

function lotDrift(lots: CreditLotDocument[], entries: LedgerEntryDocument[]): ProjectionDrift[] {
  const drift: ProjectionDrift[] = [];
  const creditEntries = entries.filter((e) => e.bucket === 'credit');
  const lotIds = new Set([
    ...lots.map((lot) => lot._id.toHexString()),
    ...creditEntries.flatMap((e) => (e.lotId ? [e.lotId.toHexString()] : [])),
  ]);
  for (const lotId of lotIds) {
    const expected = balance(creditEntries.filter((e) => e.lotId?.toHexString() === lotId));
    const actual = lots.find((lot) => lot._id.toHexString() === lotId)?.minutesRemaining;
    if (actual !== expected) drift.push({ field: `lot:${lotId}`, expected, actual: actual ?? 'missing' });
  }
  return drift;
}

function compare(drift: ProjectionDrift[], field: string, expected: number | string, actual: number | string): void {
  if (expected !== actual) drift.push({ field, expected, actual });
}

/** Rebuild the account's projections from its ledger (`lots` = every lot of the account) and diff them. */
export function checkProjection(
  account: BillingAccountDocument,
  lots: CreditLotDocument[],
  entries: LedgerEntryDocument[],
  now: Date,
): ProjectionCheck {
  const drift: ProjectionDrift[] = [];
  const { buckets } = account;
  const credit = balance(entries.filter((e) => e.bucket === 'credit'));
  compare(drift, 'buckets.credit.minutesAvailable', credit, buckets.credit.minutesAvailable);
  compare(drift, 'Σ creditLots.minutesRemaining', credit, lots.reduce((sum, lot) => sum + lot.minutesRemaining, 0));
  drift.push(...lotDrift(lots, entries));

  const plan = balance(entries.filter((e) => e.bucket === 'plan'));
  compare(drift, 'buckets.plan (granted − used)', plan, buckets.plan.minutesGranted - buckets.plan.minutesUsed);

  const windowStart = now.getTime() - FREE_WINDOW_MS;
  const recordedFree = buckets.free.recentFreeVideoAt
    .filter((at) => at.getTime() > windowStart)
    .map((at) => at.toISOString())
    .sort();
  compare(drift, 'buckets.free.recentFreeVideoAt', freeVideoTimes(entries, now).join(','), recordedFree.join(','));

  const holds = account.holds.map((hold) => hold.requestId).sort();
  compare(drift, 'holds', openHoldRefs(entries).join(','), holds.join(','));
  return { ok: drift.length === 0, drift };
}
