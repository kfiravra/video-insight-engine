import { SeekChip } from '@/components/vie';
import { cn } from '@/lib/utils';
import { splitTimeRefs } from '@/features/video-output/lib/quiz-time-refs';

interface QuizExplanationProps {
  text: string;
  /** Where the transcript gives the answer, in seconds (output-quality 1.13) —
   *  range-checked by the enrichment, so it wins over a `(m:ss)` in the text that
   *  sits further than the tolerance from it. Rendered as a trailing chip only when
   *  the text cites no `(m:ss)` itself, so a question never shows the same time twice. */
  timestamp?: number | null;
  /** Append `timestamp` as a trailing chip when the text cites no time (default).
   *  Off where the caller offers its own control for it (Study's "Watch this part"). */
  stampChip?: boolean;
  onSeek?: (seconds: number) => void;
  /** Paragraph classes on top of the muted default (Study mode's info tint). */
  className?: string;
}

const CHIP_CLASS = 'ms-1 px-1.5 align-baseline';

/** Seconds a cited `(m:ss)` may sit from the checked timestamp and still be the same
 *  moment — the enrichment's `QUIZ_TIMESTAMP_TOLERANCE_S`. */
const STAMP_TOLERANCE_S = 10;

/** Where a cited time seeks: itself when no stamp was checked or it agrees with the
 *  checked one; else the checked stamp — the text's time is the model's word, unchecked
 *  (a past-the-end `(m:ss)` never wins over an in-range stamp). */
function seekTarget(cited: number, checked: number | null | undefined): number {
  if (checked == null || Math.abs(cited - checked) <= STAMP_TOLERANCE_S) return cited;
  return checked;
}

/** Explanation paragraph with every `(m:ss)` reference rendered as a seek chip. */
export function QuizExplanation({ text, timestamp, stampChip = true, onSeek, className }: QuizExplanationProps) {
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
            seconds={seekTarget(segment.seconds, timestamp)}
            onSeek={onSeek}
            // No leading margin when the chip opens the paragraph.
            className={i === 0 ? 'px-1.5 align-baseline' : CHIP_CLASS}
          />
        ),
      )}
      {stampChip && timestamp != null && !citesTime && (
        <SeekChip seconds={timestamp} onSeek={onSeek} className={CHIP_CLASS} />
      )}
    </p>
  );
}
