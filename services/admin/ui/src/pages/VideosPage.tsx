import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useUsageByVideo, useVideoDetail } from '../hooks/use-admin-api';
import type { VideoSummaryItem } from '../lib/api';
import { formatCost, formatDuration, timeAgo, formatNumber, formatDateTime } from '../lib/format';
import { DollarIcon, ZapIcon, VideoPlayIcon, ChevronRightIcon } from '../components/icons';
import { Panel } from '../components/Panel';
import { SkeletonPanel } from '../components/SkeletonPanel';
import { ErrorState } from '../components/ErrorState';
import { StatCard } from '../components/StatCard';
import { SortableHeader } from '../components/SortableHeader';
import { useUrlSort } from '../hooks/use-sort';
import { RunCallsTable } from '../components/RunsTable';

function ChevronIcon({ open }: { open: boolean }) {
  return (
    <span
      className="inline-flex transition-transform duration-200"
      style={{ transform: open ? 'rotate(90deg)' : 'rotate(0deg)' }}
    >
      <ChevronRightIcon />
    </span>
  );
}

function StatusDot({ status }: { status: string | null }) {
  const color =
    status === 'completed' ? 'var(--color-success)' :
    status === 'processing' ? 'var(--color-warning)' :
    status === 'error' ? 'var(--color-danger)' :
    'var(--color-text-muted)';
  return (
    <span
      className="inline-block w-2 h-2 rounded-full"
      style={{ background: color }}
      title={status ?? 'unknown'}
    />
  );
}

function SummaryStats({ data }: { data: Array<{ calls: number; cost_usd: number }> }) {
  const totalVideos = data.length;
  const totalCost = data.reduce((sum, v) => sum + v.cost_usd, 0);
  const totalCalls = data.reduce((sum, v) => sum + v.calls, 0);

  return (
    <div className="grid grid-cols-3 gap-3">
      <StatCard
        label="Videos"
        value={formatNumber(totalVideos)}
        tone="accent"
        icon={<VideoPlayIcon />}
      />
      <StatCard
        label="Total Spend"
        value={formatCost(totalCost)}
        tone="primary"
        icon={<DollarIcon />}
      />
      <StatCard
        label="Total Calls"
        value={formatNumber(totalCalls)}
        tone="success"
        icon={<ZapIcon />}
      />
    </div>
  );
}

// The drawer's call list: the newest pipeline calls (the video detail page has every run).
const DRAWER_CALLS_LIMIT = 50;

/** Inline expanded call details fetched on demand */
function VideoCallsPanel({ videoId }: { videoId: string }) {
  const { data, isLoading, isError, error, refetch } = useVideoDetail(videoId);

  if (isError) {
    return (
      <div className="px-4 py-3">
        <ErrorState error={error} onRetry={() => refetch()} title="Failed to load call details" compact />
      </div>
    );
  }

  if (isLoading || !data) {
    return (
      <div className="px-4 py-3">
        <div className="flex gap-2">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="h-4 w-24 rounded bg-[var(--color-border)] animate-pulse" />
          ))}
        </div>
      </div>
    );
  }

  const { runs, by_feature } = data;
  // The pills cover every service (chat included); the list is the pipeline runs' calls
  // (summarizer only, last 90 days), newest first, capped like the old flat list.
  const calls = runs
    .flatMap((run) => run.calls)
    .sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime())
    .slice(0, DRAWER_CALLS_LIMIT);

  if (calls.length === 0 && by_feature.length === 0) {
    return (
      <div className="px-4 py-3 text-xs text-[var(--color-text-muted)]">
        No individual calls recorded.
      </div>
    );
  }

  return (
    <div className="px-4 pt-1 pb-3 space-y-3">
      {/* Feature summary pills */}
      {by_feature.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {by_feature.map((f) => (
            <span
              key={f.feature}
              className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[10px] font-medium bg-[var(--color-primary-soft)] text-[var(--color-primary)]"
            >
              {f.feature}
              <span className="text-[var(--color-text-faint)]">{f.calls} calls</span>
              <span className="font-bold">{formatCost(f.cost_usd)}</span>
            </span>
          ))}
        </div>
      )}

      {calls.length > 0 ? (
        <RunCallsTable calls={calls} />
      ) : (
        <div className="text-xs text-[var(--color-text-muted)]">No pipeline calls in the last 90 days.</div>
      )}
    </div>
  );
}

type SortKey = 'title' | 'cost_usd' | 'calls' | 'tokens' | 'last_call';

function compareVideos(a: VideoSummaryItem, b: VideoSummaryItem, key: SortKey): number {
  switch (key) {
    case 'title': {
      const at = (a.title ?? a.video_id ?? '').toLowerCase();
      const bt = (b.title ?? b.video_id ?? '').toLowerCase();
      return at.localeCompare(bt);
    }
    case 'cost_usd':
      return (a.cost_usd ?? 0) - (b.cost_usd ?? 0);
    case 'calls':
      return (a.calls ?? 0) - (b.calls ?? 0);
    case 'tokens':
      return (a.tokens_in + a.tokens_out) - (b.tokens_in + b.tokens_out);
    case 'last_call': {
      const ad = a.last_call ? new Date(a.last_call).getTime() : 0;
      const bd = b.last_call ? new Date(b.last_call).getTime() : 0;
      return ad - bd;
    }
    default:
      return 0;
  }
}

function isSortKey(value: string | null): value is SortKey {
  return value === 'title' || value === 'cost_usd' || value === 'calls' || value === 'tokens' || value === 'last_call';
}

interface VideosPageProps {
  days?: number;
}

export function VideosPage({ days = 30 }: VideosPageProps) {
  const { data, isLoading, isError, error, refetch } = useUsageByVideo(days, 50);
  const { sort, toggleSort } = useUrlSort({ key: 'cost_usd', direction: 'desc' });
  const [filter, setFilter] = useState('');
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const toggle = (videoId: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(videoId)) next.delete(videoId);
      else next.add(videoId);
      return next;
    });
  };

  const visible = useMemo(() => {
    if (!data) return [];
    const needle = filter.trim().toLowerCase();
    const filtered = needle
      ? data.filter((v) => (v.title ?? v.video_id).toLowerCase().includes(needle))
      : data;
    const key: SortKey = isSortKey(sort.key) ? sort.key : 'cost_usd';
    const sign = sort.direction === 'desc' ? -1 : 1;
    const sorted = [...filtered].sort((a, b) => compareVideos(a, b, key) * sign);
    return sorted;
  }, [data, filter, sort.key, sort.direction]);

  if (isError) {
    return (
      <div className="space-y-4">
        <h2 className="text-lg font-bold">Videos</h2>
        <ErrorState error={error} onRetry={() => refetch()} title="Failed to load videos" />
      </div>
    );
  }

  if (isLoading || !data) {
    return (
      <div className="space-y-3">
        <h2 className="text-lg font-bold">Videos</h2>
        <SkeletonPanel size="sm" count={6} />
      </div>
    );
  }

  if (data.length === 0) {
    return (
      <div className="space-y-4">
        <h2 className="text-lg font-bold">Videos</h2>
        <Panel tone="dim" padding="lg">
          <div className="flex flex-col items-center justify-center py-8">
            <div className="w-12 h-12 rounded-full bg-[var(--color-primary-soft)] flex items-center justify-center mb-3">
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="var(--color-primary)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <polygon points="23 7 16 12 23 17 23 7" /><rect x="1" y="5" width="15" height="14" rx="2" ry="2" />
              </svg>
            </div>
            <p className="text-sm font-medium text-[var(--color-text)]">No videos processed yet</p>
            <p className="text-xs text-[var(--color-text-muted)] mt-1">Videos will appear here once they are summarized or explained.</p>
          </div>
        </Panel>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-bold">Videos</h2>
        <span className="text-xs text-[var(--color-text-muted)]">
          {visible.length} of {data.length} videos (last {days} days)
        </span>
      </div>

      <SummaryStats data={data} />

      <div className="flex items-center gap-2">
        <input
          type="search"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Filter by title..."
          aria-label="Filter videos by title"
          data-testid="videos-filter"
          className="flex-1 px-3 py-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] text-sm text-[var(--color-text)] placeholder:text-[var(--color-text-faint)] outline-none focus:ring-2 focus:ring-[var(--color-primary)]"
        />
      </div>

      <Panel tone="raised" padding="none">
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-[var(--color-border)] bg-[var(--color-surface-dim)]">
                <th className="w-8 p-3" />
                <th className="text-left p-3">
                  <SortableHeader
                    sortKey="title"
                    currentKey={sort.key}
                    direction={sort.direction}
                    onToggle={toggleSort}
                  >
                    Video
                  </SortableHeader>
                </th>
                <th className="text-right p-3">
                  <SortableHeader
                    sortKey="calls"
                    currentKey={sort.key}
                    direction={sort.direction}
                    onToggle={toggleSort}
                    align="right"
                  >
                    Calls
                  </SortableHeader>
                </th>
                <th className="text-right p-3">
                  <SortableHeader
                    sortKey="cost_usd"
                    currentKey={sort.key}
                    direction={sort.direction}
                    onToggle={toggleSort}
                    align="right"
                  >
                    Cost
                  </SortableHeader>
                </th>
                <th className="text-right p-3 hidden md:table-cell">
                  <SortableHeader
                    sortKey="tokens"
                    currentKey={sort.key}
                    direction={sort.direction}
                    onToggle={toggleSort}
                    align="right"
                  >
                    Tokens
                  </SortableHeader>
                </th>
                <th className="text-center p-3 font-medium text-[var(--color-text-muted)] hidden sm:table-cell">Status</th>
                <th className="text-right p-3 pr-4 hidden lg:table-cell">
                  <SortableHeader
                    sortKey="last_call"
                    currentKey={sort.key}
                    direction={sort.direction}
                    onToggle={toggleSort}
                    align="right"
                  >
                    Last Active
                  </SortableHeader>
                </th>
              </tr>
            </thead>
            <tbody>
              {visible.length === 0 ? (
                <tr>
                  <td colSpan={7} className="p-6 text-center text-xs text-[var(--color-text-muted)]">
                    No videos match &ldquo;{filter}&rdquo;.
                  </td>
                </tr>
              ) : (
                visible.map((v) => {
                  const isOpen = expanded.has(v.video_id);
                  return (
                    <VideoRow
                      key={v.video_id}
                      video={v}
                      isOpen={isOpen}
                      onToggle={() => toggle(v.video_id)}
                    />
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  );
}

interface VideoRowProps {
  video: VideoSummaryItem;
  isOpen: boolean;
  onToggle: () => void;
}

function VideoRow({ video: v, isOpen, onToggle }: VideoRowProps) {
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
            onClick={onToggle}
            data-testid={`expand-${v.video_id}`}
            className="flex items-center justify-center w-6 h-6 rounded-md hover:bg-[var(--color-surface-dim)] text-[var(--color-text-muted)] transition-colors"
            aria-label={isOpen ? 'Collapse' : 'Expand'}
          >
            <ChevronIcon open={isOpen} />
          </button>
        </td>

        {/* Video info */}
        <td className="p-3">
          <Link to={`/videos/${v.video_id}`} className="flex items-center gap-3 min-w-0">
            {v.thumbnail_url ? (
              <img
                src={v.thumbnail_url}
                alt=""
                className="w-20 h-[45px] rounded object-cover flex-shrink-0 bg-[var(--color-border)]"
              />
            ) : (
              <div className="w-20 h-[45px] rounded bg-[var(--color-surface-dim)] flex-shrink-0 flex items-center justify-center">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--color-text-faint)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <polygon points="5 3 19 12 5 21 5 3" />
                </svg>
              </div>
            )}
            <div className="min-w-0">
              <p className="font-medium truncate max-w-[300px] text-[var(--color-text)] hover:text-[var(--color-primary)] transition-colors">
                {v.title ?? v.video_id}
              </p>
              <p className="text-[var(--color-text-muted)] truncate max-w-[200px]">
                {v.channel ?? '—'}
                {v.duration != null && <span className="ml-2">{formatDuration(v.duration)}</span>}
              </p>
            </div>
          </Link>
        </td>

        <td className="p-3 text-right font-mono">{formatNumber(v.calls)}</td>
        <td className="p-3 text-right font-mono font-bold text-[var(--color-primary)]">
          {formatCost(v.cost_usd)}
        </td>
        <td className="p-3 text-right hidden md:table-cell">
          {formatNumber(v.tokens_in + v.tokens_out)}
        </td>
        <td className="p-3 text-center hidden sm:table-cell">
          <StatusDot status={v.status} />
        </td>
        <td
          className="p-3 pr-4 text-right text-[var(--color-text-muted)] hidden lg:table-cell"
          title={formatDateTime(v.last_call)}
        >
          {timeAgo(v.last_call)}
        </td>
      </tr>

      {/* Expanded calls panel */}
      {isOpen && (
        <tr style={{ borderLeft: '3px solid var(--color-primary)' }}>
          <td colSpan={7} className="bg-[var(--color-surface)] border-b border-[var(--color-border)]" data-testid={`calls-${v.video_id}`}>
            <VideoCallsPanel videoId={v.video_id} />
          </td>
        </tr>
      )}
    </>
  );
}
