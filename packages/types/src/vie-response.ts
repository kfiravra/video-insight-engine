// ═══════════════════════════════════════════════════
// VIE Response — Domain-Based Composable Types
// ═══════════════════════════════════════════════════

import {
  CONTENT_TAG_VALUES,
  MODIFIER_VALUES,
  type ContentTag,
  type Modifier,
} from '@vie/shared/config';
import type {
  ConceptItem,
  FlashcardItem,
  FrameEvidence,
  QuizItem,
  ScenarioItem,
  StepItem,
} from './vie-response-items.js';
import type {
  FinanceData,
  FitnessData,
  FitnessExercise,
  FoodData,
  GamingData,
  LearningData,
  MusicData,
  NarrativeData,
  NewsData,
  PodcastData,
  ProjectData,
  ReviewComparison,
  ReviewData,
  SportData,
  TechData,
  TechSnippet,
  TravelData,
} from './vie-response-domains.js';

// Tab item types and domain data interfaces live in their own modules (the
// 500-line rule); re-exported here so every import path stays as it was.
export * from './vie-response-items.js';
export * from './vie-response-domains.js';

// ─────────────────────────────────────────────────────
// Content Tags & Modifiers (derived from @vie/shared)
// ─────────────────────────────────────────────────────

export { CONTENT_TAG_VALUES, MODIFIER_VALUES };
export type { ContentTag, Modifier };

// ─────────────────────────────────────────────────────
// Tab & Section Definitions (LLM-designed)
// ─────────────────────────────────────────────────────

/** Legacy tab shape from triage (no component/props). */
export interface TabDefinition {
  id: string;
  label: string;
  emoji: string;
  dataSource: string;
  goal?: string;
  /** When true, the tab is activatable programmatically (e.g. via a hero button)
   *  but is not rendered in the tab bar. Used for overview tabs that have been
   *  promoted to a hero-level quick-nav control. */
  hidden?: boolean;
}

// ─────────────────────────────────────────────────────
// Assembled Tab (v2 — component-addressed)
// ─────────────────────────────────────────────────────

export interface CrossTabLinkDef {
  targetTab: string;
  label: string;
}

/** Secondary-tier component attached above or below a tab's primary interactive.
 *  Attachments enrich sparse tabs (frame strip, quick quiz, tip) and break up
 *  dense ones (summary header). They are never standalone tabs — only the
 *  primary `component` on a `TabEntry` can be planner-selected. */
export interface TabAttachment {
  /** Render position relative to the primary interactive. */
  slot: 'top' | 'bottom';
  /** Secondary-tier component name (must have a COMPONENT_REGISTRY renderer). */
  component: string;
  props: Record<string, unknown>;
  /** Visual footprint hint — `banner` spans full width, `strip` is compact. */
  size?: 'banner' | 'strip';
}

/** Backend-assembled tab: specifies which interactive to render and pre-resolved props. */
export interface TabEntry {
  id: string;
  label: string;
  emoji: string;
  component: string;
  props: Record<string, unknown>;
  goal?: string;
  crossTabLinks?: CrossTabLinkDef[];
  /** Optional secondary-tier attachments rendered around the primary. Absent
   *  on flat tabs, which render exactly as before. */
  attachments?: TabAttachment[];
  /** Set when the assembler degraded this tab from a richer component whose
   *  assembler couldn't build from the data (degrade-never-drop ladder).
   *  Telemetry/debug only — rendering keys off `component` as usual. */
  degradedFrom?: string;
}

// ─────────────────────────────────────────────────────
// Triage Result
// ─────────────────────────────────────────────────────

export interface TriageResult {
  contentTags: ContentTag[];
  modifiers: Modifier[];
  primaryTag: ContentTag;
  userGoal: string;
  tabs: TabDefinition[];
  confidence: number;
}

// ─────────────────────────────────────────────────────
// Video-to-Action overhaul: new component prop types
// ─────────────────────────────────────────────────────

/** Verdict header rendered above ComparisonInteractive via ReviewSummary. */
export interface VerdictHeader {
  badge?: string;
  bottomLine?: string;
  bestFor?: string[];
  notFor?: string[];
  score?: number;
  maxScore?: number;
  subScores?: Array<{ category: string; score: number }>;
}

export interface ConceptCanvasProps {
  concepts: ConceptItem[];
  /** Ordered, de-duped group labels for the lanes + Groups view. */
  groups?: string[];
  onSeek?: (seconds: number) => void;
  videoId?: string;
  nextTab?: string;
  onNavigateTab?: (id: string) => void;
}

export interface StepFlowCanvasProps {
  steps: StepItem[];
  onSeek?: (seconds: number) => void;
  tabId?: string;
}

/**
 * @deprecated ComparisonRadar was merged into ComparisonInteractive (P3C). The
 * `comparison_radar` component is now a registry alias that renders
 * ComparisonInteractive with the radar hero forced on. Kept for backward type
 * compatibility; new code should use ComparisonInteractive's `view="radar"`.
 */
export interface ComparisonRadarProps {
  comparisons: ReviewComparison[];
  leftLabel?: string;
  rightLabel?: string;
}

/** One graded match in a ConnectCanvas quiz: a left-column prompt and the
 *  right-column answer it should connect to. `match` is the answer-key value
 *  (the connected concept the prompt belongs with). */
export interface ConnectPair {
  prompt: string;
  match: string;
}

export interface ConnectCanvasProps {
  pairs: ConnectPair[];
  videoId?: string;
  tabId?: string;
  nextTab?: string;
  onNavigateTab?: (id: string) => void;
}

export interface CodePlaygroundProps {
  snippets: TechSnippet[];
  onSeek?: (seconds: number) => void;
}

export interface QuizArenaQuestion extends FrameEvidence {
  question: string;
  options: string[];
  correctIndex: number;
  explanation?: string;
  context?: string;
  kind?: 'standard' | 'scenario';
  thumbnailUrl?: string;
  timestamp?: number;
  /** The speaker's words that settle the answer, verbatim from the transcript (quiz v2,
   *  output-quality 2.1b) — shown under the explanation once the question is answered. */
  quote?: string | null;
  /** The quote is the English rendering of non-English speech (2.0 d, D12): prose. */
  quoteTranslated?: boolean;
}

export interface QuizArenaProps {
  questions: QuizArenaQuestion[];
  tabId?: string;
  nextTab?: string;
  onNavigateTab?: (id: string) => void;
  videoId?: string;
}

export interface PackingMissionItem {
  item: string;
  category?: string;
  essential?: boolean;
  weight?: number;
  emoji?: string;
}

export interface PackingMissionProps {
  items: PackingMissionItem[];
  videoId?: string;
  tabId?: string;
  nextTab?: string;
  onNavigateTab?: (id: string) => void;
}

export interface WorkoutRoomProps {
  exercises: FitnessExercise[];
  warmup?: FitnessExercise[];
  cooldown?: FitnessExercise[];
  onSeek?: (seconds: number) => void;
}

export interface LyricsKaraokeLine {
  text: string;
  timestamp?: number;
  words?: Array<{ text: string; startTime: number; endTime: number }>;
}

export interface LyricsKaraokeSection {
  name: string;
  timestamp?: number;
  lines: LyricsKaraokeLine[];
}

export interface LyricsKaraokeProps {
  sections: LyricsKaraokeSection[];
  lines?: LyricsKaraokeLine[];
  artist?: string;
  onSeek?: (seconds: number) => void;
  currentTime?: number;
}

export interface FilmstripFrame {
  thumbnailUrl: string;
  timestamp: number;
  caption?: string;
  ocr?: string;
  sceneType?: string;
}

export interface VideoFilmstripProps {
  frames: FilmstripFrame[];
  onSeek?: (seconds: number) => void;
  currentTime?: number;
  mode?: 'tab' | 'overlay';
}

// ─────────────────────────────────────────────────────
// Secondary-tier component prop types (attachment-only)
// ─────────────────────────────────────────────────────

/** One equal-weight stat in a StatBanner. Deliberately NOT a hero metric. */
export interface StatBannerStat {
  label: string;
  value: string;
  emoji?: string;
  /** The key point the figure comes from — what the number means. */
  meaning?: string;
  /** Where the video states the figure, in seconds. */
  timestamp?: number;
}

export interface StatBannerProps {
  stats: StatBannerStat[];
}

export interface TipCalloutProps {
  /** Visual treatment — full border + bg tint, never a side-stripe. */
  style?: 'tip' | 'warning' | 'note';
  text: string;
  title?: string;
}

export interface SummaryHeaderProps {
  /** One-line orientation for a dense tab. */
  summary: string;
  title?: string;
  emoji?: string;
}

export interface DiagramCardItem {
  label: string;
  detail?: string;
  emoji?: string;
}

/** A directed edge between two diagram nodes, addressed by node index. */
export interface DiagramCardEdge {
  source: number;
  target: number;
}

export interface DiagramCardProps {
  /** Ordered nodes shown as a read-only ReactFlow diagram. */
  nodes: DiagramCardItem[];
  /** Explicit edges (from concept connections); falls back to a sequential
   *  chain when omitted. */
  edges?: DiagramCardEdge[];
  caption?: string;
}

// ─────────────────────────────────────────────────────
// VIE Response Meta
// ─────────────────────────────────────────────────────

export interface VIEResponseMeta {
  videoId: string;
  videoTitle: string;
  creator: string;
  contentTags: ContentTag[];
  modifiers: Modifier[];
  primaryTag: ContentTag;
  userGoal: string;
  tldr?: string;
  /** Each a claim closing with the `[m:ss]` (`[h:mm:ss]`) where it is said, when known. */
  keyTakeaways?: string[];
  masterSummary?: string;
  seoDescription?: string;
  /** The hero rewrite's 3–5 sentence brief (output-quality 2.2); `masterSummary`
   *  carries the same text for older readers. Absent on older rows. */
  brief?: string;
  /** Who gets what from watching (≤ 20 words); absent or empty when not written. */
  worthItFor?: string;
  /** What someone might come for that the video does not contain (≤ 20 words). */
  skipIf?: string;
  /** Minutes to read the page, counted by code over the shipped hero + tabs. */
  readingMinutes?: number;
  /** ISO 639-1 language code (e.g., "en", "he"). */
  language?: string;
  /** Whether the video content is in a right-to-left language. */
  isRTL?: boolean;
  /**
   * Partial-result flag: extraction dropped batches or coverage was critical.
   * Set (true only) by the summarizer assembly phase; the web renders a
   * "partial result — retry" affordance when present.
   */
  degraded?: boolean;
}

// ─────────────────────────────────────────────────────
// VIE Response
// ─────────────────────────────────────────────────────

export interface VIEResponse {
  meta: VIEResponseMeta;
  tabs: TabDefinition[];

  // Domain data — only populated domains present
  travel?: TravelData;
  food?: FoodData;
  tech?: TechData;
  fitness?: FitnessData;
  music?: MusicData;
  learning?: LearningData;
  review?: ReviewData;
  project?: ProjectData;
  podcast?: PodcastData;
  news?: NewsData;
  gaming?: GamingData;
  sport?: SportData;

  // Modifier enrichment
  narrative?: NarrativeData;
  finance?: FinanceData;

  // Stage 3 enrichment
  quizzes?: QuizItem[];
  scenarios?: ScenarioItem[];
  flashcards?: FlashcardItem[];
}

// ─────────────────────────────────────────────────────
// Celebration Config (resolved by post-processing)
// ─────────────────────────────────────────────────────

export interface CelebrationConfig {
  tabId: string;
  emoji: string;
  title: string;
  subtitle?: string;
  nextTabId?: string;
  nextLabel?: string;
}

// ─────────────────────────────────────────────────────
// SSE Events
// ─────────────────────────────────────────────────────

export interface SSETriageCompleteEvent {
  event: 'triage_complete';
  contentTags: ContentTag[];
  modifiers: Modifier[];
  primaryTag: ContentTag;
  userGoal: string;
  tabs: TabDefinition[];
  confidence: number;
}

type ExtractionCompleteDomain =
  'travel' | 'food' | 'tech' | 'fitness' | 'music' | 'learning' | 'review' | 'project';

export interface SSEDomainExtractionCompleteEvent {
  event: 'extraction_complete';
  /**
   * Domain-keyed. The split extraction (EXTRACTION_PARALLEL, pipeline-1min 3.5)
   * sends only the keys its field groups requested, so each domain object is partial.
   */
  data: { [K in ExtractionCompleteDomain]?: Partial<NonNullable<VIEResponse[K]>> };
}

// ─────────────────────────────────────────────────────
// Type Guards
// ─────────────────────────────────────────────────────

export function isContentTag(value: string): value is ContentTag {
  return CONTENT_TAG_VALUES.includes(value as ContentTag);
}

export function isModifier(value: string): value is Modifier {
  return MODIFIER_VALUES.includes(value as Modifier);
}

export function isVIEResponse(response: unknown): response is VIEResponse {
  return (
    typeof response === 'object' &&
    response !== null &&
    'meta' in response &&
    'tabs' in response
  );
}
