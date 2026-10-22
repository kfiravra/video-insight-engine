/**
 * Component registry for assembled (v2) tabs — component name → renderer.
 *
 * Split out of ComposableOutput.tsx (project-score-9 4.4/6.2). Props reaching
 * these renderers are schema-validated once at the tab boundary
 * (tab-prop-schemas.ts via handleTabReady), so renderers use direct typed
 * casts instead of per-field coercion; malformed payloads never get here —
 * they are remapped to display_section upstream. Non-streamed tabs (cached
 * assembledTabs) are covered by the per-tab ErrorBoundary in ComposableOutput.
 */

/* eslint-disable react-refresh/only-export-components --
 * Registry module, not a component file: it exports a name→renderer map and
 * lookup helpers, so fast-refresh boundaries don't apply (same shape as
 * ComposableOutputV1.tsx). */

import { lazy, Suspense, type ReactNode } from 'react';
import { QUIZ_TIMER } from '@vie/shared/config';
import type { SpotItem, FlashcardItem, TabEntry, TechSnippet, StepItem, FitnessExercise, ReviewComparison, FilmstripFrame, PackingMissionItem, QuizArenaQuestion, ConceptItem, LyricsKaraokeSection, StatBannerStat, DiagramCardItem, ConnectPair, ClaimItem, TierListItem, FormationPosition } from '@vie/types';
import { incrementTelemetryCounter } from '@/features/video-output/lib/telemetry';
import { stripCountPrefix } from '@/components/vie/cards/domain-meta';
import { DisplaySection } from './DisplaySection';
import {
  ChecklistInteractive,
  FlashDeckInteractive,
  SpotExplorer,
  StepByStepInteractive,
  MomentTrack,
  ComparisonInteractive,
  BudgetInteractive,
  OverviewInteractive,
  InfoGridInteractive,
  // Video-to-action overhaul: legacy keys now forward to these new components
  VideoFilmstrip,
  CodePlayground,
  QuizArena,
  PackingMission,
  WorkoutRoom,
  LyricsKaraoke,
  // Interactive Overhaul v2 — Phase 5b: news signature component
  ClaimsTracker,
  // Interactive Overhaul v2 — Phase 5c: gaming signature component
  TierList,
  // Interactive Overhaul v2 — Phase 2: secondary-tier (attachment-only)
  StatBanner,
  TipCallout,
  SummaryHeader,
} from './interactive';
import type { MomentItem } from './interactive/MomentTrack';
import type { VerdictData } from './interactive/comparison/ReviewSummary';
import type { OverviewCrossTabLink } from './interactive/OverviewInteractive';

// Heavy canvas + radar components — code-split via React.lazy so they only
// load when a tab actually uses them.
const ConceptCanvas = lazy(() =>
  import('./interactive/ConceptCanvas').then((m) => ({ default: m.ConceptCanvas })),
);
const StepFlowCanvas = lazy(() =>
  import('./interactive/StepFlowCanvas').then((m) => ({ default: m.StepFlowCanvas })),
);
const ConnectCanvas = lazy(() =>
  import('./interactive/ConnectCanvas').then((m) => ({ default: m.ConnectCanvas })),
);
const DiagramCard = lazy(() =>
  import('./interactive/DiagramCard').then((m) => ({ default: m.DiagramCard })),
);
// FormationDiagram uses ReactFlow (heavy) — code-split like the other canvases.
const FormationDiagram = lazy(() =>
  import('./interactive/FormationDiagram').then((m) => ({ default: m.FormationDiagram })),
);

function CanvasFallback() {
  return (
    <div
      className="flex items-center justify-center py-12 text-sm text-muted-foreground"
      role="status"
      aria-live="polite"
    >
      <span className="animate-pulse">Loading canvas…</span>
    </div>
  );
}

export interface NavProps {
  nextTab?: string;
  onNavigateTab: (id: string) => void;
  onSeek?: (seconds: number) => void;
  tabId?: string;
  /** Current tab's display label (e.g. "10 Ingredients") with count prefix
   *  intact. Renderers that want a section-heading version strip the prefix
   *  themselves. Sourced from the translated `tab.label`. */
  tabLabel?: string;
  currentTime?: number;
  contentTag?: string;
  /** Full tab list — used by hub-style renderers (overview) to build sibling
   *  navigation. Optional so renderers that don't need it can ignore it. */
  allTabs?: TabEntry[];
  /** Stable per-video id — wired through for renderers that persist per-video
   *  local state (overview bookmarks). */
  videoId?: string;
  /** Pre-computed Overview cross-tab links, memoized at the parent so the
   *  reference is stable across `currentTime` ticks (would otherwise defeat
   *  `memo()` on OverviewInteractive). */
  derivedOverviewLinks?: OverviewCrossTabLink[];
}

/** Hide the review-summary header when the verdict carries no bottomLine —
 *  ReviewSummary is meaningless without one (matches pre-4.4 behavior). */
function verdictOrUndefined(props: Record<string, unknown>): VerdictData | undefined {
  const verdict = props.verdict as VerdictData | null | undefined;
  return verdict?.bottomLine ? verdict : undefined;
}

/**
 * Registry mapping component names to render functions.
 * Each function receives pre-assembled props from the backend.
 */
export const COMPONENT_REGISTRY: Record<string, (props: Record<string, unknown>, nav: NavProps) => ReactNode> = {
  spot_explorer: (props, nav) => (
    <SpotExplorer
      spots={(props.spots as SpotItem[] | undefined) ?? []}
      sections={props.sections as Array<{ label: string; spotIndices: number[] }> | undefined}
      {...nav}
    />
  ),
  moment_track: (props, nav) => (
    <MomentTrack
      items={(props.items as MomentItem[] | undefined) ?? []}
      filters={typeof props.filters === 'boolean' ? props.filters : undefined}
      onSeek={nav.onSeek}
      currentTime={nav.currentTime}
      {...nav}
    />
  ),
  comparison: (props, nav) => (
    <ComparisonInteractive
      comparisons={(props.comparisons as ReviewComparison[] | undefined) ?? []}
      pros={props.pros as string[] | undefined}
      cons={props.cons as string[] | undefined}
      type={typeof props.type === 'string' ? props.type as 'table' | 'pros_cons' | 'versus' : undefined}
      leftLabel={typeof props.leftLabel === 'string' ? props.leftLabel : undefined}
      rightLabel={typeof props.rightLabel === 'string' ? props.rightLabel : undefined}
      verdict={verdictOrUndefined(props)}
      {...nav}
    />
  ),
  checklist: (props, nav) => (
    <ChecklistInteractive
      items={(props.items as Array<{ label: string; note?: string; emoji?: string }> | undefined) ?? []}
      tabLabel={stripCountPrefix(nav.tabLabel ?? '') || 'Items'}
      groups={props.groups as string[] | undefined}
      scalable={typeof props.scalable === 'boolean' ? props.scalable : undefined}
      baseServings={typeof props.baseServings === 'number' ? props.baseServings : undefined}
      tabId={nav.tabId}
      {...nav}
    />
  ),
  step_player: (props, nav) => (
    <StepByStepInteractive
      steps={(props.steps as StepItem[] | undefined) ?? []}
      mode={typeof props.mode === 'string' ? props.mode as 'scrollable' | 'one_at_a_time' : undefined}
      timers={typeof props.timers === 'boolean' ? props.timers : undefined}
      onSeek={nav.onSeek}
      tabId={nav.tabId}
      {...nav}
    />
  ),
  flash_deck: (props, nav) => (
    <FlashDeckInteractive
      cards={(props.cards as FlashcardItem[] | undefined) ?? []}
      shuffleable={typeof props.shuffleable === 'boolean' ? props.shuffleable : undefined}
      tabId={nav.tabId}
      {...nav}
    />
  ),
  budget: (props, nav) => (
    <BudgetInteractive
      total={typeof props.total === 'number' ? props.total : 0}
      currency={typeof props.currency === 'string' ? props.currency : undefined}
      breakdown={(props.breakdown as Array<{ category: string; amount: number; emoji?: string; notes?: string }> | undefined) ?? []}
      savingTips={props.savingTips as string[] | undefined}
      {...nav}
    />
  ),
  overview: (props, nav) => {
    // Overview props are nested inside props.data from the assembler.
    const d = (typeof props.data === 'object' && props.data !== null ? props.data : props) as Record<string, unknown>;
    const dur = typeof d.duration === 'number'
      ? `${Math.round(Number(d.duration) / 60)} min`
      : typeof d.duration === 'string' ? d.duration : undefined;
    // Sibling-tab nav grid is computed once at the parent (memoized on
    // `tabs` + `activeTab`) so `memo()` on OverviewInteractive holds across
    // `currentTime` ticks. We deliberately stop forwarding
    // title / subtitle / summary / masterSummary — they duplicate the page
    // hero above this tab.
    const derivedLinks = nav.derivedOverviewLinks ?? [];
    return (
      <OverviewInteractive
        tips={Array.isArray(d.tips) ? d.tips as string[] : undefined}
        keyTakeaways={Array.isArray(d.keyTakeaways) ? d.keyTakeaways as string[] : undefined}
        highlights={Array.isArray(d.highlights) ? d.highlights as Array<{ emoji: string; text: string }> : undefined}
        quote={typeof d.quote === 'string' ? d.quote : undefined}
        quoteAuthor={typeof d.quoteAuthor === 'string' ? d.quoteAuthor : undefined}
        duration={dur}
        level={typeof d.level === 'string' ? d.level : typeof d.difficulty === 'string' ? d.difficulty as string : undefined}
        crossTabLinks={derivedLinks.length > 0 ? derivedLinks : undefined}
        videoId={nav.videoId}
        onNavigateTab={nav.onNavigateTab}
      />
    );
  },
  info_grid: (props, nav) => (
    <InfoGridInteractive
      items={(props.items as Array<{ key: string; value: string }> | undefined) ?? []}
      mode={typeof props.mode === 'string' ? props.mode as 'key_value' | 'table' | 'tag_cloud' : undefined}
      sections={props.sections as Array<{ label: string; indices: number[] }> | undefined}
      {...nav}
    />
  ),
  // ─── Video-to-action overhaul: 2026-05-28 ───
  // Legacy keys (code_explorer, exercise_tracker, lyrics_player, scenario,
  // gallery, verdict, quiz) were retired in 2026-05-29 cleanup. Cached
  // assembledTabs rows with those keys now fall through to display_section.
  // The new keys below are the only valid component names going forward —
  // every category renders the new component directly.

  video_filmstrip: (props, nav) => (
    <VideoFilmstrip
      frames={(props.frames as FilmstripFrame[] | undefined) ?? []}
      onSeek={nav.onSeek}
      currentTime={nav.currentTime}
      mode={typeof props.mode === 'string' ? props.mode as 'tab' | 'overlay' : 'tab'}
    />
  ),

  code_playground: (props, nav) => (
    <CodePlayground snippets={(props.snippets as TechSnippet[] | undefined) ?? []} onSeek={nav.onSeek} />
  ),

  quiz_arena: (props, nav) => (
    <QuizArena
      questions={(props.questions as QuizArenaQuestion[] | undefined) ?? []}
      tabId={nav.tabId}
      videoId={nav.videoId}
      nextTab={nav.nextTab}
      onNavigateTab={nav.onNavigateTab}
      onSeek={nav.onSeek}
      withTimer={QUIZ_TIMER}
    />
  ),

  packing_mission: (props, nav) => (
    <PackingMission
      items={(props.items as PackingMissionItem[] | undefined) ?? []}
      videoId={nav.videoId}
      tabId={nav.tabId}
      nextTab={nav.nextTab}
      onNavigateTab={nav.onNavigateTab}
    />
  ),

  workout_room: (props, nav) => (
    <WorkoutRoom
      exercises={(props.exercises as FitnessExercise[] | undefined) ?? []}
      warmup={props.warmup as FitnessExercise[] | undefined}
      cooldown={props.cooldown as FitnessExercise[] | undefined}
      onSeek={nav.onSeek}
    />
  ),

  lyrics_karaoke: (props, nav) => (
    <LyricsKaraoke
      sections={(props.sections as LyricsKaraokeSection[] | undefined) ?? []}
      artist={typeof props.artist === 'string' ? props.artist : undefined}
      onSeek={nav.onSeek}
      currentTime={nav.currentTime}
    />
  ),

  // ─── News signature component (interactive-overhaul-v2 P5b) ───
  claims_tracker: (props, nav) => (
    <ClaimsTracker claims={(props.claims as ClaimItem[] | undefined) ?? []} onSeek={nav.onSeek} />
  ),

  // ─── Gaming signature component (interactive-overhaul-v2 P5c) ───
  tier_list: (props, nav) => (
    <TierList
      items={(props.items as TierListItem[] | undefined) ?? []}
      videoId={nav.videoId}
      tabId={nav.tabId}
    />
  ),

  // ─── Sport signature component (interactive-overhaul-v2 P5d) ───
  // Read-only ReactFlow pitch — lazy-loaded like the other canvases.
  formation_diagram: (props) => (
    <Suspense fallback={<CanvasFallback />}>
      <FormationDiagram
        positions={(props.positions as FormationPosition[] | undefined) ?? []}
        name={typeof props.name === 'string' ? props.name : undefined}
        team={typeof props.team === 'string' ? props.team : undefined}
      />
    </Suspense>
  ),

  // Code-split via React.lazy — each canvas/radar lazy chunk only loads when
  // its tab is opened. Wrap in <Suspense> so the loading flash is contained.
  concept_canvas: (props, nav) => (
    <Suspense fallback={<CanvasFallback />}>
      <ConceptCanvas
        concepts={(props.concepts as ConceptItem[] | undefined) ?? []}
        groups={props.groups as string[] | undefined}
        onSeek={nav.onSeek}
      />
    </Suspense>
  ),

  step_flow_canvas: (props, nav) => (
    <Suspense fallback={<CanvasFallback />}>
      <StepFlowCanvas
        steps={(props.steps as StepItem[] | undefined) ?? []}
        onSeek={nav.onSeek}
        tabId={nav.tabId}
      />
    </Suspense>
  ),

  connect_canvas: (props, nav) => (
    <Suspense fallback={<CanvasFallback />}>
      <ConnectCanvas
        pairs={(props.pairs as ConnectPair[] | undefined) ?? []}
        videoId={nav.videoId}
        tabId={nav.tabId}
        nextTab={nav.nextTab}
        onNavigateTab={nav.onNavigateTab}
      />
    </Suspense>
  ),

  // comparison_radar is an alias for the unified ComparisonInteractive with the
  // radar hero forced on (view="radar"). The 1A promotion comparison→comparison_radar
  // now just sets the radar view; ComparisonRadar.tsx was merged + deleted in P3C.
  comparison_radar: (props, nav) => (
    <ComparisonInteractive
      comparisons={(props.comparisons as ReviewComparison[] | undefined) ?? []}
      pros={props.pros as string[] | undefined}
      cons={props.cons as string[] | undefined}
      leftLabel={typeof props.leftLabel === 'string' ? props.leftLabel : undefined}
      rightLabel={typeof props.rightLabel === 'string' ? props.rightLabel : undefined}
      verdict={verdictOrUndefined(props)}
      view="radar"
      {...nav}
    />
  ),

  // ─── Secondary-tier components (interactive-overhaul-v2 P2) ───
  // Attachment-only: rendered by <TabAttachments> around a primary, never as a
  // standalone tab. Registered here so REGISTERED_COMPONENT_NAMES includes them
  // and the parity test stays green.
  stat_banner: (props, nav) => (
    <StatBanner stats={(props.stats as StatBannerStat[] | undefined) ?? []} onSeek={nav.onSeek} />
  ),

  tip_callout: (props) => (
    <TipCallout
      text={typeof props.text === 'string' ? props.text : ''}
      style={props.style === 'warning' || props.style === 'note' ? props.style : 'tip'}
      title={typeof props.title === 'string' ? props.title : undefined}
    />
  ),

  summary_header: (props) => (
    <SummaryHeader
      summary={typeof props.summary === 'string' ? props.summary : ''}
      title={typeof props.title === 'string' ? props.title : undefined}
      emoji={typeof props.emoji === 'string' ? props.emoji : undefined}
    />
  ),

  diagram_card: (props) => (
    <Suspense fallback={<CanvasFallback />}>
      <DiagramCard
        nodes={(props.nodes as DiagramCardItem[] | undefined) ?? []}
        edges={props.edges as Array<{ source: number; target: number }> | undefined}
        caption={typeof props.caption === 'string' ? props.caption : undefined}
      />
    </Suspense>
  ),

  // frame_strip = VideoFilmstrip in compact overlay mode (reuses primary renderer).
  frame_strip: (props, nav) => (
    <VideoFilmstrip
      frames={(props.frames as FilmstripFrame[] | undefined) ?? []}
      onSeek={nav.onSeek}
      currentTime={nav.currentTime}
      mode="overlay"
    />
  ),

  // quick_quiz = single-question QuizArena (reuses primary renderer).
  quick_quiz: (props, nav) => (
    <QuizArena
      questions={(props.questions as QuizArenaQuestion[] | undefined) ?? []}
      tabId={nav.tabId}
      videoId={nav.videoId}
      onNavigateTab={nav.onNavigateTab}
      onSeek={nav.onSeek}
      withTimer={QUIZ_TIMER}
    />
  ),

  // Fallback to DisplaySection
  display_section: (props) => (
    <DisplaySection data={props.data} />
  ),
};

/** Component names with a registered renderer. Exported for the contract-parity
 *  test (interactive-overhaul-v2 1F), which fails the build if a component the
 *  backend can emit has no frontend renderer (would silently fall to
 *  DisplaySection). */
export const REGISTERED_COMPONENT_NAMES: ReadonlySet<string> = new Set(
  Object.keys(COMPONENT_REGISTRY),
);

/**
 * Resolve a component's renderer, counting unknown-component fallbacks.
 * Streamed tabs are already remapped upstream (handleTabReady), so a miss here
 * means a cached/DB tab carries a name the registry doesn't know — telemetry
 * makes that drift prod-visible before it becomes a bug report.
 */
export function resolveRenderer(
  component: string,
): ((props: Record<string, unknown>, nav: NavProps) => ReactNode) | undefined {
  const renderer = COMPONENT_REGISTRY[component];
  if (!renderer && component) {
    incrementTelemetryCounter('tab_component_unknown');
    if (import.meta.env.DEV) {
      console.warn(`[component-registry] Unknown component "${component}", falling back to DisplaySection`);
    }
  }
  return renderer;
}
