import type { ReactNode } from 'react';
import type {
  BillingCreditLot,
  BillingHold,
  BillingLedgerEntry,
  BillingRecurringGrant,
} from '../../lib/billing';
import {
  describeSource,
  formatCents,
  formatMinutes,
  formatSignedMinutes,
} from '../../lib/billing';
import { formatCost, formatDateTime, formatDateTimeUtc, shortId, timeAgo } from '../../lib/format';

interface SectionProps {
  title: string;
  count: number;
  empty: string;
  children: ReactNode;
}

/** A titled table, or its empty message when `count` is 0. */
function Section({ title, count, empty, children }: SectionProps) {
  return (
    <section className="space-y-2">
      <h4 className="text-xs font-semibold uppercase tracking-wide text-[var(--color-text-muted)]">
        {title}
        {count > 0 ? ` (${count})` : ''}
      </h4>
      {count === 0 ? (
        <p className="text-xs text-[var(--color-text-muted)]">{empty}</p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-[var(--color-border)]">
          <table className="w-full text-[11px]">{children}</table>
        </div>
      )}
    </section>
  );
}

function Head({ children }: { children: ReactNode }) {
  return (
    <thead className="bg-[var(--color-surface-dim)]">
      <tr>{children}</tr>
    </thead>
  );
}

const ALIGN_CLASS = { left: 'text-left', right: 'text-right' } as const;

interface ThProps {
  children: ReactNode;
  align?: keyof typeof ALIGN_CLASS;
  /** Responsive visibility classes, e.g. `hidden sm:table-cell`. */
  hide?: string;
}

function Th({ children, align = 'left', hide = '' }: ThProps) {
  return <th className={`p-2 font-medium ${ALIGN_CLASS[align]} ${hide}`}>{children}</th>;
}

const ROW = 'border-t border-[var(--color-border)]';

// ─── Credit lots ───

type LotStatus = 'Open' | 'Expired' | 'Used up';

/**
 * Expired is checked first: the repair job zeroes the minutes of an expired lot
 * (recurring monthly grants included), so "nothing left" alone would make every
 * expired lot read "Used up".
 */
function lotStatus(lot: BillingCreditLot): LotStatus {
  if (lot.expired) return 'Expired';
  return lot.minutesRemaining === 0 ? 'Used up' : 'Open';
}

const LOT_STATUS_COLOR: Record<LotStatus, string> = {
  Open: 'var(--color-success)',
  Expired: 'var(--color-warning)',
  'Used up': 'var(--color-text-faint)',
};

export function LotsTable({ lots }: { lots: BillingCreditLot[] }) {
  return (
    <Section title="Credit lots" count={lots.length} empty="No credit lots.">
      <Head>
        <Th>Lot</Th>
        <Th hide="hidden sm:table-cell">Source</Th>
        <Th align="right">Remaining</Th>
        <Th align="right" hide="hidden sm:table-cell">Granted</Th>
        <Th align="right">Expires</Th>
        <Th>Status</Th>
        <Th align="right" hide="hidden md:table-cell">Paid</Th>
      </Head>
      <tbody>
        {lots.map((lot) => {
          const status = lotStatus(lot);
          return (
            <tr key={lot.lotId} className={ROW} data-testid={`lot-row-${lot.lotId}`}>
              <td className="p-2 font-mono text-[var(--color-text-muted)]">{shortId(lot.lotId)}</td>
              <td className="p-2 hidden sm:table-cell font-mono text-[var(--color-text-muted)]">
                {describeSource(lot.source)}
              </td>
              <td className="p-2 text-right font-mono font-medium">{formatMinutes(lot.minutesRemaining)}</td>
              <td className="p-2 text-right hidden sm:table-cell font-mono text-[var(--color-text-muted)]">
                {formatMinutes(lot.minutesGranted)}
              </td>
              <td className="p-2 text-right text-[10px]">
                {lot.expiresAt === null ? 'Never' : formatDateTimeUtc(lot.expiresAt)}
              </td>
              <td className="p-2" style={{ color: LOT_STATUS_COLOR[status] }}>{status}</td>
              <td className="p-2 text-right hidden md:table-cell font-mono text-[var(--color-text-muted)]">
                {lot.priceUsdCents != null ? formatCents(lot.priceUsdCents) : '—'}
              </td>
            </tr>
          );
        })}
      </tbody>
    </Section>
  );
}

// ─── Open holds ───

function describeSplit(hold: BillingHold): string {
  const { free, plan, credit } = hold.split;
  return [free > 0 && `free ${free}`, plan > 0 && `plan ${plan}`, credit > 0 && `credit ${credit}`]
    .filter(Boolean)
    .join(', ');
}

export function HoldsTable({ holds }: { holds: BillingHold[] }) {
  return (
    <Section title="Open holds" count={holds.length} empty="No runs in flight.">
      <Head>
        <Th>Video</Th>
        <Th align="right">Held</Th>
        <Th hide="hidden sm:table-cell">Taken from</Th>
        <Th align="right">Started</Th>
      </Head>
      <tbody>
        {holds.map((hold) => (
          <tr key={hold.requestId} className={ROW}>
            <td className="p-2 font-mono">{hold.youtubeId ?? '—'}</td>
            <td className="p-2 text-right font-mono font-medium">{formatMinutes(hold.minutes)}</td>
            <td className="p-2 hidden sm:table-cell text-[var(--color-text-muted)]">{describeSplit(hold)}</td>
            <td className="p-2 text-right text-[10px]" title={formatDateTime(hold.createdAt)}>
              {timeAgo(hold.createdAt)}
            </td>
          </tr>
        ))}
      </tbody>
    </Section>
  );
}

// ─── Recurring grants ───

interface GrantsTableProps {
  grants: BillingRecurringGrant[];
  /** A row action such as Delete; the actions column appears only when this is given. */
  renderActions?: (grant: BillingRecurringGrant) => ReactNode;
}

export function GrantsTable({ grants, renderActions }: GrantsTableProps) {
  return (
    <Section title="Recurring grants" count={grants.length} empty="No recurring grants.">
      <Head>
        <Th align="right">Minutes</Th>
        <Th>Every</Th>
        <Th>Status</Th>
        <Th align="right" hide="hidden sm:table-cell">Next run</Th>
        <Th align="right" hide="hidden md:table-cell">Last run</Th>
        <Th hide="hidden sm:table-cell">Reason</Th>
        {renderActions && <Th align="right">Actions</Th>}
      </Head>
      <tbody>
        {grants.map((grant) => (
          <tr key={grant.grantId} className={ROW}>
            <td className="p-2 text-right font-mono font-medium">{formatMinutes(grant.minutes)}</td>
            <td className="p-2">{grant.every}</td>
            <td
              className="p-2"
              style={{ color: grant.active ? 'var(--color-success)' : 'var(--color-text-faint)' }}
            >
              {grant.active ? 'Active' : 'Paused'}
            </td>
            <td className="p-2 text-right hidden sm:table-cell text-[10px]">{formatDateTime(grant.nextRunAt)}</td>
            <td className="p-2 text-right hidden md:table-cell text-[10px]">{formatDateTime(grant.lastRunAt)}</td>
            <td className="p-2 hidden sm:table-cell truncate max-w-[200px]" title={grant.reason}>
              {grant.reason}
            </td>
            {renderActions && <td className="p-2 text-right">{renderActions(grant)}</td>}
          </tr>
        ))}
      </tbody>
    </Section>
  );
}

// ─── Ledger ───

function minutesColor(minutes: number): string {
  if (minutes > 0) return 'var(--color-success)';
  return minutes < 0 ? 'var(--color-warning)' : 'var(--color-text-muted)';
}

function LedgerRow({ entry }: { entry: BillingLedgerEntry }) {
  return (
    <tr className={ROW} data-testid={`ledger-row-${entry.id}`}>
      <td className="p-2 text-[10px] whitespace-nowrap">{formatDateTime(entry.createdAt)}</td>
      <td className="p-2">{entry.type}</td>
      <td className="p-2">{entry.bucket}</td>
      <td className="p-2 text-right font-mono font-medium" style={{ color: minutesColor(entry.minutes) }}>
        {formatSignedMinutes(entry.minutes)}
      </td>
      <td className="p-2 min-w-0">
        <div className="truncate max-w-[220px]" title={entry.reason ?? undefined}>
          {entry.reason ?? describeSource(entry.source)}
        </div>
        {entry.youtubeId && (
          <div className="font-mono text-[9px] text-[var(--color-text-faint)]">{entry.youtubeId}</div>
        )}
      </td>
      <td className="p-2 hidden sm:table-cell font-mono text-[var(--color-text-muted)]" title={entry.actorId ?? undefined}>
        {entry.actorId ? shortId(entry.actorId) : '—'}
      </td>
      <td className="p-2 text-right hidden md:table-cell font-mono text-[var(--color-text-muted)]">
        {entry.costUsd != null ? formatCost(entry.costUsd) : '—'}
      </td>
    </tr>
  );
}

interface LedgerTableProps {
  entries: BillingLedgerEntry[];
}

export function LedgerTable({ entries }: LedgerTableProps) {
  return (
    <Section title="Ledger" count={entries.length} empty="No ledger entries yet.">
      <Head>
        <Th>When</Th>
        <Th>Type</Th>
        <Th>Bucket</Th>
        <Th align="right">Minutes</Th>
        <Th>Reason</Th>
        <Th hide="hidden sm:table-cell">Actor</Th>
        <Th align="right" hide="hidden md:table-cell">Cost</Th>
      </Head>
      <tbody>
        {entries.map((entry) => (
          <LedgerRow key={entry.id} entry={entry} />
        ))}
      </tbody>
    </Section>
  );
}
