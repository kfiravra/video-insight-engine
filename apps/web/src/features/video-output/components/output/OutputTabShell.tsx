import { useRef } from 'react';
import type { ContentTag, SynthesisResult, TabDefinition, TabEntry } from '@vie/types';
import { ErrorBoundary } from '@/components/ui/error-boundary';
import { VideoHero } from '@/components/vie';
import type { StreamPhase } from '@/features/video-output/hooks/use-summary-stream';
import { ComposableOutput } from './ComposableOutput';
import { GlassCard } from './GlassCard';
import { ProvisionalTabNotice } from './ProvisionalTabNotice';
import { TabLayout, type TabLayoutHandle } from './TabLayout';
import { useTabCoordination } from './TabCoordinationContext';

/** A tab-strip entry as the shell renders it. */
export interface ShellTabDef extends TabDefinition {
  preview?: string;
  provisional?: boolean;
}

export interface PendingTabLabel {
  id: string;
  label: string;
  emoji: string;
}

interface CommandDeckProps {
  title: string;
  creator?: string;
  duration?: number | null;
  synthesis: SynthesisResult | null;
  /** The question the video answers — lives on the overview tab's props
   *  (`data.keyQuestion`), which the router filters from the strip, so the
   *  router hands it over separately. */
  keyQuestion?: string;
  youtubeId?: string;
  tabDefs: ShellTabDef[];
  pendingTabs: PendingTabLabel[];
  domainGradient: string;
  primaryTag: ContentTag;
  isStreaming?: boolean;
}

/** Reads active-tab state from coordination context and renders the hero with
 *  the embedded tab bar. A thin wrapper so the hero doesn't need the context
 *  imported into it (keeps VideoHero reusable). Mounted before the first tab
 *  too, so the hero (and its inline player) never remounts when tabs land. */
export function CommandDeck({
  title,
  creator,
  duration,
  synthesis,
  keyQuestion,
  youtubeId,
  tabDefs,
  pendingTabs,
  domainGradient,
  primaryTag,
  isStreaming,
}: CommandDeckProps) {
  // Context must be present — fail loud if a future refactor breaks that
  // invariant instead of silently showing a non-interactive tab bar.
  const coordination = useTabCoordination();
  if (!coordination) {
    throw new Error('CommandDeck must be rendered inside TabCoordinationProvider');
  }
  const { activeTab: activeTabId, setActiveTab, completedTabs } = coordination;

  return (
    <VideoHero
      title={title}
      creator={creator}
      duration={duration}
      tldr={synthesis?.tldr}
      keyTakeaways={synthesis?.keyTakeaways}
      masterSummary={synthesis?.masterSummary}
      keyQuestion={keyQuestion}
      youtubeId={youtubeId}
      tabs={tabDefs}
      activeTabId={activeTabId}
      onTabSelect={setActiveTab}
      completedTabs={completedTabs}
      domainGradient={domainGradient}
      primaryTag={primaryTag}
      isStreaming={isStreaming}
      pendingTabs={pendingTabs}
    />
  );
}

interface TabPanelProps {
  tabDefs: ShellTabDef[];
  tabs: TabEntry[] | null;
  primaryTag: ContentTag;
  isStreaming?: boolean;
  tabCount: number;
  streamPhase?: StreamPhase;
  videoSummaryId: string;
  /** Planned tabs not streamed yet — the provisional notice counts them. */
  pendingTabCount: number;
  onCancel?: () => void;
}

export function TabPanel({
  tabDefs,
  tabs,
  primaryTag,
  isStreaming,
  tabCount,
  streamPhase,
  videoSummaryId,
  pendingTabCount,
  onCancel,
}: TabPanelProps) {
  const tabLayoutRef = useRef<TabLayoutHandle>(null);
  const provisionalIds = new Set(tabDefs.filter((t) => t.provisional).map((t) => t.id));
  return (
    <TabLayout
      ref={tabLayoutRef}
      tabs={tabDefs}
      primaryTag={primaryTag}
      isStreaming={isStreaming}
      totalCount={tabCount}
      streamPhase={streamPhase}
      onCancel={onCancel}
    >
      {(activeTabId, onNavigateTab) => (
        <ErrorBoundary
          fallback={
            <GlassCard>
              <p className="text-sm text-muted-foreground text-center py-6">
                Something went wrong rendering this tab. Try switching to another tab.
              </p>
            </GlassCard>
          }
        >
          {provisionalIds.has(activeTabId) && (
            <ProvisionalTabNotice pendingTabCount={pendingTabCount} isLive={isStreaming === true} />
          )}
          <ComposableOutput
            response={null}
            tabs={tabs}
            activeTab={activeTabId}
            onNavigateTab={onNavigateTab}
            primaryTag={primaryTag}
            videoSummaryId={videoSummaryId}
          />
        </ErrorBoundary>
      )}
    </TabLayout>
  );
}
