import { memo, useState, useMemo } from 'react';
import type { VIEResponse, TabEntry, TabAttachment } from '@vie/types';
import { DisplaySection } from './DisplaySection';
import { CrossTabLink } from './CrossTabLink';
import { TabIntro } from './TabIntro';
import { resolveCrossTabLinks } from './link-rules';
import { resolveTabData } from '@/features/video-output/components/output/lib/tab-data-resolver';
import { INTERACTIVE_TABS, renderInteractive } from './ComposableOutputV1';
import { useVideoPlayer } from '@/features/video-output/contexts/VideoPlayerContext';
import { FlowPlayer } from './FlowPlayer';
import { detectFlowMode } from './flow-modes';
import { ErrorBoundary } from '@/components/ui/error-boundary';
import { EmojiMarker } from '@/components/vie';
import { inferItemCount, trueCountLabel } from '@/components/vie/cards/domain-meta';
import { stripLeadingEmoji } from '@/lib/string-utils';
import { validateTabEntries } from '@/features/video-output/lib/tab-prop-schemas';
import {
  COMPONENT_REGISTRY,
  resolveRenderer,
  type NavProps,
} from './component-registry';
import type { OverviewCrossTabLink } from './interactive/OverviewInteractive';

// Re-exported so the contract-parity test (and any external consumer) keeps
// its import path after the registry moved to component-registry.tsx (6.2).
export { REGISTERED_COMPONENT_NAMES } from './component-registry';

interface ComposableOutputProps {
  /** v1: VIEResponse with domain-keyed data (null when using assembled tabs). */
  response: VIEResponse | null;
  /** Pre-assembled tabs from backend (component-addressed). */
  tabs?: TabEntry[] | null;
  activeTab: string;
  onNavigateTab: (tabId: string) => void;
  /** Primary content tag (for cooking mode detection). */
  primaryTag?: string;
  /** Stable per-video id used for per-video local state (e.g. overview bookmarks). */
  videoSummaryId?: string;
}

/** The label a tab is shown under: emoji stripped, planner count replaced by
 *  the shipped count (R3). */
function displayLabel(tab: TabEntry): string {
  return trueCountLabel(stripLeadingEmoji(tab.label), inferItemCount(tab));
}

/** Build the cross-tab nav grid shown inside the Overview tab. Sources sibling
 *  tabs from the assembled response, drops the overview itself, and annotates
 *  each entry with an emoji + best-effort item count. */
function buildOverviewCrossTabLinks(
  allTabs: TabEntry[] | undefined,
  activeTabId: string,
): OverviewCrossTabLink[] {
  if (!allTabs || allTabs.length === 0) return [];
  const siblings = allTabs.filter((t) => t.id !== activeTabId && t.component !== 'overview');
  return siblings.map((t) => ({
    targetTab: t.id,
    label: displayLabel(t),
    emoji: t.emoji || undefined,
    count: inferItemCount(t),
  }));
}

interface TabAttachmentsProps {
  attachments: TabAttachment[] | undefined;
  slot: 'top' | 'bottom';
  nav: NavProps;
}

/** Render the secondary-tier attachments for one slot of a tab. Each attachment
 *  is routed through COMPONENT_REGISTRY (secondaries are registered there) and
 *  wrapped in its own ErrorBoundary so a broken attachment can't take down the
 *  primary interactive. Renders nothing when there are no attachments for the
 *  slot — keeping flat tabs byte-for-byte unchanged. */
function TabAttachments({ attachments, slot, nav }: TabAttachmentsProps) {
  if (!attachments || attachments.length === 0) return null;
  const forSlot = attachments.filter((a) => a.slot === slot && COMPONENT_REGISTRY[a.component]);
  if (forSlot.length === 0) return null;
  return (
    <div className="flex flex-col gap-2">
      {forSlot.map((attachment, i) => {
        const renderer = COMPONENT_REGISTRY[attachment.component];
        return (
          <ErrorBoundary
            key={`${slot}-${attachment.component}-${i}`}
            fallback={null}
          >
            {renderer(attachment.props ?? {}, nav)}
          </ErrorBoundary>
        );
      })}
    </div>
  );
}

/**
 * Composable output renderer.
 *
 * tabs path: Uses COMPONENT_REGISTRY to render by component name + pre-assembled props.
 * response path (v1 fallback): Routes tabs to interactive components via tab ID, resolving data from VIEResponse.
 */
export const ComposableOutput = memo(function ComposableOutput({
  response,
  tabs,
  activeTab,
  onNavigateTab,
  primaryTag,
  videoSummaryId,
}: ComposableOutputProps) {
  const { seekTo, currentTime, isPlayerOpen } = useVideoPlayer();
  const [flowModeActive, setFlowModeActive] = useState(false);

  // `currentTime` keeps its last polled value after the player closes; passing
  // it through would leave playback-driven active states (and the moment
  // timeline's activeIndex-over-scroll-focus override) stuck on a stale
  // position forever. No open player → no playback position.
  const playbackTime = isPlayerOpen ? currentTime : undefined;

  // Single validated boundary for EVERY tab source (4.4 follow-up): SSE tabs
  // arrive pre-gated by handleTabReady (re-validation is an idempotent pass),
  // while cached/DB assembledTabs, sourceLanguage tabs, and share pages get
  // their first — and only — Zod pass here. Malformed entries remap to the
  // display_section fallback and bump the prod-visible drift counters.
  const validatedTabs = useMemo(
    () => (tabs && tabs.length > 0 ? validateTabEntries(tabs) : null),
    [tabs],
  );

  // Memoize derived structures on `validatedTabs` so per-tab filtering and the
  // Overview cross-tab nav don't allocate fresh arrays on every `currentTime`
  // tick. This is what makes `memo()` on individual tab renderers actually hold.
  const filteredCrossLinks = useMemo(() => {
    const map = new Map<string, NonNullable<TabEntry['crossTabLinks']>>();
    if (validatedTabs) {
      for (const t of validatedTabs) {
        map.set(t.id, (t.crossTabLinks ?? []).filter((l) => l.targetTab !== 'overview'));
      }
    }
    return map;
  }, [validatedTabs]);

  const derivedOverviewLinks = useMemo(
    () => buildOverviewCrossTabLinks(validatedTabs ?? undefined, activeTab),
    [validatedTabs, activeTab],
  );

  // Detect the applicable enter-mode (cooking, workout, build, study, explore,
  // practice). Returns null — and shows no enter-mode button — when the
  // domain's required tabs/props aren't present. Cooking is migrated verbatim:
  // the resolved cooking mode produces the same ingredients context + steps
  // sequence the old hardcoded `cookingModeData` did.
  const flowMode = useMemo(
    () => detectFlowMode(validatedTabs, primaryTag),
    [validatedTabs, primaryTag],
  );

  // ─── Component-addressed rendering ───
  if (validatedTabs && validatedTabs.length > 0) {
    const tab = validatedTabs.find((t: TabEntry) => t.id === activeTab) ?? validatedTabs[0];
    if (!tab) return null;

    const renderer = resolveRenderer(tab.component);
    // Pull pre-filtered crossLinks from the memoized map. The Overview tab is
    // filtered from the strip at the OutputRouter layer; legacy backend rules
    // can still target "overview" and the parent-level filter drops those.
    const crossLinks = filteredCrossLinks.get(tab.id) ?? [];
    // Don't pass nextTab — ComposableOutput renders CrossTabLink after each tab.
    // Passing nextTab causes duplicate "Next" buttons inside Celebration components.
    const nav: NavProps = {
      onNavigateTab,
      onSeek: seekTo,
      tabId: tab.id,
      tabLabel: displayLabel(tab),
      currentTime: playbackTime,
      allTabs: validatedTabs,
      videoId: videoSummaryId,
      derivedOverviewLinks,
    };

    return (
      <div className="flex flex-col gap-4">
        {/* Enter-mode toggle (cooking, workout, build, study, explore, practice) */}
        {flowMode && !flowModeActive && (
          <button
            onClick={() => setFlowModeActive(true)}
            className="flex items-center justify-center gap-2 rounded-xl border border-dashed border-primary/30 bg-primary/5 px-4 py-2.5 text-sm font-medium text-primary transition-colors hover:bg-primary/10"
          >
            <EmojiMarker emoji={flowMode.emoji} size="sm" animated={false} />
            Enter {flowMode.label}
          </button>
        )}

        {/* Enter-mode player */}
        {flowModeActive && flowMode ? (
          <FlowPlayer
            emoji={flowMode.emoji}
            modeLabel={flowMode.label}
            stepNoun={flowMode.stepNoun}
            contextLabel={flowMode.contextLabel}
            contextCount={flowMode.contextCount}
            renderContext={flowMode.renderContext}
            sequenceLength={flowMode.sequenceLength}
            renderStep={(args) => flowMode.renderStep(args, seekTo)}
            completionMessage={flowMode.completionMessage}
            onExit={() => setFlowModeActive(false)}
          />
        ) : (
          <>
            {tab.goal && <TabIntro goal={tab.goal} />}
            <TabAttachments attachments={tab.attachments} slot="top" nav={nav} />
            <ErrorBoundary fallback={<div className="rounded-lg border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">Failed to render this tab. Try refreshing the page.</div>}>
              {renderer
                ? renderer(tab.props, nav)
                : <DisplaySection data={tab.props} />}
            </ErrorBoundary>
            <TabAttachments attachments={tab.attachments} slot="bottom" nav={nav} />

            {crossLinks.length > 0 && (
              <div className="flex flex-col gap-2 mt-2">
                {crossLinks.map((link) => {
                  const targetTabDef = validatedTabs.find((t: TabEntry) => t.id === link.targetTab);
                  return (
                    <CrossTabLink
                      key={link.targetTab}
                      tabId={link.targetTab}
                      label={targetTabDef ? displayLabel(targetTabDef) : link.label}
                      description={targetTabDef?.goal}
                      onNavigate={onNavigateTab}
                    />
                  );
                })}
              </div>
            )}
          </>
        )}
      </div>
    );
  }

  // ─── v1: Legacy VIEResponse-based rendering ───
  if (!response) return null;

  const activeTabDef = response.tabs.find((t) => t.id === activeTab);
  const tabIds = response.tabs.map((t) => t.id);
  const crossTabLinkMap = resolveCrossTabLinks(tabIds);
  const linksForTab = crossTabLinkMap[activeTab] ?? [];

  if (!activeTabDef) return null;

  const data = resolveTabData(response, activeTab, activeTabDef.dataSource);
  const isInteractive = INTERACTIVE_TABS.has(activeTab);
  const interactiveContent = isInteractive
    ? renderInteractive(activeTab, data, undefined, onNavigateTab)
    : null;

  return (
    <div className="flex flex-col gap-4">
      {interactiveContent ?? <DisplaySection data={data} />}

      {linksForTab.length > 0 && (
        <div className="flex flex-col gap-2 mt-2">
          {linksForTab.map((link) => (
            <CrossTabLink
              key={link.targetTab}
              tabId={link.targetTab}
              label={link.label}
              onNavigate={onNavigateTab}
            />
          ))}
        </div>
      )}
    </div>
  );
});
