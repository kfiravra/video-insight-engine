import { memo } from 'react';
import { cn } from '@/lib/utils';
import { SeekChip } from '@/components/vie';

/** Mirrors `StatBannerStat` in @vie/types: the assembler's 0b.11 tiles carry
 *  `meaning` (the key point behind the figure) and `timestamp` (seconds); both render. */
export interface StatBannerStat {
  label: string;
  value: string;
  emoji?: string;
  meaning?: string;
  timestamp?: number;
}

interface StatBannerProps {
  stats: StatBannerStat[];
  className?: string;
  /** Seeks the player; a tile's timestamp is a chip only when this exists. */
  onSeek?: (seconds: number) => void;
}

/**
 * StatBanner — a row of equal-weight compact stats. Secondary-tier
 * (attachment-only). Deliberately NOT a hero-metric template: no oversized
 * number, no gradient, no single dominant figure. Every stat carries the same
 * visual weight so the banner reads as a quick reference strip, not a dashboard
 * KPI hero.
 */
export const StatBanner = memo(function StatBanner({ stats, className, onSeek }: StatBannerProps) {
  const clean = stats.filter((s) => s && s.label?.trim() && s.value?.trim());
  if (clean.length === 0) return null;

  return (
    <div
      className={cn(
        'flex flex-wrap items-stretch gap-2 rounded-xl border border-border bg-card p-2',
        className,
      )}
      role="list"
      aria-label="Key stats"
    >
      {clean.map((stat, i) => (
        <div
          key={`${stat.label}-${i}`}
          role="listitem"
          className="flex min-w-[6rem] flex-1 items-center gap-2 rounded-lg bg-muted/20 px-3 py-2"
        >
          {stat.emoji ? (
            <span aria-hidden="true" className="shrink-0 text-base leading-none">
              {stat.emoji}
            </span>
          ) : null}
          <span className="flex min-w-0 flex-1 flex-col leading-tight">
            <span className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground/80">
              {stat.label}
            </span>
            <span className="text-sm font-semibold tabular-nums text-foreground">
              {stat.value}
            </span>
            {stat.meaning?.trim() ? (
              <span className="mt-0.5 text-xs font-normal leading-snug text-muted-foreground">
                {stat.meaning}
              </span>
            ) : null}
          </span>
          {typeof stat.timestamp === 'number' && stat.timestamp >= 0 ? (
            <SeekChip seconds={stat.timestamp} onSeek={onSeek} className="shrink-0" />
          ) : null}
        </div>
      ))}
    </div>
  );
});
