import { memo, useState, useMemo, useEffect, useRef } from 'react';
import { ChevronDown, ChevronUp } from 'lucide-react';
import {
  Legend,
  PolarAngleAxis,
  PolarGrid,
  PolarRadiusAxis,
  Radar,
  RadarChart,
} from 'recharts';
import { GlassCard, Badge } from '@/components/vie';
import { Slider } from '@/components/ui/slider';

import { DEFAULT_WEIGHT, MAX_SCORE, scoreComparisonAxes } from './radar-scoring';

import type { ReviewComparison } from '@vie/types';
import type { AxisScore } from './radar-scoring';

function computeLeftPercent(scores: AxisScore[], weights: number[]): number {
  const leftTotal = scores.reduce((acc, s, i) => acc + s.left * (weights[i] ?? DEFAULT_WEIGHT), 0);
  const rightTotal = scores.reduce((acc, s, i) => acc + s.right * (weights[i] ?? DEFAULT_WEIGHT), 0);
  const sum = leftTotal + rightTotal;
  if (sum <= 0) return 50;
  return Math.round((leftTotal / sum) * 100);
}

interface ComparisonRadarHeroProps {
  comparisons: ReviewComparison[];
  colLeft: string;
  colRight: string;
}

/**
 * Radar hero — one axis per numeric comparison row (`scoreComparisonAxes`),
 * two series, per-axis weight sliders to re-balance the weighted winner.
 * Rendered above the table when a comparison has ≥3 scoreable axes. Merged in
 * from the retired standalone ComparisonRadar; the unified component now
 * chooses radar-vs-table by data.
 */
export const ComparisonRadarHero = memo(function ComparisonRadarHero({
  comparisons,
  colLeft,
  colRight,
}: ComparisonRadarHeroProps) {
  const scored = useMemo(() => scoreComparisonAxes(comparisons), [comparisons]);
  const [weights, setWeights] = useState<number[]>(() => scored.map(() => DEFAULT_WEIGHT));
  // One weight per scored axis, not per row: a prose row has no spoke, so it
  // never resets the tuning, while a new axis count starts it fresh. Adjusted
  // while rendering (React's pattern for state that follows a prop), no effect.
  if (weights.length !== scored.length) {
    setWeights(scored.map(() => DEFAULT_WEIGHT));
  }
  const [tunerOpen, setTunerOpen] = useState(false);

  // Self-measure the chart box rather than leaning on Recharts'
  // ResponsiveContainer, which latches a 0×0 reading when it mounts inside a
  // hidden (display:none) tab and never recovers on reveal — the radar then
  // renders as a ~14px stub. A ResizeObserver fires correctly when the element
  // first gains size, so we hand RadarChart explicit pixel dimensions.
  const chartRef = useRef<HTMLDivElement>(null);
  const [chartSize, setChartSize] = useState<{ width: number; height: number }>({ width: 0, height: 0 });
  useEffect(() => {
    const el = chartRef.current;
    if (!el) return;
    // Floor out implausibly-narrow readings (~14px) that Recharts/ResizeObserver
    // can deliver mid-layout and latch forever. A real radar card is always far
    // wider than this, so the floor rejects only the glitch — legitimate small
    // viewports (and shrinks from a larger size) still pass through.
    const MIN_CHART_WIDTH = 100;
    const measure = (width: number, height: number) => {
      if (width >= MIN_CHART_WIDTH && height > 0) {
        setChartSize({ width, height });
      }
    };
    // Synchronous read first: an already-laid-out container is sized now,
    // so we don't depend on the observer's first async callback.
    const initial = el.getBoundingClientRect();
    measure(initial.width, initial.height);
    const observer = new ResizeObserver((entries) => {
      const rect = entries[0]?.contentRect;
      if (rect) measure(rect.width, rect.height);
    });
    observer.observe(el);
    // Backstop: if the container mounts tiny (mid tab-transition) the observer
    // can miss the settle. Re-read after the browser has laid out for real.
    const raf1 = requestAnimationFrame(() => {
      const r = el.getBoundingClientRect();
      measure(r.width, r.height);
    });
    return () => {
      cancelAnimationFrame(raf1);
      observer.disconnect();
    };
  }, []);

  const leftPercent = useMemo(() => computeLeftPercent(scored, weights), [scored, weights]);
  const rightPercent = 100 - leftPercent;
  const winnerLabel = leftPercent === rightPercent ? null : leftPercent > rightPercent ? colLeft : colRight;
  const winnerMargin = Math.abs(leftPercent - rightPercent);

  const chartData = scored.map((s) => ({ feature: s.feature, [colLeft]: s.left, [colRight]: s.right }));
  // A lower-is-better spoke is scored inverted (the cheaper side reaches the
  // rim) — say so, or "Price" at the rim reads as "pricier".
  const lowerIsBetter = scored.filter((s) => s.lowerIsBetter).map((s) => s.feature);

  return (
    <div className="space-y-3">
      <GlassCard variant="default" className="p-4">
        <div className="mb-3 flex items-center justify-between gap-2">
          <h3 className="text-sm font-semibold text-foreground">Side-by-side</h3>
          <span data-testid="comparison-radar-winner-badge">
            {winnerLabel ? (
              <Badge variant="success" className="text-xs font-semibold">
                {winnerLabel} wins by {winnerMargin}%
              </Badge>
            ) : (
              <Badge variant="muted" className="text-xs font-medium">
                Tie
              </Badge>
            )}
          </span>
        </div>
        <div
          ref={chartRef}
          className="h-[260px] w-full sm:h-[320px]"
          role="img"
          aria-label={`Radar comparing ${colLeft} and ${colRight}`}
        >
          {chartSize.width > 0 && chartSize.height > 0 && (
            <RadarChart width={chartSize.width} height={chartSize.height} data={chartData} outerRadius="75%">
              <PolarGrid stroke="var(--border)" />
              <PolarAngleAxis dataKey="feature" tick={{ fill: 'var(--muted-foreground)', fontSize: 11 }} />
              <PolarRadiusAxis
                domain={[0, MAX_SCORE]}
                tick={{ fill: 'var(--muted-foreground)', fontSize: 10 }}
                stroke="var(--border)"
              />
              <Radar
                name={colLeft}
                dataKey={colLeft}
                stroke="var(--vie-accent)"
                fill="var(--vie-accent)"
                fillOpacity={0.35}
              />
              <Radar
                name={colRight}
                dataKey={colRight}
                stroke="var(--muted-foreground)"
                fill="var(--muted-foreground)"
                fillOpacity={0.2}
              />
              <Legend />
            </RadarChart>
          )}
        </div>
        {lowerIsBetter.length > 0 && (
          <p className="mt-2 text-xs text-muted-foreground">
            Further out is better — lower wins on {lowerIsBetter.join(', ')}.
          </p>
        )}
      </GlassCard>

      {/* Weight sliders collapse by default — the winner split stays visible so
          the signal survives, but the six-slider control no longer competes for
          attention on first read. */}
      <GlassCard variant="outlined" className="space-y-3 p-4">
        <button
          type="button"
          onClick={() => setTunerOpen((open) => !open)}
          aria-expanded={tunerOpen}
          className="flex w-full items-center justify-between gap-2"
        >
          <span className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            Tune what matters
            {tunerOpen
              ? <ChevronUp className="h-3.5 w-3.5" aria-hidden="true" />
              : <ChevronDown className="h-3.5 w-3.5" aria-hidden="true" />}
          </span>
          <span className="text-xs font-medium tabular-nums text-muted-foreground">
            {colLeft} {leftPercent}% · {colRight} {rightPercent}%
          </span>
        </button>
        {tunerOpen && (
        <ul className="space-y-2">
          {scored.map((axis, index) => (
            <li
              key={`${axis.feature}-${index}`}
              className="grid grid-cols-[minmax(0,1fr)_minmax(120px,2fr)_3rem] items-center gap-3"
            >
              <span className="truncate text-sm text-foreground">{axis.feature}</span>
              <Slider
                aria-label={`Weight for ${axis.feature}`}
                min={0}
                max={MAX_SCORE}
                step={1}
                value={[weights[index] ?? DEFAULT_WEIGHT]}
                onValueChange={(next) => {
                  const value = Array.isArray(next) ? next[0] : DEFAULT_WEIGHT;
                  setWeights((prev) => {
                    const updated = prev.slice();
                    updated[index] = value ?? DEFAULT_WEIGHT;
                    return updated;
                  });
                }}
                data-testid={`comparison-radar-weight-${index}`}
              />
              <span className="text-end text-xs font-mono tabular-nums text-muted-foreground">
                {weights[index] ?? DEFAULT_WEIGHT}
              </span>
            </li>
          ))}
        </ul>
        )}
      </GlassCard>
    </div>
  );
});
