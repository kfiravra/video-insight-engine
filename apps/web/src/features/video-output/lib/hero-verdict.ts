import type { ContentTag, TabEntry } from '@vie/types';

/** One category the reviewer scores on camera ("Battery" 9). */
export interface HeroSubScore {
  category: string;
  score: number;
}

/**
 * The reviewer's call as the hero renders it (output-quality 4.3). On a review
 * the verdict lives in the hero only: badge, bottom line, the moment it is said
 * and the words that say it, plus the reviewer's Best for / Not for.
 */
export interface HeroVerdictData {
  bottomLine: string;
  /** The stated verdict (`recommended`, `conditional`, …); absent when the reviewer gives none. */
  badge?: string;
  bestFor: string[];
  notFor: string[];
  /** Where the verdict is said (seconds). */
  timestamp?: number;
  quote?: string;
  /** English beneath a verbatim non-English quote (D12). */
  quoteEn?: string;
  /** The quote is already a rendering of non-English speech: prose, not a quotation. */
  quoteTranslated?: boolean;
  /** Score, scale and sub-scores exactly as assembly ships them: only stated —
   *  anchored and quoted where the reviewer gives them (`derived_guard`, D13).
   *  Never derived, and never re-gated on the bottom line's own timestamp. */
  score?: number;
  maxScore?: number;
  subScores?: HeroSubScore[];
}

export interface LiftedVerdict<T extends TabEntry> {
  verdict?: HeroVerdictData;
  /** The same tabs, minus the lifted verdict on every comparison tab that carried it. */
  tabs: T[] | null;
}

type UnknownRecord = Record<string, unknown>;

const COMPARISON_COMPONENTS = new Set(['comparison', 'comparison_radar']);
const TAB_CONTENT_KEYS = ['comparisons', 'pros', 'cons'];
/** A stated rating's scalars. `_domain_scalars` reads `rating` before `verdict`,
 *  so with these in the overview's spread its moment and quote are the rating's. */
const RATING_KEYS = ['score', 'maxScore', 'label'];

function asRecord(value: unknown): UnknownRecord | undefined {
  return value != null && typeof value === 'object' && !Array.isArray(value) ? (value as UnknownRecord) : undefined;
}

function text(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim().length > 0 ? value.trim() : undefined;
}

function texts(value: unknown): string[] {
  return Array.isArray(value) ? value.map(text).filter((item): item is string => item !== undefined) : [];
}

function nonNegative(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : undefined;
}

function subScoresOf(value: unknown): HeroSubScore[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const rows = value.flatMap((row) => {
    const record = asRecord(row);
    const category = text(record?.category);
    const score = nonNegative(record?.score);
    return category !== undefined && score !== undefined ? [{ category, score }] : [];
  });
  return rows.length > 0 ? rows : undefined;
}

/** A verdict record (the comparison tab's `verdict`, or the overview's spread
 *  of it) as the hero renders it; none without a bottom line. */
function toVerdict(raw: UnknownRecord | undefined): HeroVerdictData | undefined {
  const bottomLine = text(raw?.bottomLine);
  if (!raw || !bottomLine) return undefined;
  const maxScore = nonNegative(raw.maxScore);
  return {
    bottomLine,
    badge: text(raw.badge),
    bestFor: texts(raw.bestFor),
    notFor: texts(raw.notFor),
    timestamp: nonNegative(raw.timestamp),
    quote: text(raw.quote),
    quoteEn: text(raw.quoteEn),
    quoteTranslated: raw.quoteTranslated === true || undefined,
    score: nonNegative(raw.score),
    maxScore: maxScore ? maxScore : undefined,
    subScores: subScoresOf(raw.subScores),
  };
}

/** The overview carries the verdict's stated scalars (badge, bottomLine, and
 *  timestamp / quote unless a stated rating's come first). */
function overviewData(tabs: TabEntry[]): UnknownRecord | undefined {
  const overview = tabs.find((tab) => tab.id === 'overview' || tab.component === 'overview');
  return asRecord(overview?.props?.data);
}

/** The fallback on a row with no comparison verdict: with a rating in the
 *  spread, only the bottom line and the badge are the verdict's. */
function spreadVerdict(data: UnknownRecord | undefined): HeroVerdictData | undefined {
  const verdict = toVerdict(data);
  if (!verdict || !RATING_KEYS.some((key) => data?.[key] !== undefined)) return verdict;
  return { bottomLine: verdict.bottomLine, badge: verdict.badge, bestFor: [], notFor: [] };
}

function comparisonVerdict(tab: TabEntry): HeroVerdictData | undefined {
  return COMPARISON_COMPONENTS.has(tab.component) ? toVerdict(asRecord(tab.props?.verdict)) : undefined;
}

/** A comparison verdict stored without its quote takes the overview's — only
 *  when the overview's quote is said at the verdict's own moment. */
function withSpreadQuote(verdict: HeroVerdictData, spread: HeroVerdictData | undefined): HeroVerdictData {
  if (!spread?.quote || verdict.quote) return verdict;
  if (verdict.timestamp === undefined || verdict.timestamp !== spread.timestamp) return verdict;
  return {
    ...verdict,
    quote: spread.quote,
    quoteEn: spread.quoteEn,
    quoteTranslated: spread.quoteTranslated,
    badge: verdict.badge ?? spread.badge,
  };
}

function keepsContent(tab: TabEntry): boolean {
  const hasRows = TAB_CONTENT_KEYS.some((key) => {
    const value = tab.props[key];
    return Array.isArray(value) && value.length > 0;
  });
  return hasRows || (tab.attachments?.length ?? 0) > 0;
}

function withoutVerdict<T extends TabEntry>(tab: T): T {
  const props = Object.fromEntries(Object.entries(tab.props).filter(([key]) => key !== 'verdict'));
  return { ...tab, props };
}

/** Every comparison tab carrying the lifted verdict loses it, and one holding
 *  nothing else leaves the strip: its only content is in the hero. Assembly
 *  now puts the verdict on one tab (`assemblers_primary._carries_verdict`), but
 *  rows stored before carry it on several — so every matching tab is stripped. */
function withoutLifted<T extends TabEntry>(tabs: T[], bottomLine: string): T[] {
  return tabs.flatMap((tab) => {
    if (comparisonVerdict(tab)?.bottomLine !== bottomLine) return [tab];
    return keepsContent(tab) ? [withoutVerdict(tab)] : [];
  });
}

/**
 * Lift a review's verdict into the hero (Kfir 2026-10-22: the hero only). The
 * first comparison tab's verdict with a bottom line wins, else the overview's
 * spread of it. Not a review → nothing lifted, the tabs returned as they came.
 */
export function liftReviewVerdict<T extends TabEntry>(tabs: T[] | null, primaryTag: ContentTag): LiftedVerdict<T> {
  if (primaryTag !== 'review' || !tabs || tabs.length === 0) return { tabs };
  const spread = overviewData(tabs);
  const stated = tabs.map(comparisonVerdict).find((candidate) => candidate !== undefined);
  if (!stated) return { verdict: spreadVerdict(spread), tabs };
  return { verdict: withSpreadQuote(stated, toVerdict(spread)), tabs: withoutLifted(tabs, stated.bottomLine) };
}
