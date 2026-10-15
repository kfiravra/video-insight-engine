import { Clock } from 'lucide-react';

import { Checkbox } from '@/components/ui/checkbox';
import { cn } from '@/lib/utils';
import type { StepItem } from '@vie/types';

/** One step as both views render it (flow node card and list row). */
export interface StepData {
  step: StepItem;
  index: number;
  completed: boolean;
  durationSeconds: number;
  onToggle: (index: number, completed: boolean) => void;
  onSeek?: (seconds: number) => void;
}

export function StepNumber({ number, completed }: { number: number; completed: boolean }) {
  return (
    <span
      className={cn(
        'inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full border text-xs font-bold tabular-nums',
        completed
          ? 'border-success bg-success text-success-foreground'
          : 'border-[color:var(--vie-accent,var(--primary))]/50 text-[color:var(--vie-accent,var(--primary))]',
      )}
      aria-hidden="true"
    >
      {number}
    </span>
  );
}

/** Compact human label for the step-duration chip — "5 min" / "45s" / "1h 10m". */
function formatDurationLabel(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min`;
  const hrs = Math.floor(minutes / 60);
  const rem = minutes % 60;
  return rem === 0 ? `${hrs}h` : `${hrs}h ${rem}m`;
}

export function DurationChip({ seconds }: { seconds: number }) {
  if (seconds <= 0) return null;
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-muted/40 px-2 py-0.5 text-[11px] font-medium tabular-nums text-muted-foreground">
      <Clock className="h-3 w-3 shrink-0" aria-hidden="true" />
      {formatDurationLabel(seconds)}
    </span>
  );
}

interface CompleteToggleProps {
  stepNumber: number;
  index: number;
  completed: boolean;
  onToggle: (index: number, completed: boolean) => void;
}

export function CompleteToggle({ stepNumber, index, completed, onToggle }: CompleteToggleProps) {
  return (
    <label className="flex items-center gap-2 pt-1 text-xs font-medium text-foreground/80">
      <Checkbox
        checked={completed}
        onCheckedChange={() => onToggle(index, completed)}
        aria-label={completed ? `Mark step ${stepNumber} incomplete` : `Mark step ${stepNumber} complete`}
      />
      <span>{completed ? 'Done' : 'Mark complete'}</span>
    </label>
  );
}
