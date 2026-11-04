import { Link, useParams } from 'react-router-dom';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, LabelList } from 'recharts';
import { useVideoDetail } from '../hooks/use-admin-api';
import { formatCost, formatDuration, timeAgo, formatNumber } from '../lib/format';
import { ArrowLeftIcon, DollarIcon, ZapIcon, HashIcon, ClockIcon } from '../components/icons';
import { Panel } from '../components/Panel';
import { StatCard } from '../components/StatCard';
import { SkeletonPanel } from '../components/SkeletonPanel';
import { ErrorState } from '../components/ErrorState';
import { PurgeVideoControls } from '../components/PurgeVideoControls';
import { RunsTable } from '../components/RunsTable';

function BackLink() {
  return (
    <Link
      to="/videos"
      className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-[var(--color-primary)] bg-[var(--color-primary-soft)] hover:bg-[var(--color-primary)]/15 transition-colors"
    >
      <ArrowLeftIcon /> Back to Videos
    </Link>
  );
}

export function VideoDetailPage() {
  const { videoId } = useParams<{ videoId: string }>();
  const { data, isLoading, isError, error, refetch } = useVideoDetail(videoId);

  if (isError) {
    return (
      <div className="space-y-4">
        <BackLink />
        <ErrorState error={error} onRetry={() => refetch()} title="Failed to load video detail" />
      </div>
    );
  }

  if (isLoading || !data) {
    return (
      <div className="space-y-4">
        <BackLink />
        <SkeletonPanel size="lg" />
        <SkeletonPanel size="xl" />
      </div>
    );
  }

  const { video, summary, by_feature, runs } = data;

  return (
    <div className="space-y-6">
      <BackLink />

      {/* Video header */}
      <Panel tone="raised" padding="lg">
        <div className="flex flex-col sm:flex-row items-start gap-4">
          {video?.thumbnail_url ? (
            <img
              src={video.thumbnail_url}
              alt=""
              className="w-44 h-[100px] rounded-lg object-cover flex-shrink-0 bg-[var(--color-border)]"
            />
          ) : (
            <div className="w-44 h-[100px] rounded-lg bg-[var(--color-surface-dim)] flex-shrink-0 flex items-center justify-center">
              <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="var(--color-text-faint)" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <polygon points="5 3 19 12 5 21 5 3" />
              </svg>
            </div>
          )}
          <div className="min-w-0 flex-1">
            <h2 className="text-lg font-bold truncate">{video?.title ?? videoId}</h2>
            <p className="text-sm text-[var(--color-text-muted)] mt-0.5">
              {video?.channel ?? '—'}
              {video?.duration != null && <span className="ml-3 text-[var(--color-text-faint)]">{formatDuration(video.duration)}</span>}
            </p>
            <div className="flex items-center gap-2 mt-3 flex-wrap">
              {video?.category && (
                <span className="px-2.5 py-1 rounded-full text-[11px] font-medium bg-[var(--color-primary-soft)] text-[var(--color-primary)]">
                  {video.category}
                </span>
              )}
              {video?.status && (
                <span className={`px-2.5 py-1 rounded-full text-[11px] font-medium ${
                  video.status === 'completed'
                    ? 'bg-[var(--color-success-soft)] text-[var(--color-success)]'
                    : video.status === 'error'
                      ? 'bg-[var(--color-danger-soft)] text-[var(--color-danger)]'
                      : 'bg-[var(--color-warning-soft)] text-[var(--color-warning)]'
                }`}>
                  {video.status}
                </span>
              )}
              {video?.processed_at && (
                <span className="text-[11px] text-[var(--color-text-faint)]">
                  Processed {timeAgo(video.processed_at)}
                </span>
              )}
            </div>
          </div>
          {videoId && <PurgeVideoControls videoId={videoId} />}
        </div>
      </Panel>

      {/* Stats row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard
          label="Total Cost"
          value={formatCost(summary.total_cost_usd)}
          tone="primary"
          icon={<DollarIcon />}
        />
        <StatCard
          label="LLM Calls"
          value={formatNumber(summary.total_calls)}
          tone="success"
          icon={<ZapIcon />}
        />
        <StatCard
          label="Total Tokens"
          value={formatNumber(summary.total_tokens_in + summary.total_tokens_out)}
          tone="warning"
          icon={<HashIcon />}
        />
        <StatCard
          label="Avg Duration"
          value={`${Math.round(summary.avg_duration_ms)}ms`}
          tone="neutral"
          icon={<ClockIcon />}
        />
      </div>

      {/* Feature breakdown chart */}
      {by_feature.length > 0 && (
        <Panel title="Cost by Feature" tone="raised" padding="md">
          <ResponsiveContainer width="100%" height={Math.max(140, by_feature.length * 56)}>
            <BarChart data={by_feature} layout="vertical" margin={{ right: 40 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" horizontal={false} />
              <XAxis type="number" tick={{ fontSize: 11, fill: 'var(--color-text-muted)' }} tickFormatter={(v: number) => `$${v.toFixed(2)}`} />
              <YAxis type="category" dataKey="feature" tick={{ fontSize: 11, fill: 'var(--color-text-muted)' }} width={140} />
              <Tooltip
                contentStyle={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)', borderRadius: 8, fontSize: 12 }}
                formatter={(v: number | undefined) => [`$${(v ?? 0).toFixed(4)}`, 'Cost']}
              />
              <Bar dataKey="cost_usd" fill="var(--color-primary)" radius={[0, 6, 6, 0]}>
                <LabelList
                  dataKey="cost_usd"
                  position="right"
                  formatter={(v) => `$${Number(v ?? 0).toFixed(4)}`}
                  style={{ fontSize: 10, fill: 'var(--color-text-muted)' }}
                />
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </Panel>
      )}

      {/* The video's pipeline attempts, newest first, each expandable to its calls */}
      <Panel title={`Pipeline Runs (${runs.length})`} tone="raised" padding="none">
        {runs.length === 0 ? (
          <div className="p-6 text-center text-xs text-[var(--color-text-muted)]">
            No pipeline runs recorded for this video in the last 90 days.
          </div>
        ) : (
          <RunsTable runs={runs} />
        )}
      </Panel>
    </div>
  );
}
