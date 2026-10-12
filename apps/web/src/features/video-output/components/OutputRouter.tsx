import { useMemo } from 'react';
import { isContentTag } from '@vie/types';
import type { SynthesisResult, ContentTag, VIEResponseMeta, TriageResult } from '@vie/types';
import { getDomainGradient } from '@vie/shared/config';
import { TabCoordinationProvider } from './output/TabCoordinationContext';
import { CommandDeck, TabPanel, type PendingTabLabel, type ShellTabDef } from './output/OutputTabShell';
import { TabStateProvider } from '@/features/video-output/contexts/TabStateContext';
import { GlassCard } from './output/GlassCard';
import type { StreamTab } from '@/features/video-output/lib/streaming/stream-tabs';
import { StreamingPlaceholder, StreamErrorCard } from './StreamingPlaceholder';
import { stripLeadingEmoji } from '@/lib/string-utils';
import { DirectionProvider } from '@/contexts/DirectionContext';
import { getLabels } from '@/lib/i18n';
import { isRTL as isRTLLanguage } from '@/lib/rtl';
import type {
  StreamPhase,
  StreamPhaseDetail,
  ExtractionProgressInfo,
  FrameInfo,
} from '@/features/video-output/hooks/use-summary-stream';

interface OutputRouterProps {
  title: string;
  videoSummaryId: string;
  /** Streamed or DB tabs; streamed ones carry the stream flags (provisional). */
  tabs: StreamTab[] | null;
  meta: VIEResponseMeta | null;
  synthesis: SynthesisResult | null;
  isStreaming?: boolean;
  tabCount?: number;
  tabLabels?: PendingTabLabel[];
  /** The stream holds the run's final tab set — nothing more will land. */
  tabsSettled?: boolean;
  /** Streamed tab id → its successor (`tab_ready.replaces`); focus follows. */
  replacedTabIds?: Record<string, string>;
  youtubeId?: string;
  creator?: string;
  duration?: number | null;
  language?: string;
  isRTL?: boolean;
  streamPhase?: StreamPhase;
  phaseDetail?: StreamPhaseDetail | null;
  extractionProgress?: ExtractionProgressInfo | null;
  /** Triage result from the stream — drives the early domain-accent bind and
   *  the "Detected: …" moment in the streaming timeline. */
  triage?: TriageResult | null;
  /** Vision-analyzed frames streamed mid-pipeline; shown as work evidence. */
  frames?: FrameInfo[];
  /** Video poster from the metadata event — the earliest visual proof that
   *  the pipeline is working on the right video. */
  thumbnailUrl?: string;
  /** Non-fatal pipeline warnings streamed so far. */
  warnings?: string[];
  /** User-friendly stream error. When set (and not streaming), an error card
   *  with a retry affordance renders — instead of the generic "extraction
   *  incomplete" message before any tab, above the tabs once some landed. */
  streamError?: string | null;
  /** Reattach to the stream (the run keeps going server-side). */
  onRetryStream?: () => void;
  retryingStream?: boolean;
  /** Abort the in-flight stream. When provided, the streaming placeholder
   *  surfaces a Cancel control after a brief delay (so it doesn't compete
   *  with the peak moment). */
  onCancelStream?: () => void;
  /** Optional toggle for switching between original-language and English
   *  content. Rendered at the top of the content area when present. */
  languageToggle?: React.ReactNode;
}

/** Count moment_track items that carry a usable frame thumbnail. */
function countFrameBackedItems(props: Record<string, unknown> | undefined): number {
  const items = props && Array.isArray(props.items) ? props.items : [];
  let count = 0;
  for (const entry of items) {
    if (
      entry != null &&
      typeof entry === 'object' &&
      typeof (entry as { thumbnailUrl?: unknown }).thumbnailUrl === 'string' &&
      (entry as { thumbnailUrl: string }).thumbnailUrl.length > 0
    ) {
      count++;
    }
  }
  return count;
}

export function OutputRouter({
  title,
  videoSummaryId,
  tabs,
  meta,
  synthesis,
  isStreaming,
  tabCount = 0,
  tabLabels = [],
  tabsSettled = false,
  replacedTabIds,
  youtubeId,
  creator,
  duration,
  language,
  isRTL: isRTLProp,
  streamPhase,
  phaseDetail,
  extractionProgress,
  triage,
  frames,
  thumbnailUrl,
  warnings,
  streamError,
  onRetryStream,
  retryingStream,
  onCancelStream,
  languageToggle,
}: OutputRouterProps) {
  // meta wins, but triage arrives much earlier in the stream — falling back
  // to it binds the domain accent within seconds instead of waiting for
  // assembly to start.
  const primaryTag = useMemo((): ContentTag => {
    const raw = typeof meta?.primaryTag === 'string' && meta.primaryTag
      ? meta.primaryTag
      : (typeof triage?.primaryTag === 'string' ? triage.primaryTag : '');
    if (isContentTag(raw)) return raw;
    if (raw && import.meta.env.DEV) {
      console.warn(`[OutputRouter] Invalid primaryTag "${raw}", defaulting to learning`);
    }
    return 'learning';
  }, [meta, triage]);

  // Tab-preview tooltips come from the i18n bundle so non-English sessions
  // don't regress to hardcoded strings. getLabels() is pure, safe to call
  // here above DirectionProvider (it takes the language prop directly).
  const tabPreviews = useMemo(() => getLabels(language ?? 'en').tabPreviews, [language]);

  // Overview tab is suppressed in the tab strip — the page-level VideoHero
  // owns the briefing (title, brief, key takeaways), so a dedicated Overview
  // tab would just repeat content. The backend still emits it (cache-friendly),
  // we just drop it before rendering.
  // The filmstrip tab is likewise suppressed when a moment_track tab carries
  // rich frame coverage (≥8 frame-backed items) — its grid view already IS a
  // filmstrip with context, so a second frame strip would just repeat frames.
  const orderedTabs = useMemo(() => {
    if (!tabs || tabs.length === 0) return tabs;
    const hasRichMomentTrack = tabs.some(
      (t) => t.component === 'moment_track' && countFrameBackedItems(t.props) >= 8,
    );
    return tabs.filter((t) => {
      if (t.id === 'overview' || t.component === 'overview') return false;
      if (t.component === 'video_filmstrip' && hasRichMomentTrack) return false;
      return true;
    });
  }, [tabs]);

  const tabDefs = useMemo((): ShellTabDef[] => {
    if (!orderedTabs || orderedTabs.length === 0) return [];
    return orderedTabs.map(t => ({
      id: t.id,
      label: t.emoji ? stripLeadingEmoji(t.label) : t.label,
      emoji: t.emoji,
      dataSource: '',
      preview: tabPreviews[t.id],
      provisional: t.provisional === true,
    }));
  }, [orderedTabs, tabPreviews]);

  // Planned tabs (meta.tabLabels) that haven't streamed yet: inert pills in
  // the strip and the provisional notice's count. Nothing is pending once the
  // final tab set is known (e.g. during the translation tail).
  const pendingTabs = useMemo((): PendingTabLabel[] => {
    if (!isStreaming || tabsSettled) return [];
    const arrived = new Set((tabs ?? []).map((t) => t.id));
    return tabLabels.filter(
      (l) => l.id !== 'overview' && !arrived.has(l.id) && !(replacedTabIds && l.id in replacedTabIds),
    );
  }, [isStreaming, tabsSettled, tabs, tabLabels, replacedTabIds]);

  const hasData = orderedTabs != null && orderedTabs.length > 0;
  const domainGradient = useMemo(() => getDomainGradient(primaryTag), [primaryTag]);
  const initialTab = tabDefs[0]?.id ?? '';

  // Suppressed tabs (overview, redundant filmstrip) are filtered out of the
  // strip above, but tabCount (from meta/complete) includes them — subtract
  // them so "N of M" can actually reach M.
  const suppressedTabCount = (tabs?.length ?? 0) - (orderedTabs?.length ?? 0);
  const adjustedTabCount = Math.max(0, tabCount - suppressedTabCount);

  if (!hasData && !isStreaming && !streamError) return null;

  return (
    <DirectionProvider language={language} isRTL={isRTLProp ?? isRTLLanguage(language)}>
      <div className="mx-auto flex w-full max-w-5xl flex-col gap-4 px-3 pt-3 pb-8 md:px-6 md:pt-5 md:pb-12">
        {/* Language toggle is intentionally pinned to the LTR-end (page right)
            regardless of content direction. Rationale: English is the default
            pill and English readers expect controls on the right; flipping
            with RTL would put the active control where the eye doesn't look. */}
        {languageToggle ? (
          <div dir="ltr" className="flex justify-end">{languageToggle}</div>
        ) : null}
        {/* One tree from the first event to the last: the hero (and its inline
            player) never remounts when the first tab lands, and the initial
            tab is chosen once (sticky — a tab streamed in ahead of it never
            steals focus). */}
        <TabCoordinationProvider
          key={videoSummaryId}
          videoId={videoSummaryId}
          initialTab={initialTab}
          replacedTabIds={replacedTabIds}
        >
          <TabStateProvider videoId={videoSummaryId}>
            <CommandDeck
              title={title}
              creator={creator}
              duration={duration}
              synthesis={synthesis}
              youtubeId={youtubeId}
              tabDefs={tabDefs}
              pendingTabs={hasData ? pendingTabs : tabLabels}
              domainGradient={domainGradient}
              primaryTag={primaryTag}
              isStreaming={isStreaming}
            />
            {/* A stream that failed after tabs landed (a dropped connection
                mid-run) keeps the tabs and says so, with a way back in. */}
            {hasData && !isStreaming && streamError && (
              <StreamErrorCard message={streamError} onRetry={onRetryStream} retrying={retryingStream} />
            )}
            {hasData ? (
              <TabPanel
                tabDefs={tabDefs}
                tabs={orderedTabs}
                primaryTag={primaryTag}
                isStreaming={isStreaming}
                tabCount={adjustedTabCount}
                streamPhase={streamPhase}
                videoSummaryId={videoSummaryId}
                pendingTabCount={pendingTabs.length}
                onCancel={onCancelStream}
              />
            ) : isStreaming ? (
              <StreamingPlaceholder
                streamPhase={streamPhase}
                phaseDetail={phaseDetail}
                extractionProgress={extractionProgress ?? null}
                triage={triage}
                frames={frames}
                thumbnailUrl={thumbnailUrl}
                tabCount={tabCount}
                tabLabels={tabLabels}
                language={language}
                warnings={warnings}
                onCancel={onCancelStream}
              />
            ) : streamError ? (
              <StreamErrorCard
                message={streamError}
                onRetry={onRetryStream}
                retrying={retryingStream}
              />
            ) : (
              <GlassCard>
                <p className="text-sm text-muted-foreground text-center py-6">
                  Content extraction was incomplete for this video.
                </p>
              </GlassCard>
            )}
          </TabStateProvider>
        </TabCoordinationProvider>

        <p className="text-center text-xs text-muted-foreground/60">
          AI-generated summary. Verify important details with the original video.
        </p>
      </div>
    </DirectionProvider>
  );
}
