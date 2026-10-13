import { useState } from 'react';
import { useUsageByRun } from '../hooks/use-admin-api';
import type { RunSummary, RunCallSummary } from '../lib/api';
import { formatCost, formatDateTime, formatUsageVolume } from '../lib/format';
import { buildLangfuseTraceUrl } from '../lib/langfuse';
import { Panel } from './Panel';
import { SkeletonPanel } from './SkeletonPanel';
import { ErrorState } from './ErrorState';

/** Truncate a request_id to a readable short form, e.g. "req-aaa…111". */
function shortRequestId(id: string): string {
  if (id.length <= 14) return id;
  return `${id.slice(0, 8)}…${id.slice(-4)}`;
}

interface RegenBadgeProps {
  ordinal: number;
}

function RegenBadge({ ordinal }: RegenBadgeProps) {
  return (
    <span
      className="inline-flex items-center px-1.5 py-0.5 rounded text-[9px] font-semibold uppercase tracking-wide bg-[var(--color-warning-soft)] text-[var(--color-warning)]"
      title={`Regeneration #${ordinal}`}
    >
      regen #{ordinal}
    </span>
  );
}

/** Partial-result marker: the run's doc is flagged degraded (a failed
 *  extraction field group, a dropped chunked batch or critical coverage —
 *  the same flag the web app's DegradedNotice shows). */
function DegradedBadge() {
  return (
    <span
      className="inline-flex items-center px-1.5 py-0.5 rounded text-[9px] font-semibold uppercase tracking-wide bg-[var(--color-danger-soft)] text-[var(--color-danger)]"
      title="Degraded run: an extraction field group failed, a chunked batch was dropped, or coverage was critical — the stored output is a partial result"
    >
      degraded
    </span>
  );
}

interface RunCallsTableProps {
  calls: RunCallSummary[];
}

function RunCallsTable({ calls }: RunCallsTableProps) {
  if (calls.length === 0) {
    return (
      <div className="px-4 py-3 text-xs text-[var(--color-text-muted)]">
        No individual calls recorded.
      </div>
    );
  }

  return (
    <div className="px-4 pt-1 pb-3">
      <div className="rounded-lg border border-[var(--color-border)] overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-[11px]">
            <thead>
              <tr className="bg-[var(--color-surface-dim)] border-b border-[var(--color-border)]">
                <th className="text-left py-1.5 px-3 font-medium text-[var(--color-text-faint)]">Feature</th>
                <th className="text-left py-1.5 px-3 font-medium text-[var(--color-text-faint)]">Model</th>
                <th className="text-right py-1.5 px-3 font-medium text-[var(--color-text-faint)]">Cost</th>
                <th className="text-right py-1.5 px-3 font-medium text-[var(--color-text-faint)] hidden sm:table-cell">Tokens</th>
                <th className="text-right py-1.5 px-3 font-medium text-[var(--color-text-faint)] hidden md:table-cell">Duration</th>
                <th className="text-right py-1.5 px-3 font-medium text-[var(--color-text-faint)]">When</th>
              </tr>
            </thead>
            <tbody>
              {calls.map((call) => (
                <tr
                  key={call.id}
                  className="border-b border-[var(--color-border)] last:border-0 hover:bg-[var(--color-surface-dim)] transition-colors"
                >
                  <td className="py-1.5 px-3">
                    <span className="px-1.5 py-0.5 rounded text-[10px] bg-[var(--color-surface-dim)]">
                      {call.feature ?? '—'}
                    </span>
                  </td>
                  <td className="py-1.5 px-3 font-mono truncate max-w-[160px]">
                    {call.model ?? '—'}
                  </td>
                  <td className="py-1.5 px-3 text-right font-mono font-medium text-[var(--color-primary)]">
                    {formatCost(call.cost_usd)}
                  </td>
                  <td className="py-1.5 px-3 text-right hidden sm:table-cell">
                    {formatUsageVolume(call)}
                  </td>
                  <td className="py-1.5 px-3 text-right text-[var(--color-text-muted)] hidden md:table-cell">
                    {call.duration_ms != null ? `${call.duration_ms}ms` : '—'}
                  </td>
                  <td className="py-1.5 px-3 text-right text-[var(--color-text-faint)] text-[10px]">
                    {formatDateTime(call.timestamp)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

interface RunRowProps {
  run: RunSummary;
  isOpen: boolean;
  onToggle: () => void;
}

function RunRow({ run, isOpen, onToggle }: RunRowProps) {
  const isUnattributed = run.request_id === null;
  // Prefer the server-resolved direct trace URL (lands on the exact trace);
  // fall back to a build-time tag-filter URL when VITE_LANGFUSE_* is configured.
  const langfuseUrl =
    run.langfuse_url ??
    buildLangfuseTraceUrl({
      requestId: run.request_id,
      videoSummaryId: run.video_summary_id,
    });

  return (
    <>
      <tr
        className={`border-b border-[var(--color-border)] transition-colors ${
          isOpen
            ? 'bg-[var(--color-primary-soft)] border-b-0'
            : 'hover:bg-[var(--color-surface-dim)]'
        }`}
        style={{ borderLeft: isOpen ? '3px solid var(--color-primary)' : '3px solid transparent' }}
      >
        {/* Expand toggle */}
        <td className="p-3 w-8">
          <button
            type="button"
            onClick={onToggle}
            className="flex items-center justify-center w-6 h-6 rounded-md hover:bg-[var(--color-surface-dim)] text-[var(--color-text-muted)] transition-colors"
            aria-label={isOpen ? 'Collapse' : 'Expand'}
          >
            <span
              className="inline-flex transition-transform duration-200"
              style={{ transform: isOpen ? 'rotate(90deg)' : 'rotate(0deg)' }}
              aria-hidden="true"
            >
              ▶
            </span>
          </button>
        </td>

        {/* Run ID */}
        <td className="p-3 min-w-0">
          {isUnattributed ? (
            <span className="text-[var(--color-text-muted)] italic text-[11px]">
              unattributed (legacy)
            </span>
          ) : (
            <span className="font-mono text-[11px] text-[var(--color-text)]">
              {shortRequestId(run.request_id!)}
            </span>
          )}
          {run.regen_ordinal != null && run.regen_ordinal > 1 && (
            <span className="ml-2">
              <RegenBadge ordinal={run.regen_ordinal} />
            </span>
          )}
          {run.degraded === true && (
            <span className="ml-2">
              <DegradedBadge />
            </span>
          )}
        </td>

        {/* Timestamp */}
        <td className="p-3 text-[11px] text-[var(--color-text-muted)] hidden sm:table-cell">
          {formatDateTime(run.first_call)}
        </td>

        {/* Cost */}
        <td className="p-3 text-right font-mono font-bold text-[var(--color-primary)]">
          {formatCost(run.total_cost_usd)}
        </td>

        {/* Call count */}
        <td className="p-3 text-right text-[11px] text-[var(--color-text-muted)]">
          {run.call_count}
        </td>

        {/* Langfuse link */}
        <td className="p-3 pr-4 text-right">
          {langfuseUrl != null ? (
            <a
              href={langfuseUrl}
              target="_blank"
              rel="noreferrer noopener"
              className="text-[10px] text-[var(--color-primary)] hover:underline whitespace-nowrap"
              aria-label="Open in Langfuse"
            >
              Langfuse ↗
            </a>
          ) : null}
        </td>
      </tr>

      {/* Expanded call details */}
      {isOpen && (
        <tr style={{ borderLeft: '3px solid var(--color-primary)' }}>
          <td
            colSpan={6}
            className="bg-[var(--color-surface)] border-b border-[var(--color-border)]"
          >
            <RunCallsTable calls={run.calls} />
          </td>
        </tr>
      )}
    </>
  );
}

interface PipelineRunsPanelProps {
  days?: number;
  limit?: number;
}

export function PipelineRunsPanel({ days = 30, limit = 20 }: PipelineRunsPanelProps) {
  const { data, isLoading, isError, error, refetch } = useUsageByRun(days, limit);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  function toggle(key: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  if (isError) {
    return <ErrorState error={error} onRetry={() => refetch()} title="Failed to load pipeline runs" />;
  }

  if (isLoading || !data) {
    return <SkeletonPanel size="lg" />;
  }

  return (
    <Panel title="Pipeline Runs" tone="raised" padding="none">
      {data.length === 0 ? (
        <div className="p-6 text-center text-xs text-[var(--color-text-muted)]">
          No pipeline runs found in the last {days} days.
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-[var(--color-border)] bg-[var(--color-surface-dim)]">
                <th className="w-8 p-3" />
                <th className="text-left p-3 font-medium text-[var(--color-text-muted)]">Run ID</th>
                <th className="text-left p-3 font-medium text-[var(--color-text-muted)] hidden sm:table-cell">
                  Started
                </th>
                <th className="text-right p-3 font-medium text-[var(--color-text-muted)]">Cost</th>
                <th className="text-right p-3 font-medium text-[var(--color-text-muted)]">Calls</th>
                <th className="text-right p-3 pr-4 font-medium text-[var(--color-text-muted)]">Trace</th>
              </tr>
            </thead>
            <tbody>
              {data.map((run, i) => {
                const key = run.request_id ?? `unattr-${i}`;
                return (
                  <RunRow
                    key={key}
                    run={run}
                    isOpen={expanded.has(key)}
                    onToggle={() => toggle(key)}
                  />
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}
