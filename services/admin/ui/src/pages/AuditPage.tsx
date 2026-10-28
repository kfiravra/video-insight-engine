import { useState } from 'react';
import type { FormEvent } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useAuditLog } from '../hooks/use-admin-api';
import type { AuditEntry } from '../lib/billing-actions';
import { diffAudit } from '../lib/audit-diff';
import { formatDateTime, shortId, timeAgo } from '../lib/format';
import { ErrorState } from '../components/ErrorState';
import { Panel } from '../components/Panel';
import { SkeletonPanel } from '../components/SkeletonPanel';

const FILTER_INPUT =
  'px-3 py-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] text-sm text-[var(--color-text)] placeholder:text-[var(--color-text-faint)] outline-none focus:ring-2 focus:ring-[var(--color-primary)]';

/** Who acted: their email when the row has one, a system actor by name, else a short user id. */
function actorLabel(entry: AuditEntry): string {
  if (entry.actorEmail) return entry.actorEmail;
  return entry.actorId.startsWith('system:') ? entry.actorId : shortId(entry.actorId);
}

function ChangeDiff({ entry }: { entry: AuditEntry }) {
  const lines = diffAudit(entry.before, entry.after);
  if (lines.length === 0) {
    return <p className="text-xs text-[var(--color-text-muted)]">No before/after recorded for this action.</p>;
  }
  return (
    <table className="w-full text-[11px]" data-testid={`audit-diff-${entry.id}`}>
      <thead>
        <tr className="text-left text-[var(--color-text-faint)]">
          <th className="py-1 pe-3 font-medium">Field</th>
          <th className="py-1 pe-3 font-medium">Before</th>
          <th className="py-1 font-medium">After</th>
        </tr>
      </thead>
      <tbody>
        {lines.map((line) => (
          <tr key={line.path} className="border-t border-[var(--color-border)]">
            <td className="py-1 pe-3 font-mono">{line.path}</td>
            <td className="py-1 pe-3 font-mono text-[var(--color-text-muted)] break-all">{line.before ?? '—'}</td>
            <td className="py-1 font-mono break-all" style={{ color: 'var(--color-primary)' }}>
              {line.after ?? '—'}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function AuditRow({ entry }: { entry: AuditEntry }) {
  const [open, setOpen] = useState(false);

  return (
    <>
      <tr className="border-t border-[var(--color-border)] align-top" data-testid={`audit-row-${entry.id}`}>
        <td className="p-2 text-[10px] whitespace-nowrap" title={timeAgo(entry.createdAt)}>
          {formatDateTime(entry.createdAt)}
        </td>
        <td className="p-2 font-mono">{entry.action}</td>
        <td className="p-2 hidden sm:table-cell" title={entry.actorId}>
          {actorLabel(entry)}
        </td>
        <td className="p-2 font-mono" title={entry.targetId}>
          <span className="text-[var(--color-text-faint)]">{entry.targetType}:</span> {shortId(entry.targetId)}
        </td>
        <td className="p-2 hidden md:table-cell truncate max-w-[220px]" title={entry.reason ?? undefined}>
          {entry.reason ?? '—'}
        </td>
        <td className="p-2 text-right">
          <button
            type="button"
            aria-expanded={open}
            aria-label={`${open ? 'Hide' : 'Show'} changes for ${entry.action}`}
            onClick={() => setOpen((value) => !value)}
            className="px-2 py-1 rounded border border-[var(--color-border)] text-[10px] font-medium hover:bg-[var(--color-surface-dim)]"
          >
            {open ? 'Hide' : 'Changes'}
          </button>
        </td>
      </tr>
      {open && (
        <tr className="bg-[var(--color-surface-dim)]">
          <td colSpan={6} className="p-3">
            <ChangeDiff entry={entry} />
          </td>
        </tr>
      )}
    </>
  );
}

interface FilterFormProps {
  targetId: string;
  actorId: string;
  onApply: (filters: { targetId: string; actorId: string }) => void;
}

function FilterForm({ targetId, actorId, onApply }: FilterFormProps) {
  const [draft, setDraft] = useState({ targetId, actorId });

  function submit(event: FormEvent) {
    event.preventDefault();
    onApply({ targetId: draft.targetId.trim(), actorId: draft.actorId.trim() });
  }

  return (
    <form className="flex flex-wrap items-center gap-2" onSubmit={submit}>
      <input
        value={draft.targetId}
        onChange={(e) => setDraft((current) => ({ ...current, targetId: e.target.value }))}
        placeholder="Target id (user, video…)"
        aria-label="Target id"
        data-testid="audit-filter-target"
        className={`${FILTER_INPUT} flex-1 min-w-[180px]`}
      />
      <input
        value={draft.actorId}
        onChange={(e) => setDraft((current) => ({ ...current, actorId: e.target.value }))}
        placeholder="Actor id"
        aria-label="Actor id"
        data-testid="audit-filter-actor"
        className={`${FILTER_INPUT} flex-1 min-w-[180px]`}
      />
      <button
        type="submit"
        className="px-3 py-2 rounded-lg bg-[var(--color-primary)] text-white text-sm font-medium"
      >
        Filter
      </button>
      <button
        type="button"
        onClick={() => {
          setDraft({ targetId: '', actorId: '' });
          onApply({ targetId: '', actorId: '' });
        }}
        className="px-3 py-2 rounded-lg border border-[var(--color-border)] text-sm font-medium hover:bg-[var(--color-surface-dim)]"
      >
        Clear
      </button>
    </form>
  );
}

/** The admin action log: who changed what, when and why, newest first. Filters live in the URL. */
export function AuditPage() {
  const [params, setParams] = useSearchParams();
  const targetId = params.get('targetId') ?? '';
  const actorId = params.get('actorId') ?? '';
  const { data, isLoading, isError, error, refetch, hasNextPage, fetchNextPage, isFetchingNextPage } = useAuditLog({
    targetId: targetId || undefined,
    actorId: actorId || undefined,
  });

  function applyFilters(next: { targetId: string; actorId: string }) {
    const nextParams = new URLSearchParams();
    if (next.targetId) nextParams.set('targetId', next.targetId);
    if (next.actorId) nextParams.set('actorId', next.actorId);
    setParams(nextParams);
  }

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-bold">Audit log</h2>
        <p className="text-xs text-[var(--color-text-muted)]">
          Every admin change, newest first. A user&apos;s history is the filter by their user id.
        </p>
      </div>

      <FilterForm key={`${targetId}|${actorId}`} targetId={targetId} actorId={actorId} onApply={applyFilters} />

      {isError ? (
        <ErrorState error={error} onRetry={() => refetch()} title="Failed to load the audit log" />
      ) : isLoading || !data ? (
        <SkeletonPanel size="sm" count={5} />
      ) : (
        <Panel tone="raised" padding="none">
          {data.length === 0 ? (
            <p className="p-6 text-center text-xs text-[var(--color-text-muted)]">No audit entries match.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead className="bg-[var(--color-surface-dim)]">
                  <tr className="text-left">
                    <th className="p-2 font-medium">Time</th>
                    <th className="p-2 font-medium">Action</th>
                    <th className="p-2 font-medium hidden sm:table-cell">Actor</th>
                    <th className="p-2 font-medium">Target</th>
                    <th className="p-2 font-medium hidden md:table-cell">Reason</th>
                    <th className="p-2 font-medium text-right">Diff</th>
                  </tr>
                </thead>
                <tbody>
                  {data.map((entry) => (
                    <AuditRow key={entry.id} entry={entry} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
      )}

      {hasNextPage && (
        <button
          type="button"
          onClick={() => fetchNextPage()}
          disabled={isFetchingNextPage}
          className="px-3 py-1.5 rounded-md border border-[var(--color-border)] text-xs font-medium hover:bg-[var(--color-surface-dim)] disabled:opacity-60"
        >
          {isFetchingNextPage ? 'Loading…' : 'Load older entries'}
        </button>
      )}
    </div>
  );
}
