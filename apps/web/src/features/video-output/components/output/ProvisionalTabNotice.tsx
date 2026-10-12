import { memo } from 'react';
import { Badge } from '@/components/vie';

interface ProvisionalTabNoticeProps {
  /** Planned tabs that haven't streamed yet. */
  pendingTabCount: number;
}

function comingUp(pendingTabCount: number): string {
  if (pendingTabCount <= 0) return 'Frames and exact moments fill in here as the analysis lands.';
  const tabs = pendingTabCount === 1 ? '1 more tab is' : `${pendingTabCount} more tabs are`;
  return `Frames and exact moments fill in here as the analysis lands — ${tabs} on the way.`;
}

/**
 * Header of a provisional tab (the plan-done Moments tab): says the content
 * is an early outline-based pass that the real tab replaces in place, so the
 * wait reads as progress rather than as a thin result.
 */
export const ProvisionalTabNotice = memo(function ProvisionalTabNotice({
  pendingTabCount,
}: ProvisionalTabNoticeProps) {
  return (
    <div
      role="status"
      className="mb-4 flex flex-col gap-2 rounded-xl border border-dashed border-border/70 bg-muted/15 px-4 py-3 sm:flex-row sm:items-center sm:gap-3"
    >
      <Badge variant="muted" className="w-fit shrink-0">
        <span
          aria-hidden="true"
          className="size-1.5 rounded-full bg-[var(--vie-accent)] animate-[stage-pulse_1.6s_ease-in-out_infinite] motion-reduce:animate-none"
        />
        Early preview
      </Badge>
      <p className="text-sm leading-snug text-muted-foreground text-pretty">
        <span className="text-foreground/85">Mapped from the video&apos;s outline while the full analysis runs.</span>{' '}
        {comingUp(pendingTabCount)}
      </p>
    </div>
  );
});
