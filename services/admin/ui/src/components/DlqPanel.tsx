import { useState } from 'react';
import { useQueueDlq, useQueueStats, useReplayDlq } from '../hooks/use-admin-api';
import type { DlqMessage } from '../lib/api';
import { formatNumber } from '../lib/format';
import { Panel } from './Panel';
import { SkeletonPanel } from './SkeletonPanel';
import { ErrorState } from './ErrorState';
import { InfoTip } from './InfoTip';

/** vie-api caps a single replay call at 500 messages (POST /queue/replay `max`). */
const REPLAY_BATCH_MAX = 500;

function field(msg: DlqMessage, key: string): string {
  const value = msg.payload?.[key];
  return value == null ? '—' : String(value);
}

/** What paid for the run (`free` / `paid`); a message published before billing-roles P3.4 carries `tier`. */
function plan(msg: DlqMessage): string {
  const value = msg.payload?.plan ?? msg.payload?.tier;
  return value == null ? '—' : String(value);
}

function attempt(msg: DlqMessage): string {
  const value = msg.headers?.['x-attempt'] ?? msg.payload?.attempt;
  return value == null ? '—' : String(value);
}

interface ReplayControlsProps {
  /** True DLQ depth from /queue/stats — the peek below is capped by `limit`. */
  depth: number;
  replay: ReturnType<typeof useReplayDlq>;
}

function ReplayControls({ depth, replay }: ReplayControlsProps) {
  const [confirming, setConfirming] = useState(false);
  const batch = Math.min(depth, REPLAY_BATCH_MAX);
  const label = depth > REPLAY_BATCH_MAX ? `Replay ${batch} of ${formatNumber(depth)}` : `Replay ${batch}`;

  if (!confirming) {
    return (
      <button
        type="button"
        className="px-2.5 py-1 text-xs rounded-md font-medium border border-[var(--color-border)] hover:bg-[var(--color-surface-dim)]"
        onClick={() => setConfirming(true)}
      >
        Replay all…
      </button>
    );
  }

  return (
    <>
      <button
        type="button"
        className="px-2.5 py-1 text-xs rounded-md font-medium bg-[var(--color-danger)] text-white disabled:opacity-50"
        disabled={replay.isPending}
        onClick={() => replay.mutate(batch, { onSettled: () => setConfirming(false) })}
      >
        {replay.isPending ? 'Replaying…' : label}
      </button>
      <button
        type="button"
        className="px-2.5 py-1 text-xs rounded-md text-[var(--color-text-muted)] hover:text-[var(--color-text)] disabled:opacity-50"
        disabled={replay.isPending}
        onClick={() => setConfirming(false)}
      >
        Cancel
      </button>
    </>
  );
}

function DlqTable({ messages }: { messages: DlqMessage[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-[var(--color-border)]">
            <th className="text-left p-2 pl-4 font-medium text-[var(--color-text-muted)]">Video</th>
            <th className="text-left p-2 font-medium text-[var(--color-text-muted)]">Summary id</th>
            <th className="text-left p-2 font-medium text-[var(--color-text-muted)]">Request id</th>
            <th className="text-right p-2 font-medium text-[var(--color-text-muted)]">Plan</th>
            <th className="text-right p-2 pr-4 font-medium text-[var(--color-text-muted)]">Attempt</th>
          </tr>
        </thead>
        <tbody>
          {messages.map((msg, i) => (
            <tr
              key={`${msg.messageId ?? 'msg'}-${i}`}
              className="border-b border-[var(--color-border)] last:border-0"
              data-testid="dlq-row"
            >
              <td className="p-2 pl-4 font-mono">{field(msg, 'youtubeId')}</td>
              <td className="p-2 font-mono truncate max-w-[200px]">{field(msg, 'videoSummaryId')}</td>
              <td className="p-2 font-mono truncate max-w-[200px]">{field(msg, 'requestId')}</td>
              <td className="p-2 text-right">{plan(msg)}</td>
              <td className="p-2 pr-4 text-right tabular-nums">{attempt(msg)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

interface DlqPanelProps {
  limit?: number;
}

export function DlqPanel({ limit = 20 }: DlqPanelProps = {}) {
  const { data, isLoading, isError, error, refetch } = useQueueDlq(limit);
  const { data: stats } = useQueueStats();
  const replay = useReplayDlq();

  if (isError) {
    return <ErrorState error={error} onRetry={() => refetch()} title="DLQ unavailable" />;
  }

  if (isLoading || !data) {
    return <SkeletonPanel size="md" />;
  }

  const messages = data.messages;
  // The peek is capped by `limit`; the counter knows how many are really queued.
  const depth = Math.max(stats?.dlq.messages ?? 0, messages.length);
  const header = (
    <span className="inline-flex items-center gap-1">
      <span>Dead-letter queue</span>
      <InfoTip label="DLQ explanation">
        Jobs that exhausted their retries. Replay re-publishes them to the main queue
        with the attempt counter reset — fix the underlying cause first, or they come
        straight back.
      </InfoTip>
    </span>
  );
  const actions = messages.length === 0 ? undefined : <ReplayControls depth={depth} replay={replay} />;

  return (
    <Panel title={header} actions={actions} tone="dim" padding="none" data-testid="dlq-panel">
      {replay.isError && (
        <p className="px-4 pt-3 text-xs text-[var(--color-danger)]" role="alert">
          Replay failed: {replay.error instanceof Error ? replay.error.message : 'unknown error'}
        </p>
      )}
      {replay.isSuccess && (
        <p className="px-4 pt-3 text-xs text-[var(--color-success)]" role="status">
          Replayed {replay.data.replayed} message{replay.data.replayed === 1 ? '' : 's'}.
        </p>
      )}
      {messages.length === 0 ? (
        <p className="text-sm text-[var(--color-text-muted)] p-4">DLQ is empty</p>
      ) : (
        <DlqTable messages={messages} />
      )}
    </Panel>
  );
}
