import { memo } from 'react';
import { motion } from 'motion/react';
import { Check } from 'lucide-react';
import { cn } from '@/lib/utils';
import { springs } from '@/lib/motion';

interface TabItem {
  id: string;
  label: string;
  emoji?: string;
}

interface TabBarProps {
  tabs: TabItem[];
  activeId: string;
  onTabChange: (id: string) => void;
  completedIds?: Set<string>;
  activeGradient?: string;
  className?: string;
  /** Unique layout id — isolates the morphing pill per TabBar instance. */
  layoutGroup?: string;
}

export const TabBar = memo(function TabBar({
  tabs,
  activeId,
  onTabChange,
  completedIds,
  activeGradient,
  className,
  layoutGroup = 'tabbar-default',
}: TabBarProps) {
  return (
    <div
      className={cn('flex gap-2 overflow-x-auto pb-1 scrollbar-hide scroll-fade-x', className)}
      role="tablist"
    >
      {tabs.map((tab) => {
        const isActive = tab.id === activeId;
        const isCompleted = completedIds?.has(tab.id);
        return (
          <button
            key={tab.id}
            id={`tab-${tab.id}`}
            role="tab"
            aria-selected={isActive}
            aria-controls={`panel-${tab.id}`}
            onClick={() => onTabChange(tab.id)}
            className={cn(
              'relative flex shrink-0 items-center gap-1.5 rounded-full px-4 py-2 text-sm font-medium transition-colors',
              isActive ? 'text-primary-foreground' : 'text-muted-foreground hover:text-foreground',
            )}
          >
            {isActive && (
              <motion.span
                layoutId={layoutGroup}
                className="absolute inset-0 rounded-full shadow-[0_8px_24px_-8px_oklch(from_var(--primary)_l_c_h_/_0.55)]"
                style={{ background: activeGradient || 'var(--primary)' }}
                transition={springs.soft}
                aria-hidden="true"
              />
            )}
            {!isActive && (
              <span className="absolute inset-0 rounded-full bg-muted/50" aria-hidden="true" />
            )}
            <span className="relative inline-flex items-center gap-1.5">
              {tab.emoji && <span role="img" aria-hidden="true">{tab.emoji}</span>}
              <span>{tab.label}</span>
              {isCompleted && !isActive && (
                <motion.span
                  initial={{ scale: 0, opacity: 0 }}
                  animate={{ scale: 1, opacity: 1 }}
                  transition={springs.crisp}
                  className="inline-flex"
                >
                  <Check className="h-3 w-3 text-success" aria-label="Completed" />
                </motion.span>
              )}
            </span>
          </button>
        );
      })}
    </div>
  );
});
