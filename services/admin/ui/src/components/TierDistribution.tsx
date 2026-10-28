import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, LabelList } from 'recharts';
import { useTierDistribution } from '../hooks/use-admin-api';
import { Panel } from './Panel';
import { SkeletonPanel } from './SkeletonPanel';
import { ErrorState } from './ErrorState';

export function TierDistribution() {
  const { data, isLoading, isError, error, refetch } = useTierDistribution();

  if (isError) {
    return <ErrorState error={error} onRetry={() => refetch()} title="Failed to load plan distribution" />;
  }

  if (isLoading || !data) {
    return <SkeletonPanel size="lg" />;
  }

  if (data.length === 0) {
    return (
      <Panel title="User Plans" tone="raised" padding="md">
        <div className="h-40 flex items-center justify-center">
          <p className="text-sm text-[var(--color-text-faint)]">No plan data available</p>
        </div>
      </Panel>
    );
  }

  const chartData = data.map((d) => ({
    ...d,
    label: `${d.tier} (${d.percentage}%)`,
  }));

  return (
    <Panel title="User Plans" tone="raised" padding="md" className="tier-distribution">
      <div data-testid="tier-distribution">
        <ResponsiveContainer width="100%" height={180}>
          <BarChart data={chartData} layout="vertical" margin={{ left: 10, right: 40 }}>
            <XAxis type="number" hide />
            <YAxis
              type="category"
              dataKey="tier"
              width={50}
              tick={{ fontSize: 12, fill: 'var(--color-text-muted)' }}
              axisLine={false}
              tickLine={false}
            />
            <Tooltip
              contentStyle={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)', borderRadius: 8, fontSize: 12 }}
              formatter={(v: number | undefined) => [`${(v ?? 0).toLocaleString()} users`, 'Count']}
            />
            <Bar dataKey="count" radius={[0, 4, 4, 0]} maxBarSize={24} fill="var(--chart-1)">
              <LabelList
                dataKey="count"
                position="right"
                style={{ fontSize: 11, fill: 'var(--color-text-muted)' }}
              />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </Panel>
  );
}
