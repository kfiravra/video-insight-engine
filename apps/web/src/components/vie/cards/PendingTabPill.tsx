import { memo } from 'react';
import { cn } from '@/lib/utils';
import { stripLeadingEmoji } from '@/lib/string-utils';

interface PendingTabPillProps {
  label: string;
  emoji?: string;
  /** Position in the strip — drives the arrival stagger. */
  index?: number;
  /** Trails live tabs: dashed and dimmer so it never reads as clickable. */
  upcoming?: boolean;
}

/** An inert pill for a planned tab that hasn't landed — in the exact geometry
 *  of a real tab button, so its arrival reads as a fill-in, not a jump. */
export const PendingTabPill = memo(function PendingTabPill({
  label,
  emoji,
  index = 0,
  upcoming = false,
}: PendingTabPillProps) {
  return (
    <span
      aria-hidden="true"
      data-stream-tab-pill
      style={{ '--stagger-i': index } as React.CSSProperties}
      className={cn(
        'inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-xl border px-3.5 py-2 text-sm font-medium',
        upcoming
          ? 'border-dashed border-border/60 text-muted-foreground/60'
          : 'border-border/50 bg-muted/20 text-muted-foreground/80',
      )}
    >
      {emoji && (
        <span aria-hidden="true" className={cn('text-[0.95rem] leading-none', upcoming && 'opacity-70')}>
          {emoji}
        </span>
      )}
      <span>{stripLeadingEmoji(label)}</span>
    </span>
  );
});
