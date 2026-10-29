import type { ReactNode } from 'react';
import type { BillingAccountSnapshot, BillingCaps } from '../../lib/billing';
import { formatMinutes } from '../../lib/billing';
import { formatDateTime, formatDateTimeUtc } from '../../lib/format';

interface SummaryTileProps {
  label: string;
  value: ReactNode;
  detail?: ReactNode;
  tone?: 'default' | 'good' | 'warn' | 'bad';
  testId?: string;
}

const TONE_CLASS: Record<NonNullable<SummaryTileProps['tone']>, string> = {
  default: 'text-[var(--color-text)]',
  good: 'text-[var(--color-success)]',
  warn: 'text-[var(--color-warning)]',
  bad: 'text-[var(--color-danger)]',
};

function SummaryTile({ label, value, detail, tone = 'default', testId }: SummaryTileProps) {
  return (
    <div
      className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-dim)] p-3 min-w-0"
      data-testid={testId}
    >
      <div className="text-[10px] uppercase tracking-wide text-[var(--color-text-faint)]">{label}</div>
      <div className={`mt-1 font-mono text-sm font-semibold ${TONE_CLASS[tone]}`}>{value}</div>
      {detail && <div className="mt-1 text-[10px] text-[var(--color-text-muted)]">{detail}</div>}
    </div>
  );
}

type FreeVideo = BillingAccountSnapshot['free'];

function freeVideoTile(free: FreeVideo): Pick<SummaryTileProps, 'value' | 'tone'> {
  if (free.perWindow <= 0) return { value: 'Disabled', tone: 'bad' };
  if (free.available) return { value: 'Ready', tone: 'good' };
  return { value: `Next at ${formatDateTime(free.nextFreeAt)}`, tone: 'warn' };
}

function planTerm(plan: BillingAccountSnapshot['plan']): string {
  if (plan.id === 'free') return 'No plan term';
  return `${formatDateTimeUtc(plan.periodStart)} to ${formatDateTimeUtc(plan.periodEnd)}`;
}

function planMinutesDetail(minutes: BillingAccountSnapshot['planMinutes']): string {
  if (minutes.cycleEnd === null) return 'No active cycle';
  return `${formatMinutes(minutes.used)} of ${formatMinutes(minutes.granted)} used, cycle ends ${formatDateTime(minutes.cycleEnd)}`;
}

/** The caps an admin set, as "label: value" strings; empty when the plan defaults apply. */
function describeCaps(caps: BillingCaps): string[] {
  const parts: string[] = [];
  if (caps.maxVideoMinutes != null) parts.push(`max video ${caps.maxVideoMinutes} min`);
  if (caps.freeVideosPerDay != null) parts.push(`${caps.freeVideosPerDay} free videos per day`);
  if (caps.maxConcurrent != null) parts.push(`${caps.maxConcurrent} runs at once`);
  return parts;
}

interface AccountSummaryProps {
  account: BillingAccountSnapshot;
}

export function AccountSummary({ account }: AccountSummaryProps) {
  const { plan, free, planMinutes, credits, caps } = account;
  const capParts = describeCaps(caps);
  const freeTile = freeVideoTile(free);

  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 gap-2" data-testid="account-summary">
      <SummaryTile
        label="Plan"
        value={plan.id === 'pro' ? 'Pro' : 'Free'}
        detail={`${plan.status}${plan.source ? `, ${plan.source}` : ''}. ${planTerm(plan)}`}
        testId="account-plan"
      />
      <SummaryTile
        label="Free video"
        value={freeTile.value}
        tone={freeTile.tone}
        detail={`${free.usedInWindow} of ${free.perWindow} used in the last 24 h, up to ${free.maxVideoMinutes} min`}
        testId="account-free-video"
      />
      <SummaryTile
        label="Plan minutes"
        value={`${formatMinutes(planMinutes.remaining)} left`}
        detail={planMinutesDetail(planMinutes)}
        testId="account-plan-minutes"
      />
      <SummaryTile
        label="Credits"
        value={formatMinutes(credits.available)}
        detail="Spendable, unexpired lots"
        testId="account-credits"
      />
      <SummaryTile
        label="Open holds"
        value={account.openHolds}
        detail="Runs in flight"
        testId="account-open-holds"
      />
      <SummaryTile
        label="Caps"
        value={caps.blocked ? 'Blocked' : capParts.length > 0 ? 'Limited' : 'None'}
        tone={caps.blocked ? 'bad' : capParts.length > 0 ? 'warn' : 'default'}
        detail={capParts.length > 0 ? capParts.join(', ') : 'Plan defaults apply'}
        testId="account-caps"
      />
    </div>
  );
}
