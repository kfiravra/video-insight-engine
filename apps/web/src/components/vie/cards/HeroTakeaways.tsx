import { memo, useCallback, useId, useState } from 'react';
import { cn } from '@/lib/utils';
import { useLabels } from '@/lib/i18n';
import { useVideoPlayer } from '@/features/video-output/contexts/VideoPlayerContext';
import { Button } from '@/components/ui/button';
import { SeekChip } from '@/components/vie/data/SeekChip';
import { FadeIn } from '@/components/vie/feedback/FadeIn';
import { CollapsibleSection } from './CollapsibleSection';
import { splitTakeawayStamps } from './takeaway-stamps';

// The hero must leave the first screen to the tab panel (audit §2 #15; on
// desktop ≤ 420 px at 1280 × 800, output-quality 4.6a): three takeaways show,
// the rest behind "+N".
const TAKEAWAY_CAP = 3;

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

interface TakeawayItemProps {
  takeaway: string;
  index: number;
  onSeek: (seconds: number) => void;
}

/** One takeaway: the claim, then a ▶ chip for each moment it cites — the
 *  `[m:ss]` marker seeks the player and never shows as text. */
function TakeawayItem({ takeaway, index, onSeek }: TakeawayItemProps) {
  const { text, stamps } = splitTakeawayStamps(takeaway);
  return (
    // The <li> is the animated element itself: a FadeIn <div> between <ul> and <li>
    // breaks the list semantics (axe `list` / `listitem`).
    <li
      className="animate-fade-up flex items-start gap-2.5 text-[0.8125rem] leading-snug text-foreground/75"
      style={{ animationDelay: `${index * 60}ms` }}
    >
      <span aria-hidden="true" className="mt-[0.4rem] h-1 w-1 rounded-full bg-foreground/30 shrink-0" />
      <span>
        {text}
        {stamps.map((seconds, i) => (
          <SeekChip key={i} seconds={seconds} onSeek={onSeek} className="ms-1.5 align-baseline md:py-0" />
        ))}
      </span>
    </li>
  );
}

interface MoreToggleProps {
  hidden: number;
  showAll: boolean;
  listId: string;
  onToggle: () => void;
  className?: string;
}

/** "+N · More takeaways" / "Hide": in the row on desktop, under the list on phones. */
function MoreToggle({ hidden, showAll, listId, onToggle, className }: MoreToggleProps) {
  const t = useLabels();
  return (
    <Button
      variant="ghost"
      size="bare"
      onClick={onToggle}
      aria-expanded={showAll}
      aria-controls={listId}
      aria-label={showAll ? t.hideTakeaways : undefined}
      className={cn('text-xs font-medium text-[var(--vie-accent)] hover:underline', className)}
    >
      {showAll ? t.hide : `+${hidden} · ${t.moreTakeaways}`}
    </Button>
  );
}

interface TakeawaysOpen {
  open: boolean;
  setUserOpen: (open: boolean) => void;
}

/** Open on desktop until the reader first engages the player (a one-shot
 *  auto-collapse, adjusted during render; while streaming it waits until the
 *  stream settles so content isn't yanked mid-arrival), collapsed on phones.
 *  `userOpen` null = untouched; a manual toggle takes permanent ownership. */
function useTakeawaysOpen(isStreaming: boolean, isPhone: boolean): TakeawaysOpen {
  const { hasEngaged } = useVideoPlayer();
  const [userOpen, setUserOpen] = useState<boolean | null>(null);
  const [autoCollapsed, setAutoCollapsed] = useState(false);
  if (!autoCollapsed && userOpen === null && hasEngaged && !isStreaming) {
    setAutoCollapsed(true);
  }
  return { open: userOpen ?? (!autoCollapsed && !isPhone), setUserOpen };
}

/** Key takeaways — open on load (they ARE the landing summary): three in one
 *  row on a desktop-wide hero, "+N" in the row for the rest; after the user
 *  engages the player they auto-collapse (once) into the slim "Key takeaways ·
 *  N" disclosure, which stays as the reopen affordance. Phones start
 *  collapsed, "+N" under the list. No persistence: every load is a fresh
 *  read-first arrival. */
export const HeroTakeaways = memo(function HeroTakeaways({ takeaways, isStreaming, isPhone }: HeroTakeawaysProps) {
  const t = useLabels();
  const { seekTo } = useVideoPlayer();
  const { open, setUserOpen } = useTakeawaysOpen(isStreaming, isPhone);
  // A seek from a takeaway chip is the reader using the list, so it stays open
  // for the next chip — the one-shot auto-collapse is for engagement elsewhere.
  const handleSeek = useCallback(
    (seconds: number) => {
      setUserOpen(true);
      seekTo(seconds);
    },
    [seekTo, setUserOpen],
  );
  const toggle = useCallback(() => setUserOpen(!open), [open, setUserOpen]);
  const [showAll, setShowAll] = useState(false);
  // "+N" in a collapsed desktop row opens the row as well.
  const toggleAll = useCallback(() => {
    if (!showAll && !open) setUserOpen(true);
    setShowAll(!showAll);
  }, [showAll, open, setUserOpen]);
  const listId = useId();
  const visible = showAll ? takeaways : takeaways.slice(0, TAKEAWAY_CAP);
  const hidden = takeaways.length - visible.length;

  if (visible.length === 0) return <TakeawaysSkeleton label={t.keyTakeaways} />;
  const more = (hidden > 0 || showAll) && (
    <MoreToggle hidden={hidden} showAll={showAll} listId={listId} onToggle={toggleAll} className={isPhone ? 'mt-2.5' : 'shrink-0'} />
  );

  return (
    <FadeIn>
      <CollapsibleSection
        baseId="vie-hero-takeaways"
        label={`${t.keyTakeaways} · ${takeaways.length}`}
        action={!isPhone && more ? more : undefined}
        open={open}
        onToggle={toggle}
      >
        {/* Uniform fine dot marker (not per-item emoji) keeps the columns
            start-aligned and reads as a crisp, ordered list. Tight 13px type
            sits clearly below the brief in hierarchy. */}
        <div className="@container">
          <ul id={listId} className="grid grid-cols-1 gap-x-7 gap-y-2 sm:grid-cols-2 @2xl:grid-cols-3">
            {visible.map((takeaway, i) => (
              <TakeawayItem key={i} takeaway={takeaway} index={i} onSeek={handleSeek} />
            ))}
          </ul>
        </div>
        {isPhone && more}
      </CollapsibleSection>
    </FadeIn>
  );
});
