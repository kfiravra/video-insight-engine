import type { ReactNode } from 'react';
import { useBillingAccount } from '../../hooks/use-admin-api';
import { ErrorState } from '../ErrorState';
import { SkeletonPanel } from '../SkeletonPanel';
import { AccountSummary } from './AccountSummary';
import { GrantsTable, HoldsTable, LedgerTable, LotsTable } from './AccountTables';
import type { BillingRecurringGrant } from '../../lib/billing';

interface AccountTabProps {
  userId: string;
  /** Action buttons (set plan, grant minutes, ...); rendered above the summary. */
  actions?: ReactNode;
  /** A row action for each recurring grant (delete); the table has no actions column without it. */
  grantActions?: (grant: BillingRecurringGrant) => ReactNode;
}

function ActionsSlot({ children }: { children: ReactNode }) {
  if (!children) return null;
  return (
    <div className="flex flex-wrap justify-end gap-2" data-testid="account-actions">
      {children}
    </div>
  );
}

function NoAccount() {
  return (
    <p className="text-xs text-[var(--color-text-muted)] py-4 text-center" data-testid="account-none">
      No billing account yet. vie-api creates it when this user first submits a video; until then the
      free-plan defaults apply.
    </p>
  );
}

interface LoadOlderProps {
  loading: boolean;
  onClick: () => void;
}

function LoadOlder({ loading, onClick }: LoadOlderProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={loading}
      className="px-3 py-1.5 rounded-md border border-[var(--color-border)] text-xs font-medium hover:bg-[var(--color-surface-dim)] disabled:opacity-60"
    >
      {loading ? 'Loading…' : 'Load older entries'}
    </button>
  );
}

/** Read-only view of a user's minutes account: plan, buckets, lots, holds, grants, ledger. */
export function AccountTab({ userId, actions, grantActions }: AccountTabProps) {
  const { data, isLoading, isError, error, refetch, hasNextPage, fetchNextPage, isFetchingNextPage } =
    useBillingAccount(userId);

  if (isError) {
    return <ErrorState error={error} onRetry={() => refetch()} title="Failed to load billing account" />;
  }
  if (isLoading || !data) {
    return <SkeletonPanel size="sm" count={3} />;
  }
  if (data.account === null) {
    return (
      <div className="space-y-4">
        <ActionsSlot>{actions}</ActionsSlot>
        <NoAccount />
      </div>
    );
  }

  return (
    <div className="space-y-4" data-testid="account-tab">
      <ActionsSlot>{actions}</ActionsSlot>
      <AccountSummary account={data.account} />
      <LotsTable lots={data.lots} />
      <HoldsTable holds={data.holds} />
      <GrantsTable grants={data.recurringGrants} renderActions={grantActions} />
      <LedgerTable entries={data.ledger} />
      {hasNextPage && <LoadOlder loading={isFetchingNextPage} onClick={() => fetchNextPage()} />}
    </div>
  );
}
