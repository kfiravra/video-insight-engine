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

/** The schema admits a numeric `value`; render it as text and never call
 *  string methods on it. */
function statValue(stat: StatBannerStat): string {
  const raw: unknown = stat.value;
  return raw == null ? '' : String(raw).trim();
}

/**
 * StatBanner — a responsive grid of equal-weight compact stats. Secondary-tier
 * (attachment-only). Deliberately NOT a hero-metric template: no oversized
 * number, no gradient, no single dominant figure. Every stat carries the same
 * visual weight so the banner reads as a quick reference strip, not a dashboard
 * KPI hero. Two columns on phones; from `sm` up the grid auto-fits tiles of
 * at least 140 px, so a row holds every stat only when each one gets that
 * width and wraps otherwise. Text wraps between words; a word breaks only
 * when it alone overflows its tile.
 */
export const StatBanner = memo(function StatBanner({ stats, className, onSeek }: StatBannerProps) {
  const clean = stats.filter((s) => s && s.label?.trim() && statValue(s));
  if (clean.length === 0) return null;

  return (
    <div
      className={cn(
        'grid grid-cols-2 gap-2 rounded-xl border border-border bg-card p-2',
        'sm:[grid-template-columns:repeat(auto-fit,minmax(140px,1fr))]',
        className,
      )}
      role="list"
      aria-label="Key stats"
    >
      {clean.map((stat, i) => (
        <div
          key={`${stat.label}-${i}`}
          role="listitem"
          className="flex min-w-0 items-start gap-2 rounded-lg bg-muted/20 px-3 py-2"
        >
          {stat.emoji ? (
            <span aria-hidden="true" className="mt-0.5 shrink-0 text-base leading-none">
              {stat.emoji}
            </span>
          ) : null}
          <span className="flex min-w-0 flex-1 flex-col leading-tight break-words hyphens-none">
            {/* The seek chip shares the label row, so the figure and its meaning
                keep the tile's full width instead of wrapping word by word
                beside the chip. */}
            <span className="flex items-center justify-between gap-2">
              <span className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground/80">
                {stat.label}
              </span>
              {typeof stat.timestamp === 'number' && stat.timestamp >= 0 ? (
                <SeekChip seconds={stat.timestamp} onSeek={onSeek} className="-my-1 shrink-0" />
              ) : null}
            </span>
            <span className="text-sm font-semibold tabular-nums text-foreground">
              {statValue(stat)}
            </span>
            {stat.meaning?.trim() ? (
              <span className="mt-0.5 text-xs font-normal leading-snug text-muted-foreground">
                {stat.meaning}
              </span>
            ) : null}
          </span>
        </div>
      ))}
    </div>
  );
});
