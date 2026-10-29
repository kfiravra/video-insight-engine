import { useUsageDuplicates } from '../hooks/use-admin-api';
import { Panel } from './Panel';
import { SkeletonPanel } from './SkeletonPanel';
import { ErrorState } from './ErrorState';
import { InfoTip } from './InfoTip';
import { formatCost } from '../lib/format';

interface DuplicatesPanelProps {
  days?: number;
}

export function DuplicatesPanel({ days = 7 }: DuplicatesPanelProps = {}) {
  const { data, isLoading, isError, error, refetch } = useUsageDuplicates(days);

  if (isError) {
    return <ErrorState error={error} onRetry={() => refetch()} title="Failed to load duplicate prompts" />;
  }
  if (isLoading || !data) {
    return <SkeletonPanel size="lg" />;
  }

  const header = (
    <span className="inline-flex items-center gap-1">
      <span>Repeated prompts</span>
      <InfoTip label="Repeated prompts explanation">
        Identical prompts (same hash) sent 3+ times in the last {days} days. Repeats are
        cache-miss candidates — each row is money spent re-asking the same question.
      </InfoTip>
    </span>
  );

  return (
    <Panel title={header} tone="raised" padding="none" data-testid="duplicates-panel">
      {data.length === 0 ? (
        <p className="text-sm text-[var(--color-text-muted)] p-4">No repeated prompts</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-[var(--color-border)]">
                <th className="text-left p-2 pl-4 font-medium text-[var(--color-text-muted)]">Prompt</th>
                <th className="text-left p-2 font-medium text-[var(--color-text-muted)]">Feature</th>
                <th className="text-left p-2 font-medium text-[var(--color-text-muted)]">Model</th>
                <th className="text-right p-2 font-medium text-[var(--color-text-muted)]">Repeats</th>
                <th className="text-right p-2 pr-4 font-medium text-[var(--color-text-muted)]">Total cost</th>
              </tr>
            </thead>
            <tbody>
              {data.map((row) => (
                <tr key={row.prompt_hash} className="border-b border-[var(--color-border)] last:border-0">
                  {/* "" is what the assistant stores (chat prompts are never kept), null what the 90-day step leaves. */}
                  <td className="p-2 pl-4 truncate max-w-[320px]" title={row.prompt_preview || undefined}>
                    {row.prompt_preview || row.prompt_hash}
                  </td>
                  <td className="p-2 truncate max-w-[140px]">{row.feature ?? '—'}</td>
                  <td className="p-2 font-mono truncate max-w-[160px]">{row.model ?? '—'}</td>
                  <td className="p-2 text-right tabular-nums">{row.count}</td>
                  <td className="p-2 pr-4 text-right font-mono font-medium">{formatCost(row.total_cost_usd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}
