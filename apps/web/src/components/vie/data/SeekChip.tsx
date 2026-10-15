import { memo } from 'react';
import { Play } from 'lucide-react';
import { cn } from '@/lib/utils';
import { formatDuration } from '@/lib/string-utils';
import { Button } from '@/components/ui/button';

interface SeekChipProps {
  seconds: number;
  onSeek?: (seconds: number) => void;
  className?: string;
}

const CHIP_CLASS =
  'inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs font-bold tabular-nums text-[var(--vie-accent)] bg-[var(--vie-accent)]/10';

/** Accent timestamp chip shared by the spot cards and the quiz explanations
 *  (hour-aware via `formatDuration`): a button only when a seek handler
 *  exists, otherwise a static marker. */
export const SeekChip = memo(function SeekChip({ seconds, onSeek, className }: SeekChipProps) {
  const time = formatDuration(seconds);
  if (!onSeek) {
    return (
      <span dir="ltr" className={cn(CHIP_CLASS, className)}>
        {time}
      </span>
    );
  }
  return (
    <Button
      variant="ghost"
      size="bare"
      onClick={() => onSeek(seconds)}
      dir="ltr"
      aria-label={`Jump to ${time}`}
      className={cn(
        CHIP_CLASS,
        'transition-colors hover:bg-[var(--vie-accent)]/20 hover:text-[var(--vie-accent)]',
        className,
      )}
    >
      <Play className="h-3 w-3 shrink-0 fill-current" aria-hidden="true" />
      {time}
    </Button>
  );
});
