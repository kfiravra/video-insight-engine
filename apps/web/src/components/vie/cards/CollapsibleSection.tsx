import { ChevronDown } from 'lucide-react';
import { cn } from '@/lib/utils';
import type { ReactNode } from 'react';

interface CollapsibleSectionProps {
  /** Stable id; used to build the `<button aria-controls>` ↔ `<div id>` link
   *  and the `<span id>` referenced by the section's `aria-labelledby`. */
  baseId: string;
  label: string;
  /** Shown beside the label and read with it (a review's verdict badge), so a
   *  collapsed row still says the call. */
  accessory?: ReactNode;
  open: boolean;
  onToggle: () => void;
  children: ReactNode;
}

/** Shared collapsible primitive for the hero's Brief, Key takeaways and Verdict
 *  rows. Renders an eyebrow row with left dash + label +
 *  flex-spacer dash + chevron, followed by a grid-rows morphing content panel. */
export function CollapsibleSection({ baseId, label, accessory, open, onToggle, children }: CollapsibleSectionProps) {
  const labelId = `${baseId}-label`;
  const contentId = `${baseId}-content`;
  return (
    <section aria-labelledby={labelId} className="space-y-3">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        aria-controls={contentId}
        className="group flex w-full items-center gap-2.5 rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <span aria-hidden="true" className="h-px w-5 bg-border" />
        <span id={labelId} className="type-eyebrow">{label}</span>
        {accessory}
        <span aria-hidden="true" className="h-px flex-1 bg-border/60" />
        <ChevronDown
          aria-hidden="true"
          className={cn(
            'h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform duration-200',
            'group-hover:text-foreground',
            !open && '-rotate-90 rtl:rotate-90',
          )}
        />
      </button>
      {/* Collapsed content is still mounted (the grid-rows morph animates it
          back in) but must leave the focus order and the accessibility tree. */}
      <div
        id={contentId}
        inert={!open}
        aria-hidden={!open}
        className={cn(
          'grid transition-[grid-template-rows,opacity] duration-300 ease-[var(--ease-out-expo)]',
          open ? 'grid-rows-[1fr] opacity-100' : 'grid-rows-[0fr] opacity-0',
        )}
      >
        <div className="overflow-hidden">{children}</div>
      </div>
    </section>
  );
}
