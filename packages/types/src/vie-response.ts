// ═══════════════════════════════════════════════════
// VIE Response — Domain-Based Composable Types
// ═══════════════════════════════════════════════════

import {
  CONTENT_TAG_VALUES,
  MODIFIER_VALUES,
  type ContentTag,
  type Modifier,
} from '@vie/shared/config';

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
// Shared Item Types
// ─────────────────────────────────────────────────────

/** Frame-evidence fields the assembler attaches via `inject_frame_thumbnails`
 *  when a vision-LLM caption is available for the item's timestamp. Extended by
 *  `SpotItem`, `StepItem`, and `MomentItem` — the three item shapes that carry
 *  per-row timestamps. Components rendering those rows pick up these props
 *  uniformly so frame intelligence reaches the UI. */
export interface FrameEvidence {
  /** Vision-LLM single-line caption for the frame at this timestamp. */
  frameCaption?: string;
  /** Vision-LLM rationale for why this frame supports/illustrates the row. */
  frameEvidence?: string;
  /** On-screen text (Tesseract OCR or vision-read) visible in the frame. */
  frameOcr?: string;
  /** Scene type — "slide", "code", "diagram", "demo", "whiteboard", etc. */
  frameSceneType?: string;
  /** Durable S3 object key for the attached frame — the API re-signs a fresh
   *  presigned URL from it on read (thumbnailUrl expires with the presign). */
  s3Key?: string;
}

export interface SpotItem extends FrameEvidence {
  name: string;
  emoji: string;
  description: string;
  cost?: string;
  currency?: string;
  duration?: string;
  mapQuery?: string;
  bookingSearch?: string;
  tips?: string;
  specs?: string;
  /** Phonetic guide for language-learning phrase cards (e.g. "su-mi-ma-sen"). */
  pronunciation?: string;
  rating?: number;
  thumbnailUrl?: string;
  /** Where the spot is discussed (seconds) — the seek chip (output-quality R2). */
  timestamp?: number | null;
  /** The speaker's verbatim line about the spot (output-quality R10). */
  quote?: string | null;
  /** Category badge text — a key idea's kind ("Thesis", "Ask"), set by the assembler. */
  category?: string;
  /** Who holds a key idea when more than one person speaks — set by the assembler. */
  speaker?: string;
}

export interface TipItem {
  type: string;
  text: string;
}

export interface StepItem extends FrameEvidence {
  number: number;
  title?: string;
  instruction: string;
  duration?: string;
  tips?: string;
  safetyNote?: string;
  timestamp?: number;
  thumbnailUrl?: string;
}

/**
 * Relationship type between two concepts, encoded on the canvas by line-style +
 * arrowhead (never hue — see DESIGN.md "One Domain, One Accent"):
 *   causes / requires → solid line + arrowhead (directional dependency)
 *   contrasts        → dashed line (opposition / trade-off)
 *   partOf / relatesTo → dotted line (loose association)
 */
export type ConceptRelation = 'causes' | 'contrasts' | 'requires' | 'partOf' | 'relatesTo';

export interface ConceptConnection {
  /** EXACT name of another concept in the same `concepts` array. */
  to: string;
  type: ConceptRelation;
}

export interface ConceptItem extends FrameEvidence {
  name: string;
  emoji: string;
  definition: string;
  example?: string;
  analogy?: string;
  /** Short thematic cluster label (e.g. "Foundations"). Assembler defaults/derives one. */
  group?: string;
  /** Seconds into the video where this concept is explained — drives frame evidence. */
  timestamp?: number;
  /** Frame thumbnail injected by `inject_frame_thumbnails` when a timestamp matches. */
  thumbnailUrl?: string;
  /**
   * Typed adjacency list. A bare string is legacy data (cached pre-v6 rows) and
   * is treated as a `relatesTo` connection by the canvas normalizer.
   */
  connections: Array<string | ConceptConnection>;
}

export interface KeyPointItem {
  emoji: string;
  title: string;
  detail: string;
  timestamp?: number;
}

/** What kind of argued idea a key idea is (output-quality D10) — its badge. */
export type KeyIdeaKind = 'thesis' | 'argument' | 'announcement' | 'number' | 'story' | 'ask';

/** One argued idea of a talk, lecture or podcast (`learning.keyIdeas`, D10). The
 *  assembler ships it as a spot card: claim → name, why → description, quote,
 *  timestamp, section → the spot sections, kind → the category badge. */
export interface KeyIdeaItem {
  /** The idea as the speaker argues it — one sentence a reader could disagree with. */
  claim: string;
  /** The speaker's reasoning or evidence for the claim. */
  why: string;
  /** The speaker's own words that carry the claim, verbatim (≤ 25 words). */
  quote?: string | null;
  /** Seconds into the video where the claim is said. */
  timestamp?: number | null;
  /** The part of the video the idea belongs to. */
  section?: string | null;
  kind?: KeyIdeaKind | null;
  /** Who holds the idea when more than one person speaks (a podcast's positions). */
  speaker?: string | null;
  /** A number idea's figure as the speaker says it — a numbers-strip tile. */
  figure?: KeyIdeaFigure | null;
}

/** The figure a `kind: number` key idea states, as said ("99 %", "less backend CPU"). */
export interface KeyIdeaFigure {
  value: string;
  label: string;
}

export interface MomentItem extends FrameEvidence {
  time: string;
  seconds: number;
  /** Present ⇒ clip (span worth re-watching). Absent ⇒ moment (navigation point). */
  endSeconds?: number;
  label: string;
  description?: string;
  /** The speaker's line that carries the moment, verbatim (output-quality R7/R10). */
  quote?: string;
  /** An interview exchange's full question (`podcast.qa`); its head is the label. */
  question?: string;
  mood?: string;
  emoji?: string;
  speaker?: string;
  tags?: string[];
  thumbnailUrl?: string;
}

export interface MomentTrackProps {
  items: MomentItem[];
  filters?: boolean;
  onSeek?: (seconds: number) => void;
  currentTime?: number;
  nextTab?: string;
  onNavigateTab?: (id: string) => void;
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
// claims_tracker — news signature component
// ─────────────────────────────────────────────────────

/** What the video does with a claim (output-quality D11): it states it, supports
 *  it with evidence, or someone in it contests it — never a verdict of ours. */
export type ClaimStatus = 'stated' | 'supported' | 'contested';

/** The pre-1.4 words stored rows still carry; the tracker reads verified as
 *  supported, disputed as contested and context as stated (D7). */
export type LegacyClaimStatus = 'verified' | 'disputed' | 'context';

/** A single claim made in the video: the assertion, who made it, its
 *  verification status, and the supporting source (if any). */
export interface ClaimItem extends FrameEvidence {
  claim: string;
  /** Who said / made the claim; absent when the video attributes it to no one. */
  source?: string | null;
  status: ClaimStatus | LegacyClaimStatus;
  /** Where the claim's verification comes from (study, outlet, official record). */
  sourceCitation?: string;
  timestamp?: number;
}

export interface ClaimsTrackerProps {
  claims: ClaimItem[];
}

// ─────────────────────────────────────────────────────
// tier_list — gaming signature component
// ─────────────────────────────────────────────────────

/** A tier in an S/A/B/C/D ranking. */
export type TierRank = 'S' | 'A' | 'B' | 'C' | 'D';

/** One rankable item. `tier` is the creator's suggested placement (the answer
 *  key); the viewer can drag it into any tier and the choice persists locally. */
export interface TierListItem {
  item: string;
  tier?: TierRank;
  reason?: string;
  emoji?: string;
  /** Where the creator places the item (seconds); a tier ships only with it (D13). */
  timestamp?: number;
}

export interface TierListProps {
  items: TierListItem[];
  videoId?: string;
  tabId?: string;
}

// ─────────────────────────────────────────────────────
// formation_diagram — sport signature component
// ─────────────────────────────────────────────────────

/** One player placed on the pitch. x/y are 0-100 percentages: x = left→right,
 *  y = own-goal end (0) → attacking end (100). */
export interface FormationPosition {
  player: string;
  role?: string;
  x: number;
  y: number;
  number?: number;
}

export interface FormationDiagramProps {
  positions: FormationPosition[];
  name?: string;
  team?: string;
}

export interface QuizQuestion {
  question: string;
  options: string[];
  correctIndex: number;
  explanation: string;
}

/** Alias for QuizQuestion for domain usage. */
export type QuizItem = QuizQuestion;

export interface Flashcard {
  front: string;
  back: string;
}

/** Extends Flashcard with optional display metadata */
export interface FlashcardItem extends Flashcard {
  emoji?: string;
  category?: string;
}

export interface ScenarioOption {
  text: string;
  correct: boolean;
  explanation: string;
}

export interface ScenarioItem {
  question: string;
  emoji?: string;
  options: ScenarioOption[];
}

// ─────────────────────────────────────────────────────
// Domain Data Interfaces (8 primary domains)
// ─────────────────────────────────────────────────────

// --- Travel ---
export interface TravelDay {
  day: number;
  city?: string;
  theme?: string;
  dailyCost?: string;
  spots: SpotItem[];
  tips: string[];
}

export interface TravelBudget {
  total: number;
  currency: string;
  breakdown: { category: string; amount: number; notes?: string }[];
}

export interface TravelPackingItem {
  item: string;
  category: string;
  essential: boolean;
  weight?: number;
  emoji?: string;
}

export interface TravelTip {
  text: string;
  type: 'tip' | 'warning' | 'info';
}

export interface TravelData {
  bestSeason?: string;
  accommodationTips: TravelTip[];
  transportationTips: TravelTip[];
  itinerary: TravelDay[];
  budget: TravelBudget;
  packingList: TravelPackingItem[];
}

// --- Food ---
export interface FoodIngredient {
  name: string;
  amount: number;
  displayAmount: string;
  unit?: string;
  group?: string;
  notes?: string;
}

export interface FoodMeta {
  prepTime?: number;
  cookTime?: number;
  totalTime?: number;
  servings?: number;
  difficulty?: 'easy' | 'medium' | 'hard';
  cuisine?: string;
}

export interface FoodData {
  meta: FoodMeta;
  ingredients: FoodIngredient[];
  steps: StepItem[];
  tips: TipItem[];
  substitutions: { original: string; substitute: string; notes?: string }[];
  nutrition: { nutrient: string; amount: string; unit?: string }[];
  equipment: string[];
}

// --- Tech ---
export interface TechSetup {
  commands: string[];
  dependencies: { name: string; version?: string }[];
  envVars: { name: string; description: string; example?: string }[];
}

export interface TechSnippet {
  filename?: string;
  language: string;
  code: string;
  explanation: string;
  timestamp?: number;
}

export interface TechPattern {
  title: string;
  doExample: string;
  dontExample: string;
  explanation: string;
}

export interface TechCheatSheetItem {
  title: string;
  code: string;
  description: string;
}

/** A concept a tutorial explains, with where it explains it (output-quality 1.3). */
export interface TechTopic {
  name: string;
  description: string;
  /** Seconds into the video where the topic is explained. */
  timestamp?: number | null;
}

export interface TechData {
  languages: string[];
  frameworks: string[];
  /** Objects since output-quality 1.3; rows stored before it carry bare names. */
  topics: Array<TechTopic | string>;
  setup: TechSetup;
  snippets: TechSnippet[];
  patterns: TechPattern[];
  cheatSheet: TechCheatSheetItem[];
  /** Checkable claims a tech talk makes (output-quality D11). */
  claims?: ClaimItem[];
}

// --- Fitness ---
export interface FitnessExercise {
  name: string;
  emoji: string;
  sets?: number;
  reps?: string;
  duration?: string;
  rest?: string;
  difficulty?: 'beginner' | 'intermediate' | 'advanced';
  formCues: string[];
  modifications: { label: string; description: string }[];
  supersetWith?: string;
  timestamp?: number;
}

export interface FitnessTimer {
  intervals: { name: string; duration: number; type: 'work' | 'rest' | 'warmup' | 'cooldown' }[];
  rounds: number;
}

export interface FitnessData {
  meta: {
    type: string;
    /** Absent when the video states no level. */
    difficulty?: 'beginner' | 'intermediate' | 'advanced';
    duration: number;
    muscleGroups: string[];
    equipment: string[];
    caloriesBurned?: number;
  };
  warmup: FitnessExercise[];
  exercises: FitnessExercise[];
  cooldown: FitnessExercise[];
  timer: FitnessTimer | null;
  tips: TipItem[];
}

// --- Music ---

export interface MusicCredit {
  role: string;
  name: string;
}

export interface MusicSection {
  name: string;
  timestamp?: number;
  endTimestamp?: number;
  duration?: number;
  description: string;
}

export interface MusicAnalysisItem {
  aspect: string;
  emoji: string;
  detail: string;
}

export interface MusicData {
  title: string;
  artist: string;
  genre: string[];
  credits: MusicCredit[];
  analysis: MusicAnalysisItem[];
  structure: MusicSection[];
  lyrics: { timestamp?: number; line: string }[];
  themes: string[];
}

// --- Learning ---
export interface LearningData {
  keyQuestion?: string;
  keyPoints: KeyPointItem[];
  /** The argument tab of a talk, lecture or podcast; keyPoints stays for explainers. */
  keyIdeas?: KeyIdeaItem[];
  /** Checkable claims of a talk or lecture (output-quality D11). */
  claims?: ClaimItem[];
  concepts: ConceptItem[];
  takeaways: string[];
  timestamps: MomentItem[];
  summary?: string;
}

// --- Review ---
export interface ReviewRating {
  score: number;
  maxScore: number;
  label: string;
  /** Where the reviewer gives the score (seconds); the score renders only with it. */
  timestamp?: number | null;
}

export interface ReviewSpec {
  key: string;
  value: string;
}

export interface ReviewComparison {
  feature: string;
  thisProduct: string;
  competitor: string;
  competitorName: string;
  winner?: 'left' | 'right' | 'tie';
}

export interface ReviewVerdict {
  /** Absent when the video gives no verdict. */
  badge?: 'recommended' | 'not_recommended' | 'conditional' | 'best_in_class';
  bestFor: string[];
  notFor: string[];
  bottomLine: string;
  subScores?: Array<{ category: string; score: number }>;
  /** Where the reviewer states the verdict (seconds); the badge renders only with it. */
  timestamp?: number | null;
}

export interface ReviewData {
  product: string;
  price?: string;
  rating: ReviewRating;
  pros: string[];
  cons: string[];
  specs: ReviewSpec[];
  comparisons: ReviewComparison[];
  verdict: ReviewVerdict;
}

// --- Project ---

export interface ProjectMaterial {
  name: string;
  quantity?: string;
  cost?: string;
  notes?: string;
}

export interface ProjectTool {
  name: string;
  required: boolean;
  alternative?: string;
}

export interface ProjectData {
  /** Absent when the video names no project. */
  projectName?: string;
  /** Absent when the video states no level. */
  difficulty?: 'beginner' | 'intermediate' | 'advanced';
  estimatedTime: string;
  estimatedCost?: string;
  materials: ProjectMaterial[];
  tools: ProjectTool[];
  steps: StepItem[];
  safetyWarnings: string[];
}

// --- Podcast ---

export interface PodcastSegment {
  title: string;
  summary?: string;
  timestamp?: number;
  endSeconds?: number;
  speaker?: string;
  /** The line that carries the segment's conclusion, verbatim. */
  quote?: string | null;
}

/** One interview exchange (`podcast.qa`, output-quality 1.3): ships as a moment —
 *  the question heads the row, the answer is its description. */
export interface PodcastQAItem {
  question: string;
  answer: string;
  /** Who answers — the guest, usually. */
  speaker?: string | null;
  timestamp?: number | null;
  quote?: string | null;
}

export interface PodcastGuest {
  name: string;
  role?: string;
  description?: string;
  emoji?: string;
}

export interface PodcastQuote {
  quote: string;
  speaker?: string;
  timestamp?: number;
}

export interface PodcastTopic {
  topic: string;
  detail?: string;
}

export interface PodcastData {
  show?: string;
  host?: string;
  segments: PodcastSegment[];
  /** An interview's questions and answers (output-quality 1.3). */
  qa?: PodcastQAItem[];
  guests: PodcastGuest[];
  quotes: PodcastQuote[];
  topics: PodcastTopic[];
  /** Checkable claims made on the show (output-quality D11). */
  claims?: ClaimItem[];
}

// --- News ---

export interface NewsTimelineEvent {
  label: string;
  description?: string;
  timestamp?: number;
}

export interface NewsEntity {
  name: string;
  role?: string;
  description?: string;
  emoji?: string;
}

export interface NewsContextItem {
  key: string;
  value: string;
}

export interface NewsData {
  headline?: string;
  storyTimeline: NewsTimelineEvent[];
  entities: NewsEntity[];
  claims: ClaimItem[];
  context: NewsContextItem[];
}

// --- Gaming ---

export interface GamingHighlight {
  label: string;
  description?: string;
  timestamp?: number;
}

export interface GamingLoadoutItem {
  item: string;
  category?: string;
  note?: string;
}

export interface GamingWalkthroughStep {
  instruction: string;
  timestamp?: number;
}

export interface GamingData {
  title?: string;
  highlights: GamingHighlight[];
  loadout: GamingLoadoutItem[];
  walkthrough: GamingWalkthroughStep[];
  /** Creator rankings — same shape as the tier_list component items. */
  rankings: TierListItem[];
}

// --- Sport ---

export interface SportMatchEvent {
  label: string;
  description?: string;
  timestamp?: number;
}

export interface SportFormation {
  name?: string;
  team?: string;
  /** Players placed on the pitch — same shape as formation_diagram positions. */
  positions: FormationPosition[];
}

export interface SportStatRow {
  feature: string;
  left?: string;
  right?: string;
}

export interface SportStatComparison {
  leftLabel?: string;
  rightLabel?: string;
  comparisons: SportStatRow[];
}

export interface SportData {
  title?: string;
  matchEvents: SportMatchEvent[];
  formation?: SportFormation;
  statComparison?: SportStatComparison;
}

// ─────────────────────────────────────────────────────
// Modifier Data Interfaces
// ─────────────────────────────────────────────────────

export interface NarrativeKeyMoment {
  timestamp: number;
  endTimestamp?: number;
  description: string;
  mood?: string;
  emoji?: string;
}

export interface NarrativeQuote {
  text: string;
  speaker: string;
  timestamp?: number;
  context?: string;
}

export interface NarrativeData {
  keyMoments: NarrativeKeyMoment[];
  quotes: NarrativeQuote[];
  takeaways: string[];
}

export interface FinanceData {
  costs: { item: string; amount: number; currency: string; category: string }[];
  savingTips: string[];
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
  keyTakeaways?: string[];
  masterSummary?: string;
  seoDescription?: string;
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
