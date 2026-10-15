import { memo, useCallback, useState } from 'react';
import { useLabels } from '@/lib/i18n';
import { useVideoPlayer } from '@/features/video-output/contexts/VideoPlayerContext';
import { Button } from '@/components/ui/button';
import { FadeIn } from '@/components/vie/feedback/FadeIn';
import { CollapsibleSection } from './CollapsibleSection';

// Mobile rule (audit §2 #15): the hero must leave the first viewport to the
// tab panel — three takeaways on phones, six on desktop, the rest behind "+N".
const TAKEAWAY_CAP_DESKTOP = 6;
const TAKEAWAY_CAP_PHONE = 3;

interface HeroTakeawaysProps {
  takeaways: string[];
  isStreaming: boolean;
  isPhone: boolean;
}

/** Streaming placeholder — the count is unknown, so a static skeleton grid
 *  stands in for the toggle. */
function TakeawaysSkeleton({ label }: { label: string }) {
  return (
    <div className="space-y-3" aria-hidden="true">
      <div className="flex items-center gap-2.5">
        <span className="h-px w-5 bg-border" />
        <span className="type-eyebrow text-muted-foreground">{label}</span>
        <span className="h-px flex-1 bg-border/60" />
      </div>
      <ul className="grid grid-cols-1 gap-x-7 gap-y-2 sm:grid-cols-2">
        {Array.from({ length: 4 }, (_, i) => (
          <li key={i} className="flex items-start gap-2.5">
            <span className="mt-[0.4rem] h-1 w-1 rounded-full bg-muted/40 shrink-0" />
            <span className="h-3 rounded bg-muted/30 animate-pulse" style={{ width: `${88 - (i % 2) * 16}%` }} />
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Key takeaways — open on load as a dense two-column grid (they ARE the
 *  landing summary); after the user engages the player they auto-collapse
 *  (once) into the slim "Key takeaways · N" disclosure, which stays as the
 *  reopen affordance. Phones start collapsed. No persistence: every load is a
 *  fresh read-first arrival. */
export const HeroTakeaways = memo(function HeroTakeaways({ takeaways, isStreaming, isPhone }: HeroTakeawaysProps) {
  const t = useLabels();
  const { hasEngaged } = useVideoPlayer();
  // `userOpen` null = untouched; a manual toggle takes permanent ownership.
  const [userOpen, setUserOpen] = useState<boolean | null>(null);
  const [autoCollapsed, setAutoCollapsed] = useState(false);
  // One-shot auto-collapse on first engagement (seek or player open), adjusted
  // during render. While streaming it waits until the stream settles so
  // content isn't yanked mid-arrival.
  if (!autoCollapsed && userOpen === null && hasEngaged && !isStreaming) {
    setAutoCollapsed(true);
  }
  const open = userOpen ?? (!autoCollapsed && !isPhone);
  const toggle = useCallback(() => {
    setUserOpen(!open);
  }, [open]);

  const [showAll, setShowAll] = useState(false);
  const cap = isPhone ? TAKEAWAY_CAP_PHONE : TAKEAWAY_CAP_DESKTOP;
  const visible = showAll ? takeaways : takeaways.slice(0, cap);
  const hidden = takeaways.length - visible.length;

  if (visible.length === 0) return <TakeawaysSkeleton label={t.keyTakeaways} />;

  return (
    <FadeIn>
      <CollapsibleSection
        baseId="vie-hero-takeaways"
        label={`${t.keyTakeaways} · ${takeaways.length}`}
        open={open}
        onToggle={toggle}
      >
        {/* Uniform fine dot marker (not per-item emoji) keeps both columns
            left-aligned and reads as a crisp, ordered list. Tight 13px type
            sits clearly below the brief in hierarchy. */}
        <ul className="grid grid-cols-1 gap-x-7 gap-y-2 sm:grid-cols-2">
          {visible.map((takeaway, i) => (
            // The <li> is the animated element itself: a FadeIn <div> between <ul> and <li>
            // breaks the list semantics (axe `list` / `listitem`).
            <li
              key={i}
              className="animate-fade-up flex items-start gap-2.5 text-[0.8125rem] leading-snug text-foreground/75"
              style={{ animationDelay: `${i * 60}ms` }}
            >
              <span aria-hidden="true" className="mt-[0.4rem] h-1 w-1 rounded-full bg-foreground/30 shrink-0" />
              <span>{takeaway}</span>
            </li>
          ))}
        </ul>
        {(hidden > 0 || showAll) && (
          <Button
            variant="ghost"
            size="bare"
            onClick={() => setShowAll(!showAll)}
            aria-label={showAll ? t.hideTakeaways : undefined}
            className="mt-2.5 text-xs font-medium text-[var(--vie-accent)] hover:underline"
          >
            {showAll ? t.hide : `+${hidden} · ${t.moreTakeaways}`}
          </Button>
        )}
      </CollapsibleSection>
    </FadeIn>
  );
});
