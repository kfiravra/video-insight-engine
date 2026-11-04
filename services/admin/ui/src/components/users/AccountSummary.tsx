import type { ReactNode } from 'react';
import type { BillingAccountSnapshot, BillingCaps } from '../../lib/billing';
import { describeBalance, formatTokens } from '../../lib/billing';
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

function planTerm(plan: BillingAccountSnapshot['plan']): string {
  if (plan.id === 'free') return 'No plan term';
  return `${formatDateTimeUtc(plan.periodStart)} to ${formatDateTimeUtc(plan.periodEnd)}`;
}

function planTokensDetail(tokens: BillingAccountSnapshot['planTokens']): string {
  if (tokens.cycleEnd === null) return 'No active cycle';
  return `${formatTokens(tokens.used)} of ${formatTokens(tokens.granted)} used, cycle ends ${formatDateTime(tokens.cycleEnd)}`;
}

/** The caps an admin set, as "label: value" strings; empty when the plan defaults apply. */
function describeCaps(caps: BillingCaps): string[] {
  const parts: string[] = [];
  if (caps.maxVideoMinutes != null) parts.push(`max video ${caps.maxVideoMinutes} min`);
  if (caps.maxConcurrent != null) parts.push(`${caps.maxConcurrent} runs at once`);
  return parts;
}

interface AccountSummaryProps {
  account: BillingAccountSnapshot;
}

export function AccountSummary({ account }: AccountSummaryProps) {
  const { plan, planTokens, credits, caps, balance, debt } = account;
  const capParts = describeCaps(caps);

  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 gap-2" data-testid="account-summary">
      <SummaryTile
        label="Plan"
        value={plan.id === 'pro' ? 'Pro' : 'Free'}
        detail={`${plan.status}${plan.source ? `, ${plan.source}` : ''}. ${planTerm(plan)}`}
        testId="account-plan"
      />
      <SummaryTile
        label="Balance"
        value={describeBalance(balance, debt)}
        tone={balance < 0 || debt > 0 ? 'bad' : 'default'}
        detail="Plan tokens left + credits - debt"
        testId="account-balance"
      />
      <SummaryTile
        label="Pro cycle tokens"
        value={`${formatTokens(planTokens.remaining)} left`}
        detail={planTokensDetail(planTokens)}
        testId="account-plan-tokens"
      />
      <SummaryTile
        label="Credits"
        value={formatTokens(credits.available)}
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
