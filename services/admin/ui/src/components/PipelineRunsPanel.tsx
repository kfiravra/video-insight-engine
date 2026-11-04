import { useUsageByRun } from '../hooks/use-admin-api';
import { Panel } from './Panel';
import { SkeletonPanel } from './SkeletonPanel';
import { ErrorState } from './ErrorState';
import { RunsTable } from './RunsTable';

interface PipelineRunsPanelProps {
  days?: number;
  limit?: number;
}

/** The summarizer's pipeline attempts in the window, newest first (D10). */
export function PipelineRunsPanel({ days = 30, limit = 20 }: PipelineRunsPanelProps) {
  const { data, isLoading, isError, error, refetch } = useUsageByRun(days, limit);

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
        <RunsTable runs={data} showVideo />
      )}
    </Panel>
  );
}
