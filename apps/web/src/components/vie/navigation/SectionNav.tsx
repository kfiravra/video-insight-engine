import { memo, useEffect, useRef } from 'react';
import { motion } from 'motion/react';
import { cn } from '@/lib/utils';
import { springs } from '@/lib/motion';
import { usePrefersReducedMotion } from '@/hooks/use-prefers-reduced-motion';

interface SectionNavProps {
  sections: Array<{ id: string; label: string }>;
  activeId: string;
  onSelect: (id: string) => void;
  className?: string;
  layoutGroup?: string;
}

/**
 * A single scrollable row of section chips: the scrollbar is hidden and the
 * edges fade (`scrollbar-hide` + `scroll-fade-x`), chips snap into place, and
 * the row scrolls to keep the active chip in view whenever the active section
 * changes — so a chosen section never sits off-screen.
 */
export const SectionNav = memo(function SectionNav({
  sections,
  activeId,
  onSelect,
  className,
  layoutGroup = 'section-nav-default',
}: SectionNavProps) {
  const chipRefs = useRef(new Map<string, HTMLButtonElement>());
  const reducedMotion = usePrefersReducedMotion();

  // Follow the active chip on change only, never on first paint: the row
  // opens on its first chip anyway, and a scrollIntoView on mount could pull
  // the page down to a nav that sits below the fold.
  const lastActiveId = useRef(activeId);
  useEffect(() => {
    if (lastActiveId.current === activeId) return;
    lastActiveId.current = activeId;
    const chip = chipRefs.current.get(activeId);
    chip?.scrollIntoView({ inline: 'center', block: 'nearest', behavior: reducedMotion ? 'auto' : 'smooth' });
  }, [activeId, reducedMotion]);

  return (
    <nav
      className={cn(
        'flex gap-1 overflow-x-auto scrollbar-hide scroll-fade-x snap-x snap-proximity scroll-smooth motion-reduce:scroll-auto',
        className,
      )}
      aria-label="Sections"
    >
      {sections.map((section) => {
        const isActive = section.id === activeId;
        return (
          <button
            key={section.id}
            type="button"
            ref={(el) => {
              if (el) chipRefs.current.set(section.id, el);
              else chipRefs.current.delete(section.id);
            }}
            onClick={() => onSelect(section.id)}
            className={cn(
              'relative shrink-0 snap-center rounded-md px-3 py-1.5 text-xs font-medium transition-colors',
              isActive ? 'text-primary-foreground' : 'text-muted-foreground hover:text-foreground',
            )}
            aria-current={isActive ? 'true' : undefined}
          >
            {isActive && (
              <motion.span
                layoutId={layoutGroup}
                className="absolute inset-0 rounded-md bg-primary shadow-[0_4px_12px_-4px_oklch(from_var(--primary)_l_c_h_/_0.45)]"
                transition={springs.soft}
                aria-hidden="true"
              />
            )}
            {!isActive && (
              <span className="absolute inset-0 rounded-md bg-muted/30 opacity-0 hover:opacity-100 transition-opacity" aria-hidden="true" />
            )}
            <span className="relative">{section.label}</span>
          </button>
        );
      })}
    </nav>
  );
});
