import { Play } from 'lucide-react';
import { cn } from '@/lib/utils';
import { formatDuration } from '@/lib/string-utils';
import { splitTimeRefs } from '@/features/video-output/lib/quiz-time-refs';

interface QuizSeekChipProps {
  seconds: number;
  onSeek?: (seconds: number) => void;
}

/** Inline timestamp chip — same accent chip as the spot cards; a button only
 *  when a seek handler exists, otherwise a static marker. */
function QuizSeekChip({ seconds, onSeek }: QuizSeekChipProps) {
  const time = formatDuration(seconds);
  const chipClass =
    'ms-1 inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 align-baseline text-xs font-bold tabular-nums text-[var(--vie-accent)] bg-[var(--vie-accent)]/10';
  if (!onSeek) {
    return (
      <span dir="ltr" className={chipClass}>
        {time}
      </span>
    );
  }
  return (
    <button
      type="button"
      onClick={() => onSeek(seconds)}
      dir="ltr"
      aria-label={`Jump to ${time}`}
      className={cn(chipClass, 'cursor-pointer transition-colors hover:bg-[var(--vie-accent)]/20')}
    >
      <Play className="h-3 w-3 fill-current" aria-hidden="true" />
      {time}
    </button>
  );
}

interface QuizExplanationProps {
  text: string;
  onSeek?: (seconds: number) => void;
}

/** Explanation paragraph with every `(m:ss)` reference rendered as a seek chip. */
export function QuizExplanation({ text, onSeek }: QuizExplanationProps) {
  return (
    <p className="text-sm leading-relaxed text-muted-foreground">
      {splitTimeRefs(text).map((segment, i) =>
        segment.kind === 'text' ? (
          <span key={i}>{segment.text}</span>
        ) : (
          <QuizSeekChip key={i} seconds={segment.seconds} onSeek={onSeek} />
        ),
      )}
    </p>
  );
}
