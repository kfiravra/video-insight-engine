import { memo, useCallback, useState } from 'react';
import { cn } from '@/lib/utils';
import { useLabels } from '@/lib/i18n';
import { CollapsibleSection } from './CollapsibleSection';

interface HeroWorthItProps {
  /** Who gets what from watching (the hero rewrite, ≤ 20 words). */
  worthItFor?: string;
  /** What someone might come for that the video does not contain (≤ 20 words). */
  skipIf?: string;
  /** A review's stated Best for / Not for: each replaces its hero line when non-empty (4.3). */
  bestFor?: string[];
  notFor?: string[];
  /** Desktop (4.6a): each line on one line, label run in, the rest truncated. */
  oneLine?: boolean;
  className?: string;
}

interface WorthLine {
  key: string;
  label: string;
  items: string[];
  dot: string;
}

function stated(items: string[] | undefined): string[] {
  return (items ?? []).map((item) => item.trim()).filter((item) => item.length > 0);
}

function lineOf(text: string | undefined): string[] {
  return stated(text ? [text] : undefined);
}

function ToneDot({ dot }: { dot: string }) {
  return <span aria-hidden="true" className={cn('h-1.5 w-1.5 rounded-full shrink-0', dot)} />;
}

/** Desktop: one line each, the label run in and the text cut with an ellipsis
 *  (the whole line stays in the DOM for screen readers and in the tooltip);
 *  a list reads as one "a · b" line. */
function OneLineWorth({ lines, className }: { lines: WorthLine[]; className?: string }) {
  return (
    <dl className={cn('space-y-0.5', className)}>
      {lines.map((line) => {
        const text = line.items.join(' · ');
        return (
          <div key={line.key} className="flex min-w-0 items-center gap-2.5">
            <dt className="type-eyebrow flex shrink-0 items-center gap-2">
              <ToneDot dot={line.dot} />
              {line.label}
            </dt>
            <dd title={text} className="min-w-0 truncate text-sm leading-snug text-foreground/80">
              {text}
            </dd>
          </div>
        );
      })}
    </dl>
  );
}

/** "Worth it for / Skip if" — the hero's answer to "should I watch this?"
 *  (C2, D18): one line each on desktop, stacked in full on phones. On a review
 *  the reviewer's own Best for / Not for lists take each line's place. The tone
 *  rides on a dot so the labels keep the eyebrow's contrast in both themes. */
export const HeroWorthIt = memo(function HeroWorthIt({ worthItFor, skipIf, bestFor, notFor, oneLine, className }: HeroWorthItProps) {
  const t = useLabels();
  const best = stated(bestFor);
  const not = stated(notFor);
  const lines: WorthLine[] = [
    best.length > 0
      ? { key: 'best', label: t.bestFor, items: best, dot: 'bg-success' }
      : { key: 'worth', label: t.worthItFor, items: lineOf(worthItFor), dot: 'bg-success' },
    not.length > 0
      ? { key: 'not', label: t.notFor, items: not, dot: 'bg-warning' }
      : { key: 'skip', label: t.skipIf, items: lineOf(skipIf), dot: 'bg-warning' },
  ].filter((line) => line.items.length > 0);
  if (lines.length === 0) return null;
  if (oneLine) return <OneLineWorth lines={lines} className={className} />;

  return (
    <dl className={cn('grid grid-cols-1 gap-x-7 gap-y-3 sm:grid-cols-2', className)}>
      {lines.map((line) => (
        <div key={line.key} className="space-y-1.5">
          <dt className="type-eyebrow flex items-center gap-2">
            <ToneDot dot={line.dot} />
            {line.label}
          </dt>
          <dd className="max-w-[68ch] text-sm leading-relaxed text-foreground/80 text-pretty">
            {line.items.length === 1 ? (
              line.items[0]
            ) : (
              <ul className="space-y-1">
                {line.items.map((item, i) => (
                  <li key={i} className="flex items-start gap-2">
                    <span aria-hidden="true" className="mt-[0.6rem] h-1 w-1 rounded-full bg-foreground/30 shrink-0" />
                    <span>{item}</span>
                  </li>
                ))}
              </ul>
            )}
          </dd>
        </div>
      ))}
    </dl>
  );
});

interface HeroWorthItRowProps {
  worthItFor?: string;
  skipIf?: string;
}

/** Phones: the worth-it lines behind a third collapsed "Worth it?" row under
 *  Brief and Key takeaways. Open, they cost ≈ 150 px and push the tab panel
 *  past 60 % of the first screen (output-quality 4.4). */
export const HeroWorthItRow = memo(function HeroWorthItRow({ worthItFor, skipIf }: HeroWorthItRowProps) {
  const t = useLabels();
  const [open, setOpen] = useState(false);
  const toggle = useCallback(() => setOpen((prev) => !prev), []);
  return (
    <CollapsibleSection baseId="vie-hero-worth" label={t.worthIt} open={open} onToggle={toggle}>
      <HeroWorthIt worthItFor={worthItFor} skipIf={skipIf} />
    </CollapsibleSection>
  );
});
