import { memo, useState } from 'react';
import { Check, X, Scale } from 'lucide-react';
import { cn } from '@/lib/utils';
import { GlassCard, FadeIn, Badge } from '@/components/vie';
import { useMediaQuery } from '@/hooks/use-media-query';

import { useLabels } from '@/lib/i18n';
import { EmptyTabState } from './EmptyTabState';
import { ComparisonRadarHero } from './comparison/ComparisonRadarHero';
import { hasMultipleCompetitors } from './comparison/competitor-groups';
import { hasRadarAxes } from './comparison/radar-scoring';
import { ComparisonTable } from './comparison/ComparisonTable';
import { ReviewSummary } from './comparison/ReviewSummary';
import { VersusCards } from './comparison/VersusCards';

import type { ComparisonRow } from './comparison/types';
import type { VerdictData } from './comparison/ReviewSummary';

type ComparisonMode = 'table' | 'pros_cons' | 'versus';

interface ComparisonInteractiveProps {
  comparisons?: ComparisonRow[];
  pros?: string[];
  cons?: string[];
  type?: ComparisonMode;
  leftLabel?: string;
  rightLabel?: string;
  verdict?: VerdictData;
  /**
   * Force the radar hero on/off. When omitted the component decides by data:
   * radar shows automatically for ≥3 comparison rows (scoreable axes). The
   * `comparison_radar` registry alias passes `view="radar"` so the 1A
   * promotion `comparison → comparison_radar` keeps surfacing the radar.
   */
  view?: 'auto' | 'radar' | 'table';
  nextTab?: string;
  onNavigateTab?: (id: string) => void;
}

export const ComparisonInteractive = memo(function ComparisonInteractive({
  comparisons = [],
  pros,
  cons,
  type = 'table',
  leftLabel,
  rightLabel,
  verdict,
  view = 'auto',
}: ComparisonInteractiveProps) {
  const t = useLabels();
  const [activeRow, setActiveRow] = useState(0);

  // Radar and table are mutually exclusive views, not a stacked pair. Mobile
  // opens on the table (the radar is cramped under ~340px); desktop opens on
  // the radar hero. `view="radar"` forces the radar regardless of viewport.
  const isCompact = useMediaQuery('(max-width: 640px)');
  const [comparisonView, setComparisonView] = useState<'radar' | 'details'>(
    () => (view !== 'radar' && isCompact ? 'details' : 'radar'),
  );

  const hasComparisons = comparisons.length > 0;
  const hasPros = pros && pros.length > 0;
  const hasCons = cons && cons.length > 0;
  const hasVerdictHeader = Boolean(verdict?.bottomLine);
  const competitorName = hasComparisons ? comparisons[0].competitorName : '';
  const isReview = Boolean(competitorName);
  const colLeft = leftLabel || (isReview ? 'This Product' : t.description);
  // Two or more competitors: no single name speaks for the other side.
  const otherSide = hasMultipleCompetitors(comparisons) ? t.competitor : competitorName;
  const colRight = rightLabel || otherSide || (isReview ? t.competitor : t.example);

  // Radar hero when the data has enough scoreable axes (or forced via `view`).
  // `view="radar"` still needs ≥3 axes — a 2-spoke radar is a line, so it
  // gracefully falls back to the table.
  const showRadar = view !== 'table' && hasComparisons && hasRadarAxes(comparisons);

  if (!hasComparisons && !hasPros && !hasCons && !hasVerdictHeader) return <EmptyTabState message="No comparison data was extracted for this video." icon={Scale} />;

  return (
    <div className="space-y-4">
      {/* Review summary header — renders only when verdict.bottomLine is set */}
      {hasVerdictHeader && <ReviewSummary verdict={verdict!} />}

      {/* View toggle — only when a radar hero and a detail view both exist.
          One view at a time replaces the old radar-stacked-on-table wall. */}
      {showRadar && (
        <div
          className="flex w-fit items-center gap-1 rounded-lg bg-muted/40 p-0.5"
          role="group"
          aria-label="Comparison view"
        >
          <button
            type="button"
            onClick={() => setComparisonView('radar')}
            aria-pressed={comparisonView === 'radar'}
            className={cn(
              'rounded-md px-3 py-1 text-xs font-semibold transition-colors',
              comparisonView === 'radar'
                ? 'bg-card text-foreground shadow-xs'
                : 'text-muted-foreground hover:text-foreground',
            )}
          >
            Radar
          </button>
          <button
            type="button"
            onClick={() => setComparisonView('details')}
            aria-pressed={comparisonView === 'details'}
            className={cn(
              'rounded-md px-3 py-1 text-xs font-semibold transition-colors',
              comparisonView === 'details'
                ? 'bg-card text-foreground shadow-xs'
                : 'text-muted-foreground hover:text-foreground',
            )}
          >
            {type === 'versus' ? 'Cards' : 'Table'}
          </button>
        </div>
      )}

      {/* Radar hero — when ≥3 scoreable axes (auto) or forced via view="radar" */}
      {showRadar && comparisonView === 'radar' && (
        <ComparisonRadarHero comparisons={comparisons} colLeft={colLeft} colRight={colRight} />
      )}

      {/* Feature comparison — table mode */}
      {hasComparisons && type === 'table' && (!showRadar || comparisonView === 'details') && (
        <ComparisonTable
          comparisons={comparisons}
          colLeft={colLeft}
          colRight={colRight}
          activeRow={activeRow}
          onSelectRow={setActiveRow}
        />
      )}

      {/* Versus mode */}
      {hasComparisons && type === 'versus' && (!showRadar || comparisonView === 'details') && (
        <VersusCards comparisons={comparisons} colLeft={colLeft} colRight={colRight} />
      )}

      {/* Go for it / Skip it recommendation cards */}
      {(hasPros || hasCons) && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {hasPros && (
            <FadeIn>
              <GlassCard variant="outlined" className="space-y-2">
                <h4 className="text-xs font-semibold uppercase tracking-wider text-success flex items-center gap-1.5">
                  <Check className="h-3.5 w-3.5" aria-hidden="true" />
                  {t.goForIt}
                  <Badge variant="success" className="text-xs font-semibold tabular-nums">{pros.length}</Badge>
                </h4>
                <ul className="space-y-1.5">
                  {pros.map((pro, i) => (
                    <li key={i} className="flex items-baseline gap-2 text-sm leading-relaxed text-muted-foreground">
                      <span className="w-1 h-1 rounded-full bg-success/70 shrink-0 translate-y-1.5" />
                      {pro}
                    </li>
                  ))}
                </ul>
              </GlassCard>
            </FadeIn>
          )}
          {hasCons && (
            <FadeIn>
              <GlassCard variant="outlined" className="space-y-2">
                <h4 className="text-xs font-semibold uppercase tracking-wider text-destructive flex items-center gap-1.5">
                  <X className="h-3.5 w-3.5" aria-hidden="true" />
                  Skip it if...
                  <Badge variant="destructive" className="text-xs font-semibold tabular-nums">{cons.length}</Badge>
                </h4>
                <ul className="space-y-1.5">
                  {cons.map((con, i) => (
                    <li key={i} className="flex items-baseline gap-2 text-sm leading-relaxed text-muted-foreground">
                      <span className="w-1 h-1 rounded-full bg-destructive/70 shrink-0 translate-y-1.5" />
                      {con}
                    </li>
                  ))}
                </ul>
              </GlassCard>
            </FadeIn>
          )}
        </div>
      )}

    </div>
  );
});
