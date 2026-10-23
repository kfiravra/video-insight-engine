import { memo, useMemo, useState } from 'react';
import { CheckCircle2, AlertTriangle, Info, BadgeCheck, BookOpen } from 'lucide-react';
import { cn } from '@/lib/utils';
import type { ClaimItem, ClaimStatus } from '@vie/types';
import { GlassCard, FadeIn, Badge, SeekChip } from '@/components/vie';
import { EmptyTabState } from './EmptyTabState';
import { claimStatus } from './claim-status';
import { QuoteLine } from './QuoteLine';

interface ClaimsTrackerProps {
  claims: ClaimItem[];
  onSeek?: (seconds: number) => void;
}

type StatusFilter = ClaimStatus | 'all';

/** What the video does with a claim (output-quality D11) — never a verdict of ours. */
const STATUS_META: Record<
  ClaimStatus,
  { label: string; variant: 'success' | 'warning' | 'muted'; Icon: typeof CheckCircle2 }
> = {
  stated: { label: 'Stated', variant: 'muted', Icon: Info },
  supported: { label: 'Supported', variant: 'success', Icon: CheckCircle2 },
  contested: { label: 'Contested', variant: 'warning', Icon: AlertTriangle },
};

const FILTER_ORDER: StatusFilter[] = ['all', 'stated', 'supported', 'contested'];

/**
 * ClaimsTracker — the news signature surface, and a talk's or podcast's on
 * has_claims (D11). Renders each claim with who made it and a status badge
 * saying what the video does with it (stated / supported / contested) — none on a
 * claim without a timestamp, whose status nobody can check (listed under All only) — plus an
 * optional evidence line and a seek-to-timestamp button. A status filter bar
 * lets the reader isolate contested claims — the accountability lens that
 * separates a news artifact from a plain summary.
 */
export const ClaimsTracker = memo(function ClaimsTracker({ claims, onSeek }: ClaimsTrackerProps) {
  const [filter, setFilter] = useState<StatusFilter>('all');

  const counts = useMemo(() => {
    const acc: Record<ClaimStatus, number> = { stated: 0, supported: 0, contested: 0 };
    for (const c of claims) {
      const status = claimStatus(c);
      if (status) acc[status] += 1;
    }
    return acc;
  }, [claims]);

  const visible = useMemo(
    () => (filter === 'all' ? claims : claims.filter((c) => claimStatus(c) === filter)),
    [claims, filter],
  );

  if (claims.length === 0) {
    return <EmptyTabState message="No claims were tracked for this video." icon={BadgeCheck} />;
  }

  return (
    <div className="flex flex-col gap-4">
      {/* Toggle buttons, not a tablist — there are no tabpanels to control. */}
      <div className="flex flex-wrap gap-2" role="group" aria-label="Filter claims by status">
        {FILTER_ORDER.map((option) => {
          const active = filter === option;
          const count = option === 'all' ? claims.length : counts[option];
          if (option !== 'all' && count === 0) return null;
          const label = option === 'all' ? 'All' : STATUS_META[option].label;
          return (
            <button
              key={option}
              type="button"
              aria-pressed={active}
              onClick={() => setFilter(option)}
              className={cn(
                'rounded-full border px-3 py-1 text-xs font-medium transition-colors',
                active
                  ? 'border-[var(--vie-accent)] bg-[var(--vie-accent)]/10 text-[var(--vie-accent)]'
                  : 'border-border text-muted-foreground hover:bg-muted/40',
              )}
            >
              {label} <span className="tabular-nums opacity-70">({count})</span>
            </button>
          );
        })}
      </div>

      <ul className="flex flex-col gap-3" aria-label="Tracked claims">
        {visible.map((claim, i) => {
          const status = claimStatus(claim);
          const meta = status ? STATUS_META[status] : null;
          // The <li> stays the list's direct child (FadeIn renders a <div>).
          return (
            <li key={`${claim.claim}-${i}`}>
              <FadeIn index={i}>
                <GlassCard className="flex flex-col gap-2.5 p-4">
                  <div className="flex items-start justify-between gap-3">
                    <p className="text-base font-medium leading-snug text-foreground">{claim.claim}</p>
                    {meta && (
                      <Badge variant={meta.variant} className="shrink-0">
                        <meta.Icon className="size-3 shrink-0" aria-hidden="true" />
                        {meta.label}
                      </Badge>
                    )}
                  </div>

                  {/* The words that make the claim, verbatim (D13). */}
                  <QuoteLine
                    quote={claim.quote}
                    quoteEn={claim.quoteEn}
                    translated={claim.quoteTranslated}
                  />

                  {/* The verification evidence carries the accountability value
                      of this surface — promoted above the attribution line. */}
                  {claim.sourceCitation ? (
                    <div className="bg-muted/20 rounded-md p-2.5">
                      <span className="type-eyebrow flex items-center gap-1.5">
                        <BookOpen className="size-3 shrink-0" aria-hidden="true" />
                        Evidence
                      </span>
                      <p className="mt-1 text-sm text-foreground/80">{claim.sourceCitation}</p>
                    </div>
                  ) : null}

                  <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
                    {claim.source ? (
                      <span>
                        <span className="text-muted-foreground/70">Claimed by</span>{' '}
                        <span className="font-medium text-foreground/90">{claim.source}</span>
                      </span>
                    ) : null}
                    {claim.timestamp != null && onSeek ? (
                      <SeekChip seconds={claim.timestamp} onSeek={onSeek} />
                    ) : null}
                  </div>
                </GlassCard>
              </FadeIn>
            </li>
          );
        })}
      </ul>
    </div>
  );
});
