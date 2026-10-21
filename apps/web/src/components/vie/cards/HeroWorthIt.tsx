import { memo } from 'react';
import { cn } from '@/lib/utils';
import { useLabels } from '@/lib/i18n';

interface HeroWorthItProps {
  /** Who gets what from watching (the hero rewrite, ≤ 20 words). */
  worthItFor?: string;
  /** What someone might come for that the video does not contain (≤ 20 words). */
  skipIf?: string;
  className?: string;
}

/** "Worth it for / Skip if" — the hero's two-line answer to "should I watch
 *  this?" (C2, D18): side by side on desktop, stacked on phones. The tone
 *  rides on a dot so the labels keep the eyebrow's contrast in both themes. */
export const HeroWorthIt = memo(function HeroWorthIt({ worthItFor, skipIf, className }: HeroWorthItProps) {
  const t = useLabels();
  const lines = [
    { key: 'worth', label: t.worthItFor, text: worthItFor?.trim(), dot: 'bg-success' },
    { key: 'skip', label: t.skipIf, text: skipIf?.trim(), dot: 'bg-warning' },
  ].filter((line) => line.text);
  if (lines.length === 0) return null;

  return (
    <dl className={cn('grid grid-cols-1 gap-x-7 gap-y-3 sm:grid-cols-2', className)}>
      {lines.map((line) => (
        <div key={line.key} className="space-y-1.5">
          <dt className="type-eyebrow flex items-center gap-2">
            <span aria-hidden="true" className={cn('h-1.5 w-1.5 rounded-full shrink-0', line.dot)} />
            {line.label}
          </dt>
          <dd className="max-w-[68ch] text-sm leading-relaxed text-foreground/80 text-pretty">{line.text}</dd>
        </div>
      ))}
    </dl>
  );
});
