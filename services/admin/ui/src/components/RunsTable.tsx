import { Fragment, useState } from 'react';
import { Link } from 'react-router-dom';
import { formatCost, formatDateTime, formatUsageVolume, shortId, timeAgo } from '../lib/format';
import { LangfuseLink } from './LangfuseLink';
import { TonePill } from './TonePill';
import type { RunCallSummary, RunStatus, RunSummary } from '../lib/api';
import type { PillTone } from './TonePill';

// Columns that give way on narrow screens; the id, status, cost and links always show.
const FROM_SM = 'hidden sm:table-cell';
const FROM_MD = 'hidden md:table-cell';
const FROM_LG = 'hidden lg:table-cell';
// The columns every run table has; the Usage page adds a Video column.
const BASE_COLUMN_COUNT = 11;

// Phones get 6px side padding so the id, status, cost and links fit 375px without scrolling.
const CELL = 'px-1.5 py-3 sm:px-3';
const HEAD_CELL = `${CELL} font-medium text-[var(--color-text-muted)] whitespace-nowrap`;

const STATUS_TONE: Record<RunStatus, PillTone> = {
  completed: 'success',
  failed: 'danger',
  running: 'warning',
  dispatched: 'warning',
  cache_hit: 'neutral',
};

const DEGRADED_REASON_TEXT: Record<string, { label: string; detail: string }> = {
  partial_extraction: {
    label: 'partial',
    detail:
      'an extraction field group failed, a chunked batch was dropped, or coverage was critical — the stored output is a partial result',
  },
  media_download_failed: {
    label: 'media',
    detail:
      'a video download failed (403, bot check, timeout…) or the hi-res pass was abandoned — frames, hi-res frames or moment images may be missing',
  },
};

interface RegenBadgeProps {
  ordinal: number;
}

function RegenBadge({ ordinal }: RegenBadgeProps) {
  return (
    <TonePill tone="warning" title={`Regeneration #${ordinal}`}>
      regen #{ordinal}
    </TonePill>
  );
}

/** A degraded run's reasons as label + explanation; rows from before the summarizer wrote
 *  reasons had one possible cause, a partial extraction. */
function degradedReasons(reasons?: string[] | null): { label: string; detail: string }[] {
  const known = (reasons ?? []).map((r) => DEGRADED_REASON_TEXT[r] ?? { label: r, detail: r });
  return known.length ? known : [DEGRADED_REASON_TEXT.partial_extraction];
}

interface DegradedBadgeProps {
  reasons?: string[] | null;
}

/** Degraded-run marker: a partial extraction (the flag the web app's
 *  DegradedNotice shows) and/or a failed frame download (admin only). */
function DegradedBadge({ reasons }: DegradedBadgeProps) {
  const labels = (reasons ?? []).map((r) => (DEGRADED_REASON_TEXT[r] ?? { label: r }).label).join(' · ');
  const title = `Degraded run: ${degradedReasons(reasons).map((r) => r.detail).join('; ')}`;
  return (
    <TonePill tone="danger" title={title}>
      {labels ? `degraded · ${labels}` : 'degraded'}
    </TonePill>
  );
}

interface RunStatusBadgeProps {
  status: string | null;
}

/** The attempt's lifecycle state; a value this UI does not know yet renders neutral. */
function RunStatusBadge({ status }: RunStatusBadgeProps) {
  if (!status) return <span className="text-[var(--color-text-faint)]">—</span>;
  const tone = Object.hasOwn(STATUS_TONE, status) ? STATUS_TONE[status as RunStatus] : 'neutral';
  return <TonePill tone={tone}>{status.replaceAll('_', ' ')}</TonePill>;
}

interface RunIdProps {
  run: RunSummary;
}

/** The run id, else (rows from before run ids) the request id, else the legacy bucket. */
function RunId({ run }: RunIdProps) {
  const id = run.run_id ?? run.request_id;
  if (id === null) {
    return <span className="text-[var(--color-text-muted)] italic text-[11px]">unattributed (legacy)</span>;
  }
  const title = run.run_id ? `Run ${id}` : `Request ${id} (no run id: written before runs were tracked)`;
  return (
    <span className="font-mono text-[11px] text-[var(--color-text)] whitespace-nowrap" title={title}>
      {shortId(id)}
    </span>
  );
}

/** How screen readers name a row: its short run id, else request id, else the legacy bucket. */
function runLabel(run: RunSummary): string {
  const id = run.run_id ?? run.request_id;
  return id ? shortId(id) : 'unattributed';
}

interface RunDetailsProps {
  run: RunSummary;
}

/** The expanded row's text for what the collapsed row only shortens or explains on hover
 *  (unreachable on touch and keyboard): the full ids and why a run is degraded. */
function RunDetails({ run }: RunDetailsProps) {
  const ids = [
    { label: 'Run', id: run.run_id },
    { label: 'Request', id: run.request_id },
    { label: 'Retry of', id: run.retry_of },
  ].filter((row): row is { label: string; id: string } => row.id !== null);
  const reasons = run.degraded === true ? degradedReasons(run.degraded_reasons) : [];
  if (ids.length === 0 && reasons.length === 0) return null;

  return (
    <dl
      data-slot="run-details"
      className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 pt-2 pb-3 text-[11px]"
    >
      {ids.map(({ label, id }) => (
        <Fragment key={label}>
          <dt className="text-[var(--color-text-muted)]">{label}</dt>
          <dd className="font-mono text-[var(--color-text)] break-all select-all">{id}</dd>
        </Fragment>
      ))}
      {reasons.length > 0 && (
        <>
          <dt className="text-[var(--color-text-muted)]">Degraded</dt>
          <dd>
            <ul className="space-y-0.5">
              {reasons.map((reason) => (
                <li key={reason.label}>
                  <span className="font-medium text-[var(--color-danger-ink)]">{reason.label}</span>
                  <span className="text-[var(--color-text)]">: {reason.detail}</span>
                </li>
              ))}
            </ul>
          </dd>
        </>
      )}
    </dl>
  );
}

function formatBypass(bypass: boolean | null): string {
  if (bypass === null) return '—';
  return bypass ? 'yes' : 'no';
}

interface RunCallsTableProps {
  calls: RunCallSummary[];
}

/** One attempt's LLM calls (the run rows' expansion; the Videos page drawer lists them too). */
export function RunCallsTable({ calls }: RunCallsTableProps) {
  if (calls.length === 0) {
    return <div className="py-2 text-xs text-[var(--color-text-muted)]">No individual calls recorded.</div>;
  }

  // A failed attempt's broken call is the first thing to read.
  const ordered = [...calls].sort((a, b) => Number(a.success !== false) - Number(b.success !== false));

  return (
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
            {ordered.map((call) => (
              <tr
                key={call.id}
                className={`border-b border-[var(--color-border)] last:border-0 transition-colors ${
                  call.success === false
                    ? 'bg-[var(--color-danger-soft)]'
                    : 'hover:bg-[var(--color-surface-dim)]'
                }`}
              >
                <td className="py-1.5 px-3">
                  <span className="inline-flex items-center gap-1.5">
                    <span className="px-1.5 py-0.5 rounded text-[10px] bg-[var(--color-surface-dim)]">
                      {call.feature ?? '—'}
                    </span>
                    {call.success === false && <TonePill tone="danger">failed</TonePill>}
                  </span>
                </td>
                <td className="py-1.5 px-3 font-mono truncate max-w-[160px]">{call.model ?? '—'}</td>
                <td className="py-1.5 px-3 text-right font-mono font-medium text-[var(--color-primary)]">
                  {formatCost(call.cost_usd)}
                </td>
                <td className="py-1.5 px-3 text-right hidden sm:table-cell">{formatUsageVolume(call)}</td>
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
  );
}

interface RunRowProps {
  run: RunSummary;
  isOpen: boolean;
  onToggle: () => void;
  showVideo: boolean;
}

function RunRow({ run, isOpen, onToggle, showVideo }: RunRowProps) {
  const columnCount = BASE_COLUMN_COUNT + (showVideo ? 1 : 0);
  return (
    <>
      <tr
        className={`border-b border-[var(--color-border)] transition-colors ${
          isOpen ? 'bg-[var(--color-primary-soft)] border-b-0' : 'hover:bg-[var(--color-surface-dim)]'
        }`}
        style={{ borderLeft: isOpen ? '3px solid var(--color-primary)' : '3px solid transparent' }}
      >
        <td className="py-3 pl-2 pr-0 sm:px-3 w-8">
          <button
            type="button"
            onClick={onToggle}
            className="flex items-center justify-center w-6 h-6 rounded-md hover:bg-[var(--color-surface-dim)] text-[var(--color-text-muted)] transition-colors"
            aria-label={`${isOpen ? 'Collapse' : 'Expand'} run ${runLabel(run)}`}
            aria-expanded={isOpen}
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

        <td className={`${CELL} min-w-0`}>
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <RunId run={run} />
            {run.regen_ordinal != null && run.regen_ordinal > 1 && <RegenBadge ordinal={run.regen_ordinal} />}
            {run.degraded === true && <DegradedBadge reasons={run.degraded_reasons} />}
          </div>
          {/* The Started column shows from lg; below it the run's age sits under the id. */}
          <time
            dateTime={run.first_call}
            title={formatDateTime(run.first_call)}
            className="mt-0.5 block text-[10px] text-[var(--color-text-muted)] whitespace-nowrap lg:hidden"
          >
            {timeAgo(run.first_call)}
          </time>
          {run.retry_of && (
            <div className="mt-0.5 text-[10px] text-[var(--color-text-muted)]" title={`Retry of run ${run.retry_of}`}>
              retry of <span className="font-mono whitespace-nowrap">{shortId(run.retry_of)}</span>
            </div>
          )}
        </td>

        {showVideo && (
          <td className={`${CELL} ${FROM_MD}`}>
            {run.video_id ? (
              <Link
                to={`/videos/${run.video_id}`}
                className="font-mono text-[11px] text-[var(--color-primary)] hover:underline whitespace-nowrap"
              >
                {run.video_id}
              </Link>
            ) : (
              <span className="text-[var(--color-text-faint)]">—</span>
            )}
          </td>
        )}

        <td className={`${CELL} text-[11px] text-[var(--color-text-muted)] whitespace-nowrap ${FROM_LG}`}>
          {formatDateTime(run.first_call)}
        </td>
        <td className={`${CELL} text-right font-mono text-[11px] ${FROM_SM}`}>
          {run.attempt != null ? `#${run.attempt}` : '—'}
        </td>
        <td className={`${CELL} font-mono text-[11px] ${FROM_MD}`}>{run.pipeline_version ?? '—'}</td>
        <td className={`${CELL} text-[11px] ${run.bypass ? 'text-[var(--color-text)]' : 'text-[var(--color-text-muted)]'} ${FROM_LG}`}>
          {formatBypass(run.bypass)}
        </td>
        <td className={`${CELL} font-mono text-[11px] text-[var(--color-text-muted)] ${FROM_LG}`}>{run.path ?? '—'}</td>
        <td className={CELL}>
          <RunStatusBadge status={run.status} />
        </td>
        <td className={`${CELL} text-right font-mono font-bold text-[var(--color-primary)]`}>
          {formatCost(run.total_cost_usd)}
        </td>
        <td className={`${CELL} text-right text-[11px] text-[var(--color-text-muted)] ${FROM_SM}`}>{run.call_count}</td>
        <td className={`${CELL} sm:pr-4`}>
          <div className="flex flex-col items-end gap-1 sm:flex-row sm:justify-end sm:gap-3">
            <LangfuseLink
              href={run.langfuse_url}
              label="Trace"
              description={`Open trace of run ${runLabel(run)} in Langfuse (opens in a new tab)`}
            />
            <LangfuseLink
              href={run.session_url}
              label="Session"
              description={`Open session of run ${runLabel(run)} in Langfuse (opens in a new tab)`}
            />
          </div>
        </td>
      </tr>

      {isOpen && (
        <tr style={{ borderLeft: '3px solid var(--color-primary)' }}>
          <td colSpan={columnCount} className="bg-[var(--color-surface)] border-b border-[var(--color-border)] px-4 pt-1 pb-3">
            {/* Zero basis, full width: the calls table scrolls in its own frame instead of widening the runs table. */}
            <div className="w-0 min-w-full">
              <RunDetails run={run} />
              <RunCallsTable calls={run.calls} />
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

/** A run's row key: its run id, else its request id; the legacy bucket falls back to its position. */
function runKey(run: RunSummary, index: number): string {
  return run.run_id ?? run.request_id ?? `unattributed-${index}`;
}

interface RunsTableProps {
  runs: RunSummary[];
  /** Adds a Video column (the YouTube id, linking to its detail page): the Usage page mixes videos. */
  showVideo?: boolean;
}

/** Pipeline attempts, one row each, expandable to their calls (Usage → Pipeline Runs, Video detail). */
export function RunsTable({ runs, showVideo = false }: RunsTableProps) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  function handleToggle(key: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-[var(--color-border)] bg-[var(--color-surface-dim)]">
            <th className="w-8 py-3 pl-2 pr-0 sm:px-3" />
            <th className={`text-left ${HEAD_CELL}`}>Run</th>
            {showVideo && <th className={`text-left ${HEAD_CELL} ${FROM_MD}`}>Video</th>}
            <th className={`text-left ${HEAD_CELL} ${FROM_LG}`}>Started</th>
            <th className={`text-right ${HEAD_CELL} ${FROM_SM}`}>Attempt</th>
            <th className={`text-left ${HEAD_CELL} ${FROM_MD}`}>Version</th>
            <th className={`text-left ${HEAD_CELL} ${FROM_LG}`}>Bypass</th>
            <th className={`text-left ${HEAD_CELL} ${FROM_LG}`}>Path</th>
            <th className={`text-left ${HEAD_CELL}`}>Status</th>
            <th className={`text-right ${HEAD_CELL}`}>Cost</th>
            <th className={`text-right ${HEAD_CELL} ${FROM_SM}`}>Calls</th>
            <th className={`text-right ${HEAD_CELL} sm:pr-4`}>Langfuse</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run, i) => {
            const key = runKey(run, i);
            return (
              <RunRow
                key={key}
                run={run}
                isOpen={expanded.has(key)}
                onToggle={() => handleToggle(key)}
                showVideo={showVideo}
              />
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
