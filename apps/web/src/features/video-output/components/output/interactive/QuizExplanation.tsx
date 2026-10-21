import { SeekChip } from '@/components/vie';
import { cn } from '@/lib/utils';
import { splitTimeRefs } from '@/features/video-output/lib/quiz-time-refs';

interface QuizExplanationProps {
  text: string;
  /** Where the transcript gives the answer, in seconds (output-quality 1.13).
   *  Rendered as a trailing chip only when the text cites no `(m:ss)` itself,
   *  so a question never shows the same time twice. */
  timestamp?: number;
  onSeek?: (seconds: number) => void;
  /** Paragraph classes on top of the muted default (Study mode's info tint). */
  className?: string;
}

const CHIP_CLASS = 'ms-1 px-1.5 align-baseline';

/** Explanation paragraph with every `(m:ss)` reference rendered as a seek chip. */
export function QuizExplanation({ text, timestamp, onSeek, className }: QuizExplanationProps) {
  const segments = splitTimeRefs(text);
  const citesTime = segments.some((segment) => segment.kind === 'seek');
  return (
    <p className={cn('text-sm leading-relaxed text-muted-foreground', className)}>
      {segments.map((segment, i) =>
        segment.kind === 'text' ? (
          <span key={i}>{segment.text}</span>
        ) : (
          <SeekChip
            key={i}
            seconds={segment.seconds}
            onSeek={onSeek}
            // No leading margin when the chip opens the paragraph.
            className={i === 0 ? 'px-1.5 align-baseline' : CHIP_CLASS}
          />
        ),
      )}
      {timestamp != null && !citesTime && (
        <SeekChip seconds={timestamp} onSeek={onSeek} className={CHIP_CLASS} />
      )}
    </p>
  );
}
