import { memo, useCallback, useId, useState } from 'react';
import { useLabels } from '@/lib/i18n';
import { Button } from '@/components/ui/button';
import { CollapsibleSection } from './CollapsibleSection';

// ≈ five lines at the brief's 68ch measure; shorter briefs never clamp.
const BRIEF_CLAMP_CHARS = 360;

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

/** The written overview every video has, under the tldr: a collapsible
 *  "Brief" that opens on desktop and starts collapsed on phones, and shows
 *  five lines until the reader asks for all (R11). */
export const HeroBrief = memo(function HeroBrief({ masterSummary, keyQuestion, isPhone }: HeroBriefProps) {
  const t = useLabels();
  // A manual toggle owns the open state from then on.
  const [userOpen, setUserOpen] = useState<boolean | null>(null);
  const open = userOpen ?? !isPhone;
  const toggle = useCallback(() => {
    setUserOpen((prev) => !(prev ?? !isPhone));
  }, [isPhone]);
  // Open ≠ unrolled: the brief shows five lines until the reader asks for all.
  const [expanded, setExpanded] = useState(false);
  const textId = useId();

  const paragraphs = briefParagraphs(masterSummary);
  if (paragraphs.length === 0) return null;
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
});
