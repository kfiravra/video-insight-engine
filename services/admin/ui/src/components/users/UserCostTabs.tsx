import { useUserActivity, useUserCostDetail } from '../../hooks/use-admin-api';
import { formatCost, formatDateTime, formatDuration, formatNumber, formatUsageVolume, timeAgo } from '../../lib/format';
import { ErrorState } from '../ErrorState';
import { SkeletonPanel } from '../SkeletonPanel';

interface OverviewTabProps {
  userId: string;
}

export function OverviewTab({ userId }: OverviewTabProps) {
  const { data, isLoading, isError, error, refetch } = useUserCostDetail(userId, 30);

  if (isError) {
    return <ErrorState error={error} onRetry={() => refetch()} title="Failed to load cost summary" />;
  }
  if (isLoading || !data) {
    return <SkeletonPanel size="sm" count={3} />;
  }

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        <StatTile label="Effective" value={formatCost(data.user.effectiveUsd)} highlight />
        <StatTile label="Raw" value={formatCost(data.user.totalCostUsd)} />
        <StatTile label="Credit" value={formatCost(data.user.creditAdjustmentUsd)} />
        <StatTile label="Videos" value={formatNumber(data.user.videoCount)} />
      </div>

      <section className="space-y-2">
        <h4 className="text-xs font-semibold uppercase tracking-wide text-[var(--color-text-muted)]">
          Daily breakdown
        </h4>
        {data.daily.length === 0 ? (
          <p className="text-xs text-[var(--color-text-muted)]">No usage in the window.</p>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-[var(--color-border)]">
            <table className="w-full text-[11px]">
              <thead className="bg-[var(--color-surface-dim)]">
                <tr>
                  <th className="p-2 text-left font-medium">Date</th>
                  <th className="p-2 text-right font-medium">Effective</th>
                  <th className="p-2 text-right font-medium">Raw</th>
                  <th className="p-2 text-right font-medium">Credit</th>
                  <th className="p-2 text-right font-medium">Videos</th>
                </tr>
              </thead>
              <tbody>
                {data.daily.map((row) => (
                  <tr key={row.date} className="border-t border-[var(--color-border)]">
                    <td className="p-2">{row.date}</td>
                    <td className="p-2 text-right font-mono font-medium text-[var(--color-primary)]">
                      {formatCost(row.effectiveUsd)}
                    </td>
                    <td className="p-2 text-right font-mono">{formatCost(row.totalCostUsd)}</td>
                    <td className="p-2 text-right font-mono">{formatCost(row.creditAdjustmentUsd)}</td>
                    <td className="p-2 text-right">{formatNumber(row.videoCount)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

interface VideosTabProps {
  userId: string;
}

export function VideosTab({ userId }: VideosTabProps) {
  const { data, isLoading, isError, error, refetch } = useUserActivity(userId);

  if (isError) {
    return <ErrorState error={error} onRetry={() => refetch()} title="Failed to load videos" />;
  }
  if (isLoading || !data) {
    return <SkeletonPanel size="sm" count={4} />;
  }

  const videos = data.videos;

  if (videos.length === 0) {
    return (
      <p className="text-xs text-[var(--color-text-muted)] py-4 text-center">
        No videos generated yet.
      </p>
    );
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-[var(--color-border)]">
      <table className="w-full text-[11px]">
        <thead className="bg-[var(--color-surface-dim)]">
          <tr>
            <th className="p-2 text-left font-medium">Title</th>
            <th className="p-2 text-left font-medium hidden sm:table-cell">Channel</th>
            <th className="p-2 text-right font-medium hidden md:table-cell">Duration</th>
            <th className="p-2 text-center font-medium">Status</th>
            <th className="p-2 text-right font-medium hidden sm:table-cell">Added</th>
          </tr>
        </thead>
        <tbody>
          {videos.map((v) => (
            <tr key={v.userVideoId} className="border-t border-[var(--color-border)] hover:bg-[var(--color-surface-dim)]">
              <td className="p-2">
                <div className="font-medium truncate max-w-[220px] text-[var(--color-text)]">
                  {v.title ?? v.youtubeId}
                </div>
                <div className="text-[var(--color-text-faint)] font-mono text-[9px] truncate">
                  {v.youtubeId}
                </div>
              </td>
              <td className="p-2 hidden sm:table-cell text-[var(--color-text-muted)] truncate max-w-[120px]">
                {v.channel ?? '—'}
              </td>
              <td className="p-2 text-right hidden md:table-cell text-[var(--color-text-muted)]">
                {formatDuration(v.duration)}
              </td>
              <td className="p-2 text-center">
                <span
                  className="inline-block w-2 h-2 rounded-full"
                  style={{
                    background:
                      v.status === 'completed' ? 'var(--color-success)' :
                      v.status === 'processing' ? 'var(--color-warning)' :
                      v.status === 'error' ? 'var(--color-danger)' :
                      'var(--color-text-muted)',
                  }}
                  title={v.status ?? 'unknown'}
                />
              </td>
              <td className="p-2 text-right hidden sm:table-cell text-[var(--color-text-faint)] text-[10px]">
                {formatDateTime(v.addedAt)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

interface AssistantTabProps {
  userId: string;
}

export function AssistantTab({ userId }: AssistantTabProps) {
  const { data, isLoading, isError, error, refetch } = useUserActivity(userId);

  if (isError) {
    return <ErrorState error={error} onRetry={() => refetch()} title="Failed to load assistant calls" />;
  }
  if (isLoading || !data) {
    return <SkeletonPanel size="sm" count={4} />;
  }

  const calls = data.assistantCalls;

  if (calls.length === 0) {
    return (
      <p className="text-xs text-[var(--color-text-muted)] py-4 text-center">
        No assistant activity yet.
      </p>
    );
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-[var(--color-border)]">
      <table className="w-full text-[11px]">
        <thead className="bg-[var(--color-surface-dim)]">
          <tr>
            <th className="p-2 text-left font-medium">Feature</th>
            <th className="p-2 text-left font-medium hidden sm:table-cell">Model</th>
            <th className="p-2 text-right font-medium">Cost</th>
            <th className="p-2 text-right font-medium hidden sm:table-cell">Tokens</th>
            <th className="p-2 text-right font-medium hidden md:table-cell">When</th>
          </tr>
        </thead>
        <tbody>
          {calls.map((call) => (
            <tr key={call.id} className="border-t border-[var(--color-border)] hover:bg-[var(--color-surface-dim)]">
              <td className="p-2">
                <span className="px-1.5 py-0.5 rounded text-[10px] bg-[var(--color-surface-dim)]">
                  {call.feature ?? '—'}
                </span>
              </td>
              <td className="p-2 font-mono hidden sm:table-cell truncate max-w-[120px] text-[var(--color-text-muted)]">
                {call.model ?? '—'}
              </td>
              <td className="p-2 text-right font-mono font-medium text-[var(--color-primary)]">
                {formatCost(call.costUsd)}
              </td>
              <td className="p-2 text-right hidden sm:table-cell text-[var(--color-text-muted)]">
                {formatUsageVolume({ unit: call.unit, audio_seconds: call.audioSeconds, tokens_in: call.tokensIn, tokens_out: call.tokensOut })}
              </td>
              <td className="p-2 text-right hidden md:table-cell text-[var(--color-text-faint)] text-[10px]">
                {formatDateTime(call.timestamp)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

interface AdjustmentsTabProps {
  userId: string;
}

export function AdjustmentsTab({ userId }: AdjustmentsTabProps) {
  const { data, isLoading, isError, error, refetch } = useUserCostDetail(userId, 30);

  if (isError) {
    return <ErrorState error={error} onRetry={() => refetch()} title="Failed to load adjustments" />;
  }
  if (isLoading || !data) {
    return <SkeletonPanel size="sm" count={2} />;
  }

  return (
    <div className="space-y-4">
      <section className="space-y-2">
        <h4 className="text-xs font-semibold uppercase tracking-wide text-[var(--color-text-muted)]">
          Past USD adjustments
        </h4>
        <p className="text-[10px] text-[var(--color-text-faint)]">
          History from before the minutes ledger. New grants are in minutes, on the Account tab.
        </p>
        {data.adjustments.length === 0 ? (
          <p className="text-xs text-[var(--color-text-muted)]">No USD grants or adjustments recorded.</p>
        ) : (
          <ul className="space-y-1">
            {data.adjustments.map((adj) => {
              // Storage convention: negative = credit, positive = charge.
              const isCredit = adj.amountUsd < 0;
              const magnitude = Math.abs(adj.amountUsd);
              return (
                <li
                  key={adj.id}
                  className="flex items-center justify-between p-2 rounded border border-[var(--color-border)] text-xs"
                >
                  <div className="min-w-0">
                    <div className="font-mono text-[var(--color-text-muted)]">
                      {adj.date} · {timeAgo(adj.createdAt)}
                    </div>
                    <div className="truncate max-w-[300px] text-[var(--color-text)]">{adj.reason}</div>
                  </div>
                  <span
                    className="font-mono font-bold"
                    style={{ color: isCredit ? 'var(--color-success)' : 'var(--color-warning)' }}
                    title={`Stored as ${formatCost(adj.amountUsd)} (negative = credit)`}
                  >
                    {isCredit ? `Credit ${formatCost(magnitude)}` : `Charge ${formatCost(magnitude)}`}
                  </span>
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </div>
  );
}

function StatTile({ label, value, highlight = false }: { label: string; value: string; highlight?: boolean }) {
  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-dim)] p-3">
      <div className="text-[10px] uppercase tracking-wide text-[var(--color-text-faint)]">{label}</div>
      <div
        className={`mt-1 font-mono text-sm font-semibold ${highlight ? 'text-[var(--color-primary)]' : 'text-[var(--color-text)]'}`}
      >
        {value}
      </div>
    </div>
  );
}
