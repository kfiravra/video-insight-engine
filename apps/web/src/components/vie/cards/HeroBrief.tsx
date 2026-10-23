import { memo, useCallback, useId, useState } from 'react';
import { cn } from '@/lib/utils';
import { useLabels } from '@/lib/i18n';
import { Button } from '@/components/ui/button';
import { CollapsibleSection } from './CollapsibleSection';

// Phones: ≈ five lines at the brief's 68ch measure once the row is opened.
const BRIEF_CLAMP_CHARS = 360;
// Desktop: past this the brief runs over two lines at its 96ch measure on any
// desktop width, so it clamps; a shorter one shows whole (never a clamp
// without its "more").
const BRIEF_TEASER_CHARS = 240;

/** Paragraphs of the brief — the synthesis separates them with line breaks. */
function briefParagraphs(text: string): string[] {
  return text
    .split(/\n+/)
    .map((p) => p.trim())
    .filter((p) => p.length > 0);
}

interface HeroBriefProps {
  /** The long-form brief (synthesis.masterSummary). */
  masterSummary: string;
  /** The question the video answers — the first line when present. */
  keyQuestion?: string;
  /** Phones start collapsed: the brief would push the strip off the first screen. */
  isPhone: boolean;
}

interface BriefBodyProps {
  paragraphs: string[];
  keyQuestion?: string;
}

/** Desktop: the brief as two lines under the tldr, led by a run-in "Brief"
 *  label, with "more" at the end of the second line (output-quality 4.6a: the
 *  hero stays ≤ 420 px at 1280 × 800). The clamp is visual only — screen
 *  readers get the whole brief either way. */
function BriefTeaser({ paragraphs, keyQuestion }: BriefBodyProps) {
  const t = useLabels();
  const [expanded, setExpanded] = useState(false);
  const textId = useId();
  const labelId = useId();
  const joined = paragraphs.join(' ');
  const clamps = t.brief.length + (keyQuestion?.length ?? 0) + joined.length > BRIEF_TEASER_CHARS;
  const folded = clamps && !expanded;
  const [first, ...rest] = folded ? [joined] : paragraphs;
  return (
    <section aria-labelledby={labelId} className="mt-1.5 max-w-[96ch] text-sm leading-relaxed text-foreground/80 text-pretty">
      <div className="relative">
        <div id={textId} className="space-y-2.5">
          <p className={cn(folded && 'line-clamp-2')}>
            <span id={labelId} className="type-eyebrow me-2.5">
              {t.brief}
            </span>
            {keyQuestion && <span className="font-medium text-foreground/90">{keyQuestion} </span>}
            <span>{first}</span>
          </p>
          {rest.map((paragraph, i) => (
            <p key={i}>{paragraph}</p>
          ))}
        </div>
        {/* Folded, the toggle sits over the end of the second line, fading
            the clipped words out (left-to-right in RTL, where `end-0` puts it
            on the left); unrolled, it follows the text. One element, so focus
            stays on it. */}
        {clamps && (
          <Button
            variant="link"
            size="bare"
            onClick={() => setExpanded(!expanded)}
            aria-expanded={expanded}
            aria-controls={textId}
            aria-label={expanded ? t.hideBrief : t.moreBrief}
            className={cn(
              'text-xs font-medium text-[var(--vie-accent)] hover:underline',
              expanded
                ? 'mt-1.5'
                : 'absolute bottom-0 end-0 bg-gradient-to-l from-card from-60% to-transparent ps-10 leading-relaxed rtl:bg-gradient-to-r',
            )}
          >
            {expanded ? t.hide : t.more}
          </Button>
        )}
      </div>
    </section>
  );
}

/** Phones: a collapsed "Brief" row; opened, five lines until the reader asks for all. */
function BriefRow({ paragraphs, keyQuestion }: BriefBodyProps) {
  const t = useLabels();
  const [open, setOpen] = useState(false);
  const toggle = useCallback(() => setOpen((prev) => !prev), []);
  // Open ≠ unrolled: the brief shows five lines until the reader asks for all.
  const [expanded, setExpanded] = useState(false);
  const textId = useId();
  const joined = paragraphs.join(' ');
  // The key question shares the five-line budget.
  const clamps = joined.length + (keyQuestion?.length ?? 0) > BRIEF_CLAMP_CHARS;
  return (
    <div className="mt-4">
      <CollapsibleSection baseId="vie-hero-brief" label={t.brief} open={open} onToggle={toggle}>
        <div className="max-w-[68ch] space-y-2.5 text-sm leading-relaxed text-foreground/80 text-pretty">
          {keyQuestion && <p className="font-medium text-foreground/90">{keyQuestion}</p>}
          <div id={textId} className="space-y-2.5">
            {clamps && !expanded ? (
              <p className="line-clamp-5">{joined}</p>
            ) : (
              paragraphs.map((paragraph, i) => <p key={i}>{paragraph}</p>)
            )}
          </div>
          {clamps && (
            <Button
              variant="ghost"
              size="bare"
              onClick={() => setExpanded(!expanded)}
              aria-expanded={expanded}
              aria-controls={textId}
              aria-label={expanded ? t.hideBrief : undefined}
              className="text-xs font-medium text-[var(--vie-accent)] hover:underline"
            >
              {expanded ? t.hide : t.showAll}
            </Button>
          )}
        </div>
      </CollapsibleSection>
    </div>
  );
}

/** The written overview every video has, under the tldr (R11): two lines and
 *  "more" on desktop, a collapsed "Brief" row on phones. */
export const HeroBrief = memo(function HeroBrief({ masterSummary, keyQuestion, isPhone }: HeroBriefProps) {
  const paragraphs = briefParagraphs(masterSummary);
  if (paragraphs.length === 0) return null;
  return isPhone ? (
    <BriefRow paragraphs={paragraphs} keyQuestion={keyQuestion} />
  ) : (
    <BriefTeaser paragraphs={paragraphs} keyQuestion={keyQuestion} />
  );
});
