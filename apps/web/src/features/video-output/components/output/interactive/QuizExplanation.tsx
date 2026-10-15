import { SeekChip } from '@/components/vie';
import { splitTimeRefs } from '@/features/video-output/lib/quiz-time-refs';

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
          <SeekChip
            key={i}
            seconds={segment.seconds}
            onSeek={onSeek}
            // No leading margin when the chip opens the paragraph.
            className={i === 0 ? 'px-1.5 align-baseline' : 'ms-1 px-1.5 align-baseline'}
          />
        ),
      )}
    </p>
  );
}
