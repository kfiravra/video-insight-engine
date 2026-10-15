import { memo, useCallback, useEffect, useRef, useState, type RefObject } from 'react';
import { motion } from 'motion/react';
import { Check } from 'lucide-react';
import { cn } from '@/lib/utils';
import { springs } from '@/lib/motion';
import { stripLeadingEmoji } from '@/lib/string-utils';
import { useLabels } from '@/lib/i18n';
import { useMediaQuery } from '@/hooks/use-media-query';
import { useVideoPlayer } from '@/features/video-output/contexts/VideoPlayerContext';
import { YouTubePlayer } from '@/components/videos/YouTubePlayer';
import { Button } from '@/components/ui/button';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { FadeIn } from '@/components/vie';
import { CollapsibleSection } from './CollapsibleSection';
import { PendingTabPill } from './PendingTabPill';
import { PlayCloseGlyph } from './PlayCloseGlyph';
import { DOMAIN_META, trueCountLabel } from './domain-meta';
import type { TabDefinition, ContentTag } from '@vie/types';

interface VideoHeroTab extends TabDefinition {
  preview?: string;
  /** An early placeholder the stream will replace — marked in the strip. */
  provisional?: boolean;
  /** The number of items the tab really ships (inferItemCount); replaces the
   *  planner's guessed count in the label. */
  itemCount?: number;
}

interface VideoHeroProps {
  title: string;
  creator?: string;
  duration?: number | null;
  tldr?: string;
  keyTakeaways?: string[];
  /** The long-form brief (synthesis.masterSummary). Rendered as a collapsible
   *  "Brief" under the tldr — open on desktop, collapsed on phones (R11). */
  masterSummary?: string;
  /** The question the video answers (extraction keyQuestion). First line of
   *  the brief when present. */
  keyQuestion?: string;
  youtubeId?: string;
  tabs?: VideoHeroTab[];
  activeTabId?: string;
  onTabSelect?: (id: string) => void;
  completedTabs?: Set<string>;
  domainGradient?: string;
  /** Primary content tag — drives the identity-strip domain emoji + label and
   *  the fallback emoji selection on takeaways that don't match a keyword. */
  primaryTag?: ContentTag;
  /** Optional difficulty / depth label shown in the identity strip ("Beginner",
   *  "Advanced", etc). */
  level?: string;
  /** Explicit streaming flag. When omitted, falls back to inferring from a
   *  missing tldr (legacy behavior) — pass it wherever the caller knows the
   *  real stream state so a completed video with an empty brief doesn't
   *  render skeletons forever. */
  isStreaming?: boolean;
  /** Planned tabs announced by the stream that haven't landed yet. Rendered
   *  as inert pills while streaming: before any tab exists, in the exact
   *  geometry of the final strip; once tabs exist, after the live ones. */
  pendingTabs?: { id: string; label: string; emoji: string }[];
  className?: string;
}

function formatDuration(seconds: number | null | undefined): string {
  if (!seconds || seconds <= 0) return '';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  return `${m}:${String(s).padStart(2, '0')}`;
}

/** Paragraphs of the brief — the synthesis writes it with blank-line breaks. */
function briefParagraphs(text: string): string[] {
  return text
    .split(/\n+/)
    .map((p) => p.trim())
    .filter((p) => p.length > 0);
}

const PHONE_QUERY = '(max-width: 767px)';
// Mobile rule (audit §2 #15): the hero must leave the first viewport to the
// tab panel — three takeaways on phones, six on desktop, the rest behind
// "+N"; the brief shows five lines until expanded.
const TAKEAWAY_CAP_DESKTOP = 6;
const TAKEAWAY_CAP_PHONE = 3;
// ≈ five lines at the brief's 68ch measure; shorter briefs never clamp.
const BRIEF_CLAMP_CHARS = 360;

/** Tabs scrolled past the strip's visible end — the "+N" overflow count,
 *  re-measured on scroll and resize. */
function useStripOverflow(stripRef: RefObject<HTMLDivElement | null>, tabCount: number): number {
  const [overflow, setOverflow] = useState(0);
  useEffect(() => {
    const strip = stripRef.current;
    if (!strip) return;
    const measure = (): void => {
      const edge = strip.getBoundingClientRect().right;
      let hidden = 0;
      for (const tab of strip.querySelectorAll<HTMLElement>('[role="tab"]')) {
        if (tab.getBoundingClientRect().right > edge + 1) hidden += 1;
      }
      setOverflow(hidden);
    };
    measure();
    strip.addEventListener('scroll', measure, { passive: true });
    const observer = new ResizeObserver(measure);
    observer.observe(strip);
    return () => {
      strip.removeEventListener('scroll', measure);
      observer.disconnect();
    };
  }, [stripRef, tabCount]);
  return overflow;
}

export const VideoHero = memo(function VideoHero({
  title,
  creator,
  duration,
  tldr,
  keyTakeaways,
  masterSummary,
  keyQuestion,
  youtubeId,
  tabs,
  activeTabId,
  onTabSelect,
  completedTabs,
  domainGradient,
  primaryTag,
  level,
  isStreaming: isStreamingProp,
  pendingTabs,
  className,
}: VideoHeroProps) {
  const t = useLabels();
  const isPhone = useMediaQuery(PHONE_QUERY);
  const { togglePlayer, isPlayerOpen, playerRef, hasEngaged, registerPlayerAnchor } = useVideoPlayer();
  const durationStr = formatDuration(duration);
  const safeTabs: VideoHeroTab[] = tabs ?? [];
  const hasTabs = safeTabs.length > 0;
  const domainMeta = primaryTag ? DOMAIN_META[primaryTag] : undefined;
  const isStreaming = isStreamingProp ?? !tldr;
  // Pending strip renders only while streaming and before real tabs exist —
  // same geometry as the live strip so the arrival is a fill-in, not a jump.
  // Requires the explicit prop: legacy callers that omit it get the skeleton
  // treatment from `isStreaming`'s tldr heuristic but never the strip.
  const showPendingStrip = !hasTabs && isStreaming && isStreamingProp !== undefined;
  // Once tabs exist, the planned ones still to come trail the live strip.
  const trailingPending = hasTabs && isStreaming && isStreamingProp !== undefined ? pendingTabs ?? [] : [];

  // Key takeaways open on load — they ARE the landing summary. They step aside
  // (auto-collapse, once) the first time the user engages the player, because
  // watching displaces reading as the primary activity. No persistence: every
  // load is a fresh read-first arrival.
  // `userOpen` null = untouched; a manual toggle takes permanent ownership.
  const [userOpen, setUserOpen] = useState<boolean | null>(null);
  const [autoCollapsed, setAutoCollapsed] = useState(false);
  // One-shot auto-collapse on first engagement (seek or player open), adjusted
  // during render. While streaming it waits until the stream settles so
  // content isn't yanked mid-arrival.
  if (!autoCollapsed && userOpen === null && hasEngaged && !isStreaming) {
    setAutoCollapsed(true);
  }
  // Phones start collapsed too (mobile rule, audit §2 #15): with the list
  // open the hero filled the first viewport; the "Key takeaways · N" row is
  // the reopen affordance.
  const takeawaysOpen = userOpen ?? (!autoCollapsed && !isPhone);

  const toggleTakeaways = useCallback(() => {
    setUserOpen(!takeawaysOpen);
  }, [takeawaysOpen]);

  // The brief opens on desktop (reading surface) and starts collapsed on
  // phones, where it would push the tab strip below the first viewport.
  // A manual toggle owns the state from then on.
  const [briefUserOpen, setBriefUserOpen] = useState<boolean | null>(null);
  const briefOpen = briefUserOpen ?? !isPhone;
  const toggleBrief = useCallback(() => {
    setBriefUserOpen(!briefOpen);
  }, [briefOpen]);
  const brief = masterSummary?.trim() ? briefParagraphs(masterSummary) : [];
  // Open ≠ unrolled: the brief shows five lines until the reader asks for all.
  const [briefExpanded, setBriefExpanded] = useState(false);
  const briefClamps = brief.join(' ').length > BRIEF_CLAMP_CHARS;

  const takeawayCap = isPhone ? TAKEAWAY_CAP_PHONE : TAKEAWAY_CAP_DESKTOP;
  const [showAllTakeaways, setShowAllTakeaways] = useState(false);
  const allTakeaways = keyTakeaways ?? [];
  const visibleTakeaways = showAllTakeaways ? allTakeaways : allTakeaways.slice(0, takeawayCap);
  const hiddenTakeaways = allTakeaways.length - visibleTakeaways.length;

  const stripRef = useRef<HTMLDivElement>(null);
  const stripOverflow = useStripOverflow(stripRef, safeTabs.length);
  const scrollStripOn = useCallback(() => {
    stripRef.current?.scrollBy({ left: stripRef.current.clientWidth * 0.8, behavior: 'smooth' });
  }, []);

  // Identity strip pieces — laid out as a single row, separated by dots so the
  // metadata reads as one cohesive line instead of a stack of chips.
  const identityChips: Array<{ key: string; node: React.ReactNode }> = [];
  if (domainMeta) {
    identityChips.push({
      key: 'domain',
      node: (
        <span className="inline-flex items-center gap-1.5 font-medium text-foreground/85">
          <span aria-hidden="true" className="text-sm leading-none">{domainMeta.emoji}</span>
          <span>{domainMeta.label}</span>
        </span>
      ),
    });
  }
  if (level) identityChips.push({ key: 'level', node: <span>{level}</span> });
  if (durationStr) identityChips.push({ key: 'duration', node: <span className="tabular-nums">{durationStr}</span> });
  if (creator) identityChips.push({ key: 'creator', node: <span className="truncate max-w-[24ch]">{creator}</span> });

  const showTakeaways = visibleTakeaways.length > 0 || isStreaming;
  // When nothing renders below the header (no takeaways, no tab strip, player
  // closed), the header must carry its own bottom padding so the card doesn't
  // look clipped. Every other block supplies its own trailing space.
  const headerNeedsBottomPad = !showTakeaways && !hasTabs && !showPendingStrip && !isPlayerOpen;

  return (
    <motion.section
      className={cn(
        'relative w-full rounded-2xl border border-border bg-card',
        className,
      )}
      // While streaming, the hero is the landing element of the
      // "stream-input-spine" view transition — the URL input morphs into
      // this card on navigate. Scoped to streaming so the name can't
      // collide with a sidebar intake form on an already-finished page.
      style={isStreaming ? ({ viewTransitionName: 'vie-input-spine' } as React.CSSProperties) : undefined}
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ ...springs.soft, delay: 0.04 }}
      aria-labelledby="vie-hero-title"
    >
      <div className={cn('px-6 pt-5 md:px-7 md:pt-6', headerNeedsBottomPad && 'pb-5 md:pb-6')}>
        {/* Top row: identity strip + title stack on the left; the watch control
            pins to the right corner as bare line-art — no tile, no ring, just a
            large stroked play arrow whose edges morph into a close mark when the
            player opens (PlayCloseGlyph). It sits in foreground ink at rest and
            lifts into the domain accent on hover and while open. The oversized
            invisible button keeps a comfortable touch target around the glyph. */}
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            {identityChips.length > 0 && (
              <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1 text-xs text-muted-foreground">
                {identityChips.map((chip, i) => (
                  <span key={chip.key} className="inline-flex items-center gap-2.5">
                    {i > 0 && <span aria-hidden="true" className="text-muted-foreground/40">·</span>}
                    {chip.node}
                  </span>
                ))}
              </div>
            )}
            <h2
              id="vie-hero-title"
              className={cn(
                'font-semibold tracking-tight leading-[1.15] text-balance break-words line-clamp-2 text-xl md:text-2xl',
                identityChips.length > 0 && 'mt-2.5',
              )}
            >
              {title || t.processing}
            </h2>
          </div>
          {youtubeId && (
            <button
              type="button"
              onClick={togglePlayer}
              className="play-control -mr-1 -mt-1 cursor-pointer flex size-12 shrink-0 items-center justify-center md:size-14"
              aria-label={isPlayerOpen ? t.hide : t.watch}
              aria-expanded={isPlayerOpen}
            >
              <PlayCloseGlyph open={isPlayerOpen} className="size-8 shrink-0 md:size-9" />
            </button>
          )}
        </div>

        {isStreaming && !tldr ? (
          <div className="mt-3 space-y-1.5" aria-hidden="true">
            <div className="h-3.5 w-full rounded bg-muted/30 animate-pulse" />
            <div className="h-3.5 w-4/5 rounded bg-muted/30 animate-pulse" />
            <div className="h-3.5 w-3/5 rounded bg-muted/30 animate-pulse" />
          </div>
        ) : tldr ? (
          <p className="mt-3 max-w-[68ch] text-[0.95rem] leading-relaxed text-foreground/85 text-pretty">
            {tldr}
          </p>
        ) : null}

        {/* The brief — the written overview every video has, under the tldr.
            The key question leads it when the extraction found one. */}
        {brief.length > 0 && (
          <div className="mt-4">
            <CollapsibleSection
              baseId="vie-hero-brief"
              label={t.brief}
              open={briefOpen}
              onToggle={toggleBrief}
            >
              <div className="max-w-[68ch] space-y-2.5 text-sm leading-relaxed text-foreground/80 text-pretty">
                {keyQuestion && (
                  <p className="font-medium text-foreground/90">{keyQuestion}</p>
                )}
                {briefClamps && !briefExpanded ? (
                  <p className="line-clamp-5">{brief.join(' ')}</p>
                ) : (
                  brief.map((paragraph, i) => <p key={i}>{paragraph}</p>)
                )}
                {briefClamps && (
                  <Button
                    variant="ghost"
                    size="bare"
                    onClick={() => setBriefExpanded(!briefExpanded)}
                    className="text-xs font-medium text-[var(--vie-accent)] hover:underline"
                  >
                    {briefExpanded ? t.hide : t.showAll}
                  </Button>
                )}
              </div>
            </CollapsibleSection>
          </div>
        )}
      </div>

      {youtubeId && (
        <div
          // Registered as the seek scroll anchor: a timestamp click far down
          // the page scrolls this wrapper back into view before the seek runs.
          ref={registerPlayerAnchor}
          className={cn(
            'grid transition-[grid-template-rows,opacity] duration-300 ease-[var(--ease-out-expo)]',
            isPlayerOpen ? 'grid-rows-[1fr] opacity-100' : 'grid-rows-[0fr] opacity-0',
          )}
        >
          <div className="overflow-hidden">
            <div className="px-6 pb-4 md:px-7 pt-4">
              <YouTubePlayer
                ref={playerRef}
                youtubeId={youtubeId}
                className="rounded-lg"
              />
            </div>
          </div>
        </div>
      )}

      {/* Key takeaways — open on load as a dense two-column grid; after the
          user engages the player they auto-collapse (once) into the slim
          "Key takeaways · N" disclosure, which stays as the reopen affordance.
          While streaming the count is unknown, so the eyebrow shows a static
          skeleton grid instead of a toggle. */}
      {showTakeaways ? (
        <div className="px-6 pb-5 md:px-7 md:pb-6 pt-4">
          {visibleTakeaways.length === 0 ? (
            <div className="space-y-3" aria-hidden="true">
              <div className="flex items-center gap-2.5">
                <span className="h-px w-5 bg-border" />
                <span className="type-eyebrow text-muted-foreground">{t.keyTakeaways}</span>
                <span className="h-px flex-1 bg-border/60" />
              </div>
              <ul className="grid grid-cols-1 gap-x-7 gap-y-2 sm:grid-cols-2">
                {Array.from({ length: 4 }, (_, i) => (
                  <li key={i} className="flex items-start gap-2.5">
                    <span className="mt-[0.4rem] h-1 w-1 rounded-full bg-muted/40 shrink-0" />
                    <span
                      className="h-3 rounded bg-muted/30 animate-pulse"
                      style={{ width: `${88 - (i % 2) * 16}%` }}
                    />
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            <FadeIn>
              <CollapsibleSection
                baseId="vie-hero-takeaways"
                label={`${t.keyTakeaways} · ${allTakeaways.length}`}
                open={takeawaysOpen}
                onToggle={toggleTakeaways}
              >
                {/* Uniform fine dot marker (not per-item emoji) keeps both
                    columns left-aligned and reads as a crisp, ordered list.
                    Tight 13px type sits clearly below the brief in hierarchy. */}
                <ul className="grid grid-cols-1 gap-x-7 gap-y-2 sm:grid-cols-2">
                  {visibleTakeaways.map((takeaway, i) => (
                    <FadeIn key={i} index={i}>
                      <li className="flex items-start gap-2.5 text-[0.8125rem] leading-snug text-foreground/75">
                        <span
                          aria-hidden="true"
                          className="mt-[0.4rem] h-1 w-1 rounded-full bg-foreground/30 shrink-0"
                        />
                        <span>{takeaway}</span>
                      </li>
                    </FadeIn>
                  ))}
                </ul>
                {(hiddenTakeaways > 0 || showAllTakeaways) && (
                  <Button
                    variant="ghost"
                    size="bare"
                    onClick={() => setShowAllTakeaways(!showAllTakeaways)}
                    className="mt-2.5 text-xs font-medium text-[var(--vie-accent)] hover:underline"
                  >
                    {showAllTakeaways ? t.hide : `+${hiddenTakeaways} · ${t.moreTakeaways}`}
                  </Button>
                )}
              </CollapsibleSection>
            </FadeIn>
          )}
        </div>
      ) : null}

      {/* Streaming strip: the tab plan (or skeleton pills before it's known)
          rendered in the exact geometry of the live strip below, so real tabs
          arriving reads as a fill-in rather than a layout swap. Inert and
          hidden from AT — the StreamingPlaceholder timeline carries progress
          semantics. */}
      {showPendingStrip && (
        <div
          aria-hidden="true"
          className={cn(
            'flex items-center gap-1.5 overflow-x-hidden',
            'border-t border-border/60 rounded-b-2xl px-3 py-2.5 md:px-4',
            '[mask-image:linear-gradient(to_right,transparent,black_16px,black_calc(100%-16px),transparent)]',
          )}
        >
          {pendingTabs && pendingTabs.length > 0
            ? pendingTabs.map((tab, i) => (
                <PendingTabPill key={tab.id} label={tab.label} emoji={tab.emoji} index={i} />
              ))
            : [88, 104, 72, 96].map((width, i) => (
                <div
                  key={i}
                  className="h-9 shrink-0 rounded-xl bg-muted/25 skeleton-breathe"
                  style={{ width }}
                />
              ))}
        </div>
      )}

      {hasTabs && (
        <TooltipProvider delayDuration={300}>
          {/* The sticky shell holds the scrolling strip and, when tabs sit past
              its end, a "+N" pill that scrolls them in — the fade alone hid
              them on phones. */}
          <div className="sticky top-0 z-20 rounded-b-2xl border-t border-border/60 bg-card/85 backdrop-blur-md">
          <div
            ref={stripRef}
            role="tablist"
            aria-label="Video insight sections"
            className={cn(
              'flex items-center gap-1.5 overflow-x-auto scroll-smooth snap-x snap-proximity',
              'rounded-b-2xl px-3 py-2.5 md:px-4',
              '[mask-image:linear-gradient(to_right,transparent,black_16px,black_calc(100%-16px),transparent)]',
              '[&::-webkit-scrollbar]:hidden [-ms-overflow-style:none] [scrollbar-width:none]',
            )}
          >
            {safeTabs.map((tab) => {
              const isActive = tab.id === activeTabId;
              const isCompleted = completedTabs?.has(tab.id) ?? false;
              const label = trueCountLabel(stripLeadingEmoji(tab.label), tab.itemCount);
              const button = (
                <button
                  key={tab.id}
                  type="button"
                  id={`tab-${tab.id}`}
                  role="tab"
                  aria-selected={isActive}
                  aria-controls={`panel-${tab.id}`}
                  onClick={() => onTabSelect?.(tab.id)}
                  className={cn(
                    'group relative inline-flex shrink-0 snap-start items-center gap-1.5 whitespace-nowrap',
                    'rounded-xl px-3.5 py-2 text-sm transition-all duration-200',
                    'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                    isActive
                      ? 'font-semibold text-primary-foreground shadow-md shadow-primary/15'
                      : 'font-medium border border-border/60 bg-muted/30 text-foreground/80 hover:bg-muted/60 hover:text-foreground',
                  )}
                  style={
                    isActive && domainGradient
                      ? { background: domainGradient }
                      : undefined
                  }
                >
                  {tab.emoji && (
                    <span aria-hidden="true" className="text-[0.95rem] leading-none">
                      {tab.emoji}
                    </span>
                  )}
                  <span>{label}</span>
                  {tab.provisional && (
                    <>
                      <span
                        aria-hidden="true"
                        className="size-1.5 shrink-0 rounded-full bg-current opacity-60 animate-pulse motion-reduce:animate-none"
                      />
                      <span className="sr-only">(early preview)</span>
                    </>
                  )}
                  {isCompleted && !isActive && (
                    <Check className="h-3.5 w-3.5 shrink-0 text-success" aria-label="Completed" />
                  )}
                </button>
              );
              if (!tab.preview) return button;
              return (
                <Tooltip key={tab.id}>
                  <TooltipTrigger asChild>{button}</TooltipTrigger>
                  <TooltipContent side="bottom" className="text-xs max-w-[220px]">
                    {tab.preview}
                  </TooltipContent>
                </Tooltip>
              );
            })}
            {trailingPending.map((tab, i) => (
              <PendingTabPill key={tab.id} label={tab.label} emoji={tab.emoji} index={i} upcoming />
            ))}
          </div>
          {stripOverflow > 0 && (
            <div className="pointer-events-none absolute inset-y-0 end-0 flex items-center rounded-be-2xl bg-gradient-to-l from-card from-55% to-transparent ps-10 pe-2">
              <Button
                variant="ghost"
                size="bare"
                onClick={scrollStripOn}
                aria-label={`+${stripOverflow} ${t.showAll}`}
                className="pointer-events-auto rounded-full border border-border/60 bg-card px-2 py-0.5 text-xs font-medium tabular-nums text-foreground/80 shadow-sm hover:bg-muted/60"
              >
                +{stripOverflow}
              </Button>
            </div>
          )}
          </div>
        </TooltipProvider>
      )}
    </motion.section>
  );
});
