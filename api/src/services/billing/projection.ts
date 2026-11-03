import type {
  BillingAccountDocument,
  CreditLotDocument,
  LedgerEntryDocument,
} from '../../repositories/billing.repository.js';

// The ledger is the truth; account buckets, open holds and lot balances are
// projections of it. This rebuilds them from the token entries and lists
// every difference. Minute entries (history from before the token
// migration, closed by its `convert` entries) are not part of the token
// projection. Used by the tests and by the repair job's drift check.

export interface ProjectionDrift {
  field: string;
  expected: number | string;
  actual: number | string;
}

export interface ProjectionCheck {
  ok: boolean;
  drift: ProjectionDrift[];
}

/** Σ tokens, hold-mirroring debits excluded: they post tokens a hold already moved. */
function balance(entries: LedgerEntryDocument[]): number {
  return entries.filter((e) => !(e.type === 'debit' && e.mirrorsHold)).reduce((sum, e) => sum + (e.tokens ?? 0), 0);
}

function holdRefsByType(entries: LedgerEntryDocument[], type: LedgerEntryDocument['type']): Set<string> {
  // Execution debits carry `executionId` and never had a hold.
  const runEntries = entries.filter((e) => e.source.kind === 'run' && e.type === type && !e.executionId);
  return new Set(runEntries.map((e) => e.source.ref));
}

/** Run ids with hold entries but no debit and no release. */
function openHoldRefs(entries: LedgerEntryDocument[]): string[] {
  const finished = new Set([...holdRefsByType(entries, 'debit'), ...holdRefsByType(entries, 'release')]);
  return [...holdRefsByType(entries, 'hold')].filter((ref) => !finished.has(ref)).sort();
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
    const actual = lots.find((lot) => lot._id.toHexString() === lotId)?.tokensRemaining;
    if (actual !== expected) drift.push({ field: `lot:${lotId}`, expected, actual: actual ?? 'missing' });
  }
  return drift;
}

function compare(drift: ProjectionDrift[], field: string, expected: number | string, actual: number | string): void {
  if (expected !== actual) drift.push({ field, expected, actual });
}

/** Rebuild the account's projections from its ledger (`lots` = every token lot of the account) and diff them. */
export function checkProjection(
  account: BillingAccountDocument,
  lots: CreditLotDocument[],
  entries: LedgerEntryDocument[],
): ProjectionCheck {
  const drift: ProjectionDrift[] = [];
  const { buckets } = account;
  const tokenEntries = entries.filter((e) => e.unit === 'tokens');

  const credit = balance(tokenEntries.filter((e) => e.bucket === 'credit'));
  compare(drift, 'buckets.credit.tokensAvailable', credit, buckets.credit.tokensAvailable);
  compare(drift, 'Σ creditLots.tokensRemaining', credit, lots.reduce((sum, lot) => sum + lot.tokensRemaining, 0));
  drift.push(...lotDrift(lots, tokenEntries));

  const plan = balance(tokenEntries.filter((e) => e.bucket === 'plan'));
  compare(drift, 'buckets.plan (granted − used)', plan, buckets.plan.tokensGranted - buckets.plan.tokensUsed);

  const debt = -balance(tokenEntries.filter((e) => e.bucket === 'debt'));
  compare(drift, 'buckets.debt.tokens', debt, buckets.debt.tokens);
  if (buckets.debt.tokens < 0) drift.push({ field: 'buckets.debt.tokens ≥ 0', expected: 0, actual: buckets.debt.tokens });

  const holds = account.holds.filter((hold) => hold.tokens > 0).map((hold) => hold.requestId).sort();
  compare(drift, 'holds', openHoldRefs(tokenEntries).join(','), holds.join(','));
  return { ok: drift.length === 0, drift };
}
