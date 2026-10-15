import { memo, useCallback, useEffect, useRef, useState, type RefObject } from 'react';
import { Check } from 'lucide-react';
import { cn } from '@/lib/utils';
import { stripLeadingEmoji } from '@/lib/string-utils';
import { useLabels } from '@/lib/i18n';
import { Button } from '@/components/ui/button';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { PendingTabPill } from './PendingTabPill';
import { trueCountLabel } from './domain-meta';
import type { TabDefinition } from '@vie/types';

export interface VideoHeroTab extends TabDefinition {
  preview?: string;
  /** An early placeholder the stream will replace — marked in the strip. */
  provisional?: boolean;
  /** The number of items the tab really ships (inferItemCount); replaces the
   *  planner's guessed count in the label. */
  itemCount?: number;
}

interface VideoHeroStripProps {
  tabs: VideoHeroTab[];
  activeTabId?: string;
  onTabSelect?: (id: string) => void;
  completedTabs?: Set<string>;
  domainGradient?: string;
  /** Planned tabs still to come — inert pills trailing the live ones. */
  trailingPending: { id: string; label: string; emoji: string }[];
}

/** Tabs scrolled past the strip's visible end — the "+N" overflow count,
 *  re-measured on scroll, on resize of the strip or any tab, and whenever the
 *  tab labels change. Direction-aware: the "end" edge is the right edge in
 *  LTR and the left edge in RTL. */
function useStripOverflow(stripRef: RefObject<HTMLDivElement | null>, labelSignature: string): number {
  const [overflow, setOverflow] = useState(0);
  useEffect(() => {
    const strip = stripRef.current;
    if (!strip) return;
    const tabs = Array.from(strip.querySelectorAll<HTMLElement>('[role="tab"]'));
    const measure = (): void => {
      const rtl = getComputedStyle(strip).direction === 'rtl';
      const stripBox = strip.getBoundingClientRect();
      let hidden = 0;
      for (const tab of tabs) {
        const box = tab.getBoundingClientRect();
        const past = rtl ? box.left < stripBox.left - 1 : box.right > stripBox.right + 1;
        if (past) hidden += 1;
      }
      setOverflow(hidden);
    };
    measure();
    strip.addEventListener('scroll', measure, { passive: true });
    const observer = new ResizeObserver(measure);
    observer.observe(strip);
    for (const tab of tabs) observer.observe(tab);
    return () => {
      strip.removeEventListener('scroll', measure);
      observer.disconnect();
    };
  }, [stripRef, labelSignature]);
  return overflow;
}

/** Scroll the strip one viewport towards its end — leftwards in RTL. */
function scrollTowardsEnd(strip: HTMLDivElement): void {
  const rtl = getComputedStyle(strip).direction === 'rtl';
  const distance = strip.clientWidth * 0.8;
  strip.scrollBy({ left: rtl ? -distance : distance, behavior: 'smooth' });
}

/** The hero's tab strip: the sticky, horizontally scrolling tablist ("Video
 *  insight sections" — the audit and screenshot tools find it by that name)
 *  plus the "+N" pill that scrolls tabs past its visible end into view — the
 *  edge fade alone hid them on phones. */
export const VideoHeroStrip = memo(function VideoHeroStrip({
  tabs,
  activeTabId,
  onTabSelect,
  completedTabs,
  domainGradient,
  trailingPending,
}: VideoHeroStripProps) {
  const t = useLabels();
  const stripRef = useRef<HTMLDivElement>(null);
  // Labels change width (planner count → shipped count), so they re-measure too.
  const labelSignature = tabs.map((tab) => `${tab.id}:${tab.label}:${tab.itemCount ?? ''}`).join('|');
  const stripOverflow = useStripOverflow(stripRef, labelSignature);
  const scrollStripOn = useCallback(() => {
    if (stripRef.current) scrollTowardsEnd(stripRef.current);
  }, []);

  return (
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
        {tabs.map((tab) => {
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
        <div className="pointer-events-none absolute inset-y-0 end-0 flex items-center rounded-ee-2xl bg-gradient-to-l from-card from-55% to-transparent ps-10 pe-2">
          <Button
            variant="ghost"
            size="bare"
            onClick={scrollStripOn}
            aria-label={t.showMoreSections.replace('{count}', String(stripOverflow))}
            className="pointer-events-auto rounded-full border border-border/60 bg-card px-2 py-0.5 text-xs font-medium tabular-nums text-foreground/80 shadow-sm hover:bg-muted/60"
          >
            +{stripOverflow}
          </Button>
        </div>
      )}
      </div>
    </TooltipProvider>
  );
});
