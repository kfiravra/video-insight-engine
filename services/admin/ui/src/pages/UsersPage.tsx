import { useMemo, useState } from 'react';
import { useUserCosts } from '../hooks/use-admin-api';
import type { UserCostRow } from '../lib/api';
import { formatCost, formatNumber, shortId } from '../lib/format';
import { Panel } from '../components/Panel';
import { SkeletonPanel } from '../components/SkeletonPanel';
import { ErrorState } from '../components/ErrorState';
import { UserDetailDrawer } from '../components/users/UserDetailDrawer';

interface UsersPageProps {
  days?: number;
}

type SortKey = 'effectiveUsd' | 'totalCostUsd' | 'videoCount' | 'creditAdjustmentUsd';

function compareRows(a: UserCostRow, b: UserCostRow, key: SortKey): number {
  return (a[key] ?? 0) - (b[key] ?? 0);
}

function planBadge(plan: string): string {
  return plan === 'pro'
    ? 'bg-[var(--color-success-soft)] text-[var(--color-success)]'
    : 'bg-[var(--color-surface-dim)] text-[var(--color-text-muted)]';
}

export function UsersPage({ days = 7 }: UsersPageProps = {}) {
  const { data, isLoading, isError, error, refetch } = useUserCosts(days, 100);
  const [filter, setFilter] = useState('');
  const [sortKey, setSortKey] = useState<SortKey>('effectiveUsd');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');
  const [selectedUser, setSelectedUser] = useState<UserCostRow | null>(null);

  const visible = useMemo(() => {
    if (!data) return [];
    const needle = filter.trim().toLowerCase();
    const filtered = needle
      ? data.filter((u) =>
          (u.email ?? '').toLowerCase().includes(needle)
          || (u.name ?? '').toLowerCase().includes(needle)
          || u.userId.toLowerCase().includes(needle),
        )
      : data;
    const sign = sortDir === 'desc' ? -1 : 1;
    return [...filtered].sort((a, b) => compareRows(a, b, sortKey) * sign);
  }, [data, filter, sortKey, sortDir]);

  function toggleSort(key: SortKey) {
    if (sortKey === key) {
      setSortDir((d) => (d === 'desc' ? 'asc' : 'desc'));
    } else {
      setSortKey(key);
      setSortDir('desc');
    }
  }

  if (isError) {
    return (
      <div className="space-y-4">
        <h2 className="text-lg font-bold">Users</h2>
        <ErrorState error={error} onRetry={() => refetch()} title="Failed to load user costs" />
      </div>
    );
  }

  if (isLoading || !data) {
    return (
      <div className="space-y-3">
        <h2 className="text-lg font-bold">Users</h2>
        <SkeletonPanel size="sm" count={6} />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-lg font-bold">Users by cost</h2>
        <span className="text-xs text-[var(--color-text-muted)]">
          {visible.length} of {data.length} users (last {days} days)
        </span>
      </div>

      <input
        type="search"
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
        placeholder="Filter by email, name, or id…"
        aria-label="Filter users"
        data-testid="users-filter"
        className="w-full px-3 py-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] text-sm text-[var(--color-text)] placeholder:text-[var(--color-text-faint)] outline-none focus:ring-2 focus:ring-[var(--color-primary)]"
      />

      <Panel tone="raised" padding="none">
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-[var(--color-border)] bg-[var(--color-surface-dim)]">
                <th className="text-left p-3">User</th>
                <th className="text-left p-3 hidden sm:table-cell">Plan</th>
                <SortableTh label="Effective" active={sortKey === 'effectiveUsd'} dir={sortDir} onClick={() => toggleSort('effectiveUsd')} />
                <SortableTh label="Raw" active={sortKey === 'totalCostUsd'} dir={sortDir} onClick={() => toggleSort('totalCostUsd')} />
                <SortableTh label="Credit" active={sortKey === 'creditAdjustmentUsd'} dir={sortDir} onClick={() => toggleSort('creditAdjustmentUsd')} />
                <SortableTh label="Videos" active={sortKey === 'videoCount'} dir={sortDir} onClick={() => toggleSort('videoCount')} />
              </tr>
            </thead>
            <tbody>
              {visible.length === 0 ? (
                <tr>
                  <td colSpan={6} className="p-6 text-center text-xs text-[var(--color-text-muted)]">
                    No users with usage, a minutes account or a sign-up in the last {days} days.
                  </td>
                </tr>
              ) : (
                visible.map((u) => (
                  <tr
                    key={u.userId}
                    onClick={() => setSelectedUser(u)}
                    className="border-b border-[var(--color-border)] last:border-0 hover:bg-[var(--color-surface-dim)] cursor-pointer transition-colors"
                    data-testid={`user-row-${u.userId}`}
                  >
                    <td className="p-3 min-w-0">
                      <div className="font-medium text-[var(--color-text)] truncate max-w-[260px]">
                        {u.email ?? u.name ?? shortId(u.userId)}
                      </div>
                      <div className="text-[10px] text-[var(--color-text-faint)] font-mono truncate">
                        {shortId(u.userId)}
                      </div>
                    </td>
                    <td className="p-3 hidden sm:table-cell">
                      <span className={`inline-block px-2 py-0.5 rounded text-[10px] font-semibold ${planBadge(u.plan)}`}>
                        {u.plan}
                      </span>
                    </td>
                    <td className="p-3 text-right font-mono font-bold text-[var(--color-primary)]">
                      {formatCost(u.effectiveUsd)}
                    </td>
                    <td className="p-3 text-right font-mono">{formatCost(u.totalCostUsd)}</td>
                    <td
                      className="p-3 text-right font-mono"
                      style={{ color: u.creditAdjustmentUsd < 0 ? 'var(--color-success)' : 'var(--color-text-muted)' }}
                    >
                      {formatCost(u.creditAdjustmentUsd)}
                    </td>
                    <td className="p-3 text-right">{formatNumber(u.videoCount)}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </Panel>

      {selectedUser && (
        <UserDetailDrawer
          user={selectedUser}
          onClose={() => setSelectedUser(null)}
        />
      )}
    </div>
  );
}

interface SortableThProps {
  label: string;
  active: boolean;
  dir: 'asc' | 'desc';
  onClick: () => void;
}

function SortableTh({ label, active, dir, onClick }: SortableThProps) {
  return (
    <th className="text-right p-3">
      <button
        type="button"
        onClick={onClick}
        className="inline-flex items-center gap-1 text-[10px] uppercase tracking-wide font-medium text-[var(--color-text-faint)] hover:text-[var(--color-text)]"
      >
        {label}
        <span aria-hidden="true">{active ? (dir === 'desc' ? '↓' : '↑') : ''}</span>
      </button>
    </th>
  );
}
