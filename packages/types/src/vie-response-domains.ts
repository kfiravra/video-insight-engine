// ═══════════════════════════════════════════════════
// VIE Response — domain and modifier data interfaces. Re-exported from
// vie-response.ts.
// ═══════════════════════════════════════════════════

import type {
  ClaimItem,
  ConceptItem,
  FormationPosition,
  KeyIdeaItem,
  KeyPointItem,
  MomentItem,
  SpotItem,
  StepItem,
  TierListItem,
  TipItem,
} from './vie-response-items.js';

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
  /** Only as the speaker states it (D13): no stated number → null, never 0. */
  amount?: number | null;
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

export interface FitnessMeta {
  type: string;
  /** Absent when the video states no level. */
  difficulty?: 'beginner' | 'intermediate' | 'advanced';
  /** Absent when the instructor states no length (D13) — never a default 0. */
  duration?: number | null;
  muscleGroups: string[];
  equipment: string[];
  caloriesBurned?: number;
}

export interface FitnessData {
  meta: FitnessMeta;
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
  /** Where the reviewer gives the score (seconds); the score renders only with it and
   *  the words that give it (`quote`, 2.0 c). */
  timestamp?: number | null;
  quote?: string | null;
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
  subScores?: Array<{ category: string; score: number; timestamp?: number | null; quote?: string | null }>;
  /** Where the reviewer states the verdict (seconds); the badge renders only with it and
   *  the words that state it (`quote`, 2.0 c). */
  timestamp?: number | null;
  quote?: string | null;
  /** The quote's English on a non-English video (D12, 4.2). */
  quoteEn?: string | null;
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
  /** The quote's English on a non-English video (D12, 4.2). */
  quoteEn?: string | null;
}

/** One interview exchange (`podcast.qa`, output-quality 1.3): ships as a moment —
 *  the question heads the row, the answer is its description. */
export interface PodcastQAItem {
  question: string;
  /** The question's English when it was asked in another language (D12, 4.2). */
  questionEn?: string | null;
  answer: string;
  /** Who answers — the guest, usually. */
  speaker?: string | null;
  timestamp?: number | null;
  quote?: string | null;
  /** The quote's English on a non-English video (D12, 4.2). */
  quoteEn?: string | null;
}

export interface PodcastGuest {
  name: string;
  role?: string;
  description?: string;
  emoji?: string;
  /** The second the guest is introduced — the guest card's seek chip (4.1). */
  timestamp?: number | null;
}

export interface PodcastQuote {
  quote: string;
  /** The quote's English on a non-English video (D12, 4.2). */
  quoteEn?: string | null;
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
  /** Where the item is named (seconds). */
  timestamp?: number;
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
  /** The line is the English rendering of non-English speech (2.0 d, D12): prose. */
  quoteTranslated?: boolean;
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
