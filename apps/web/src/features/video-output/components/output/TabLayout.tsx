import { memo, useEffect, useImperativeHandle, forwardRef, useCallback, useState, type ReactNode } from 'react';
import { cn } from '@/lib/utils';
import type { TabDefinition, ContentTag } from '@vie/types';
import { getDomainGradient } from '@vie/shared/config';
import type { StreamPhase } from '@/features/video-output/hooks/use-summary-stream';
import { useTabCoordination } from './TabCoordinationContext';
import { TabProgressStrip } from './TabProgressStrip';
import { withViewTransition } from '@/lib/view-transitions';
import { getAccentForTag } from './lib/content-accent';

interface TabLayoutProps {
  tabs: TabDefinition[];
  primaryTag: ContentTag;
  children: (activeTabId: string, onNavigateTab: (id: string) => void) => ReactNode;
  className?: string;
  isStreaming?: boolean;
  totalCount?: number;
  streamPhase?: StreamPhase;
  /** Abort the in-flight stream (shown in the progress strip while streaming). */
  onCancel?: () => void;
}

export interface TabLayoutHandle {
  setActiveTab: (tabId: string) => void;
}

const NOOP_SET_ACTIVE_TAB = (): void => {};

interface ArrivalState {
  tabId: string;
  /** First time this tab is the active one — it gets the scale-pulse. */
  fresh: boolean;
  seen: ReadonlySet<string>;
}

/** Track which tabs have been active so a newly opened tab pulses once and
 *  revisits stay calm. State adjusted during render: no effect, no ref read. */
function useFreshArrival(activeTab: string): boolean {
  const [arrival, setArrival] = useState<ArrivalState>(() => ({
    tabId: activeTab,
    fresh: true,
    seen: new Set([activeTab]),
  }));
  if (arrival.tabId !== activeTab) {
    setArrival({
      tabId: activeTab,
      fresh: !arrival.seen.has(activeTab),
      seen: new Set([...arrival.seen, activeTab]),
    });
  }
  return arrival.tabId === activeTab ? arrival.fresh : !arrival.seen.has(activeTab);
}

const TabLayoutInner = forwardRef<TabLayoutHandle, TabLayoutProps>(
  function TabLayout(
    { tabs, primaryTag, children, className, isStreaming = false, totalCount = 0, streamPhase, onCancel },
    ref,
  ) {
    const coordination = useTabCoordination();
    const activeTab = coordination?.activeTab ?? tabs[0]?.id ?? '';
    const setActiveTab = coordination?.setActiveTab ?? NOOP_SET_ACTIVE_TAB;

    // The active tab left the set (withdrawn by `complete`, filtered out):
    // fall back to the first visible tab. A tab superseded via `replaces`
    // never lands here — the coordination context follows it to its successor.
    useEffect(() => {
      if (tabs.length > 0 && !tabs.some(t => t.id === activeTab)) {
        const fallback = tabs.find(t => !t.hidden) ?? tabs[0];
        setActiveTab(fallback.id);
      }
    }, [tabs, activeTab, setActiveTab]);

    useImperativeHandle(ref, () => ({ setActiveTab }), [setActiveTab]);

    const switchTo = useCallback(
      (id: string) => {
        if (id === activeTab) return;
        withViewTransition(() => setActiveTab(id), { type: 'tab-switch' });
      },
      [activeTab, setActiveTab],
    );

    const gradient = getDomainGradient(primaryTag);
    const accent = getAccentForTag(primaryTag);
    const domainAccent: React.CSSProperties = {
      ['--domain-gradient' as string]: gradient,
      ['--vie-accent' as string]: accent,
    };

    const visibleTabs = tabs.filter(t => !t.hidden);
    const renderedCount = visibleTabs.length;
    const expectedCount = Math.max(totalCount, renderedCount);
    const isFreshArrival = useFreshArrival(activeTab);

    return (
      <div className={cn('flex flex-col gap-4', className)} data-domain={primaryTag} style={domainAccent}>
        {/* Live runs only: a run that stopped (error, cancel) must not read
            as "N of M ready" — the stream error card speaks for it. */}
        {isStreaming && (
          <TabProgressStrip
            renderedCount={renderedCount}
            expectedCount={expectedCount}
            streamPhase={streamPhase}
            onCancel={onCancel}
          />
        )}

        <div
          id={`panel-${activeTab}`}
          role="tabpanel"
          aria-labelledby={`tab-${activeTab}`}
          className={cn(
            'animate-[fadeUp_0.3s_var(--ease-out-expo,ease)_both]',
            // First-time arrivals get a brief scale-pulse so the user catches
            // the new tab landing; revisits stay calm.
            isFreshArrival && 'scale-pulse-once',
          )}
          key={activeTab}
        >
          {children(activeTab, switchTo)}
        </div>
      </div>
    );
  },
);

export const TabLayout = memo(TabLayoutInner);
