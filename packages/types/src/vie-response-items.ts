// ═══════════════════════════════════════════════════
// VIE Response — tab item types (shared items, claims, tiers, formations,
// quiz / flashcard / scenario). Re-exported from vie-response.ts.
// ═══════════════════════════════════════════════════

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
  /** The quote is the English rendering of non-English speech (output-quality 2.0 d,
   *  D12): shown as prose, never in quote styling. Set by the quote check. */
  quoteTranslated?: boolean;
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
  /** The quote is the English rendering of non-English speech (2.0 d, D12): prose. */
  quoteTranslated?: boolean;
  /** An interview exchange's whole question (`podcast.qa`) — present only when it
   *  differs from the label (a long question's label is its first sentence); the
   *  timeline row then heads with it. */
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
  /** What the video does with the claim — absent when the claim carries no timestamp
   *  (a status nobody can check is not shown: no badge, no filter count). */
  status?: ClaimStatus | LegacyClaimStatus | null;
  /** Where the claim's verification comes from (study, outlet, official record);
   *  absent with the status. */
  sourceCitation?: string;
  /** The words that make the claim, verbatim — the status needs them (D13, 2.0 c). */
  quote?: string | null;
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
  /** Where the creator places the item (seconds); a tier ships only with it and the
   *  creator's words that state it (`quote`, D13, 2.0 c). */
  timestamp?: number;
  quote?: string | null;
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
  /** Where the transcript gives the answer, in seconds (output-quality 1.13);
   *  range-checked by the enrichment — absent on older rows. */
  timestamp?: number | null;
  /** Quiz v2's evidence (output-quality 2.1b): ≤ 25 words the speaker says, found in
   *  the transcript (the stamp is set from where) — absent on older rows. */
  quote?: string | null;
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
