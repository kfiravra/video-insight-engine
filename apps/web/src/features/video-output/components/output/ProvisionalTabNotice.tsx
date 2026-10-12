import { memo } from 'react';
import { Badge } from '@/components/vie';

interface ProvisionalTabNoticeProps {
  /** Planned tabs that haven't streamed yet. */
  pendingTabCount: number;
  /** The stream is still running — the real tab is still coming. */
  isLive: boolean;
}

function followUp(pendingTabCount: number, isLive: boolean): string {
  if (!isLive) return 'Live updates stopped before the full analysis arrived.';
  if (pendingTabCount <= 0) return 'Frames and exact moments fill in here as the analysis lands.';
  const tabs = pendingTabCount === 1 ? '1 more tab is' : `${pendingTabCount} more tabs are`;
  return `Frames and exact moments fill in here as the analysis lands — ${tabs} on the way.`;
}

/**
 * Header of a provisional tab (the plan-done Moments tab): says the content
 * is an early outline-based pass that the real tab replaces in place, so the
 * wait reads as progress rather than as a thin result. A plain labelled
 * region, not a live region — it would re-announce on every tab switch.
 */
export const ProvisionalTabNotice = memo(function ProvisionalTabNotice({
  pendingTabCount,
  isLive,
}: ProvisionalTabNoticeProps) {
  return (
    <section
      aria-label="Early preview"
      className="mb-4 flex flex-col gap-2 rounded-xl border border-dashed border-border/70 bg-muted/15 px-4 py-3 sm:flex-row sm:items-center sm:gap-3"
    >
      <Badge variant="muted" className="w-fit shrink-0">
        {isLive && (
          <span
            aria-hidden="true"
            className="size-1.5 rounded-full bg-[var(--vie-accent)] animate-[stage-pulse_1.6s_ease-in-out_infinite] motion-reduce:animate-none"
          />
        )}
        Early preview
      </Badge>
      <p className="text-sm leading-snug text-muted-foreground text-pretty">
        <span className="text-foreground/85">
          {isLive ? 'Mapped from the video’s outline while the full analysis runs.' : 'Mapped from the video’s outline.'}
        </span>{' '}
        {followUp(pendingTabCount, isLive)}
      </p>
    </section>
  );
});
