import { SeekChip } from '@/components/vie';
import { cn } from '@/lib/utils';
import { splitTimeRefs } from '@/features/video-output/lib/quiz-time-refs';
import { QuoteLine } from './QuoteLine';

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
  /** The speaker's words that settle the answer (quiz v2, output-quality 2.1b): one
   *  quiet line under the explanation, carrying the timestamp's chip. */
  quote?: string | null;
  /** The quote's English on a non-English video (D12), shown beneath it. */
  quoteEn?: string | null;
  /** The quote is the English rendering of non-English speech (2.0 d, D12): prose,
   *  never quote styling. */
  quoteTranslated?: boolean;
  onSeek?: (seconds: number) => void;
  /** Paragraph classes on top of the muted default (Study mode's info tint). */
  className?: string;
}

interface QuizEvidenceProps {
  quote: string;
  quoteEn?: string | null;
  translated?: boolean;
  /** The chip's time; none when the explanation already cites one. */
  seconds: number | null;
  onSeek?: (seconds: number) => void;
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

/** The answer's evidence: the speaker's line as every verbatim line renders
 *  (`QuoteLine`: its own direction, so a Hebrew line reads right-to-left on the
 *  English page, with its English beneath — D12); a translated line reads as
 *  prose (2.0 d). The chip follows on its own line, left-to-right. */
function QuizEvidence({ quote, quoteEn, translated, seconds, onSeek }: QuizEvidenceProps) {
  return (
    <div className="space-y-1">
      <QuoteLine quote={quote} quoteEn={quoteEn} translated={translated} size="xs" />
      {seconds != null && (
        <p className={translated ? undefined : 'ps-3'}>
          <SeekChip seconds={seconds} onSeek={onSeek} className="px-1.5" />
        </p>
      )}
    </div>
  );
}

/** Explanation paragraph with every `(m:ss)` reference rendered as a seek chip, and the
 *  evidence quote under it when the question carries one. */
export function QuizExplanation({
  text,
  timestamp,
  stampChip = true,
  quote,
  quoteEn,
  quoteTranslated,
  onSeek,
  className,
}: QuizExplanationProps) {
  const segments = splitTimeRefs(text);
  const citesTime = segments.some((segment) => segment.kind === 'seek');
  const evidence = quote?.trim();
  // The timestamp's chip shows once: on the evidence line when there is one.
  const stamp = stampChip && timestamp != null && !citesTime ? timestamp : null;
  const paragraph = (
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
      {stamp != null && !evidence && <SeekChip seconds={stamp} onSeek={onSeek} className={CHIP_CLASS} />}
    </p>
  );
  if (!evidence) return paragraph;
  return (
    <div className="space-y-1.5">
      {paragraph}
      <QuizEvidence quote={evidence} quoteEn={quoteEn} translated={quoteTranslated} seconds={stamp} onSeek={onSeek} />
    </div>
  );
}
