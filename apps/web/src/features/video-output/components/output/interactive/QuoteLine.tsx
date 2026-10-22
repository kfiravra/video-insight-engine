import { memo } from 'react';
import { cn } from '@/lib/utils';

interface QuoteLineProps {
  /** The speaker's words, verbatim, in the language they spoke. */
  quote?: string | null;
  /** English rendering shown beneath a verbatim quote. */
  quoteEn?: string | null;
  /** The quote is already a rendering of non-English speech (2.0 d): prose, never a quotation. */
  translated?: boolean | null;
  size?: 'sm' | 'xs';
  className?: string;
}

const SIZE: Record<NonNullable<QuoteLineProps['size']>, string> = {
  sm: 'text-sm',
  xs: 'text-xs',
};

/**
 * The speaker's own line, set apart (R10). The verbatim quote reads in its own
 * direction (`dir="auto"`, so Hebrew sits right-to-left on an English page) with
 * its English beneath in muted text. A translated quote is not the speaker's
 * words, so it is prose — the English rendering when one exists. Renders nothing
 * without a quote.
 */
export const QuoteLine = memo(function QuoteLine({
  quote,
  quoteEn,
  translated,
  size = 'sm',
  className,
}: QuoteLineProps) {
  const line = quote?.trim();
  if (!line) return null;
  const english = quoteEn?.trim();

  if (translated) {
    return (
      <p className={cn(SIZE[size], 'leading-relaxed text-muted-foreground', className)}>
        {english || line}
      </p>
    );
  }
  return (
    <div className={cn('space-y-0.5', className)}>
      <blockquote
        dir="auto"
        className={cn(
          'border-s-2 border-[var(--vie-accent)]/40 ps-2.5 italic leading-relaxed text-foreground/85',
          SIZE[size],
        )}
      >
        {line}
      </blockquote>
      {english && english !== line && (
        <p lang="en" dir="ltr" className={cn('ps-2.5 text-start leading-relaxed text-muted-foreground', SIZE[size])}>
          {english}
        </p>
      )}
    </div>
  );
});
