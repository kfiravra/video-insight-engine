import { Check } from 'lucide-react';
import { cn } from '@/lib/utils';
import { stripLeadingEmoji } from '@/lib/string-utils';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
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

interface HeroTabButtonProps {
  tab: VideoHeroTab;
  isActive: boolean;
  isCompleted: boolean;
  /** The active tab's fill. */
  domainGradient?: string;
  onTabSelect?: (id: string) => void;
}

/** One tab of the hero strip: a `role="tab"` button paired with its
 *  `panel-<id>` tabpanel, labelled with the shipped count, wrapped in a
 *  tooltip when the tab has a preview. */
export function HeroTabButton({ tab, isActive, isCompleted, domainGradient, onTabSelect }: HeroTabButtonProps) {
  const label = trueCountLabel(stripLeadingEmoji(tab.label), tab.itemCount);
  const button = (
    <button
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
      style={isActive && domainGradient ? { background: domainGradient } : undefined}
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
    <Tooltip>
      <TooltipTrigger asChild>{button}</TooltipTrigger>
      <TooltipContent side="bottom" className="text-xs max-w-[220px]">
        {tab.preview}
      </TooltipContent>
    </Tooltip>
  );
}
