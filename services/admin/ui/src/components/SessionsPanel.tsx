import { useUsageBySession } from '../hooks/use-admin-api';
import { formatCost, formatDateTime, formatNumber, shortId, timeAgo } from '../lib/format';
import { ErrorState } from './ErrorState';
import { LangfuseLink } from './LangfuseLink';
import { Panel } from './Panel';
import { SkeletonPanel } from './SkeletonPanel';
import { TonePill } from './TonePill';
import type { SessionSummary } from '../lib/api';

const FROM_SM = 'hidden sm:table-cell';
const FROM_MD = 'hidden md:table-cell';
const HEAD_CELL = 'p-3 font-medium text-[var(--color-text-muted)] whitespace-nowrap';

interface SessionSurfaceProps {
  session: SessionSummary;
}

/** Which chat the session is: a video's (it has a summary id) or the library's. The legacy bucket mixes both. */
function SessionSurface({ session }: SessionSurfaceProps) {
  if (session.session_id === null) return <span className="text-[var(--color-text-faint)]">—</span>;
  return session.video_summary_id ? (
    <TonePill tone="primary" title={`Video chat, summary ${session.video_summary_id}`}>
      video
    </TonePill>
  ) : (
    <TonePill tone="neutral">library</TonePill>
  );
}

interface SessionRowProps {
  session: SessionSummary;
}

function SessionRow({ session }: SessionRowProps) {
  return (
    <tr className="border-b border-[var(--color-border)] last:border-0 hover:bg-[var(--color-surface-dim)] transition-colors">
      <td className="p-3 pl-4">
        {session.session_id === null ? (
          <span className="text-[var(--color-text-muted)] italic text-[11px]">unattributed (legacy)</span>
        ) : (
          <span className="font-mono text-[11px] text-[var(--color-text)]" title={`Session ${session.session_id}`}>
            {shortId(session.session_id)}
          </span>
        )}
      </td>
      <td className={`p-3 ${FROM_SM}`}>
        <SessionSurface session={session} />
      </td>
      <td className="p-3 text-right text-[11px]">{formatNumber(session.turns)}</td>
      <td className={`p-3 text-right text-[11px] text-[var(--color-text-muted)] ${FROM_MD}`}>
        {formatNumber(session.tool_llm_calls)}
      </td>
      <td className={`p-3 text-right text-[11px] text-[var(--color-text-muted)] ${FROM_SM}`}>
        {formatNumber(session.call_count)}
      </td>
      <td className="p-3 text-right font-mono font-bold text-[var(--color-primary)]">
        {formatCost(session.total_cost_usd)}
      </td>
      <td
        className={`p-3 text-right text-[11px] text-[var(--color-text-muted)] whitespace-nowrap ${FROM_SM}`}
        title={formatDateTime(session.last_call)}
      >
        {timeAgo(session.last_call)}
      </td>
      <td className="p-3 pr-4 text-right">
        <LangfuseLink href={session.langfuse_url} label="Session" description="Open session in Langfuse" />
      </td>
    </tr>
  );
}

/** A session's row key; the one legacy bucket falls back to its position. */
function sessionKey(session: SessionSummary, index: number): string {
  return session.session_id ?? `unattributed-${index}`;
}

interface SessionsPanelProps {
  days?: number;
  limit?: number;
}

/** Assistant usage per chat session, newest activity first (D10). */
export function SessionsPanel({ days = 30, limit = 20 }: SessionsPanelProps) {
  const { data, isLoading, isError, error, refetch } = useUsageBySession(days, limit);

  if (isError) {
    return <ErrorState error={error} onRetry={() => refetch()} title="Failed to load chat sessions" />;
  }

  if (isLoading || !data) {
    return <SkeletonPanel size="lg" />;
  }

  return (
    <Panel title="Chat Sessions" tone="raised" padding="none">
      {data.length === 0 ? (
        <div className="p-6 text-center text-xs text-[var(--color-text-muted)]">
          No chat sessions found in the last {days} days.
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-[var(--color-border)] bg-[var(--color-surface-dim)]">
                <th className={`text-left ${HEAD_CELL} pl-4`}>Session</th>
                <th className={`text-left ${HEAD_CELL} ${FROM_SM}`}>Surface</th>
                <th className={`text-right ${HEAD_CELL}`}>Turns</th>
                <th className={`text-right ${HEAD_CELL} ${FROM_MD}`}>Tool LLM calls</th>
                <th className={`text-right ${HEAD_CELL} ${FROM_SM}`}>Calls</th>
                <th className={`text-right ${HEAD_CELL}`}>Cost</th>
                <th className={`text-right ${HEAD_CELL} ${FROM_SM}`}>Last activity</th>
                <th className={`text-right ${HEAD_CELL} pr-4`}>Langfuse</th>
              </tr>
            </thead>
            <tbody>
              {data.map((session, i) => (
                <SessionRow key={sessionKey(session, i)} session={session} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}
