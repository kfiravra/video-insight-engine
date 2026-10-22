import type { ContentTag, TabEntry } from '@vie/types';

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
  /** Only as the reviewer gives it, and only with the verdict's timestamp (D13) — never derived. */
  score?: number;
  maxScore?: number;
}

export interface LiftedVerdict<T extends TabEntry> {
  verdict?: HeroVerdictData;
  /** The same tabs, minus the verdict on the tab it was lifted from. */
  tabs: T[] | null;
}

type UnknownRecord = Record<string, unknown>;

const COMPARISON_COMPONENTS = new Set(['comparison', 'comparison_radar']);
const TAB_CONTENT_KEYS = ['comparisons', 'pros', 'cons'];

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

/** A verdict record (the comparison tab's `verdict`, or the overview's spread
 *  of it) as the hero renders it; none without a bottom line. */
function toVerdict(raw: UnknownRecord | undefined): HeroVerdictData | undefined {
  const bottomLine = text(raw?.bottomLine);
  if (!raw || !bottomLine) return undefined;
  const timestamp = nonNegative(raw.timestamp);
  const score = timestamp === undefined ? undefined : nonNegative(raw.score);
  const maxScore = nonNegative(raw.maxScore);
  return {
    bottomLine,
    badge: text(raw.badge),
    bestFor: texts(raw.bestFor),
    notFor: texts(raw.notFor),
    timestamp,
    quote: text(raw.quote),
    quoteEn: text(raw.quoteEn),
    quoteTranslated: raw.quoteTranslated === true || undefined,
    score,
    maxScore: score !== undefined && maxScore ? maxScore : undefined,
  };
}

/** The overview carries the verdict's stated scalars (badge, bottomLine,
 *  timestamp, quote) — the source on a row with no comparison verdict. */
function overviewSpread(tabs: TabEntry[]): HeroVerdictData | undefined {
  const overview = tabs.find((tab) => tab.id === 'overview' || tab.component === 'overview');
  return toVerdict(asRecord(overview?.props?.data));
}

function comparisonVerdict(tab: TabEntry): HeroVerdictData | undefined {
  return COMPARISON_COMPONENTS.has(tab.component) ? toVerdict(asRecord(tab.props?.verdict)) : undefined;
}

/** The comparison tab's verdict ships no quote; the overview's spread of the
 *  same verdict (same bottom line or same moment) does. */
function withSpreadQuote(verdict: HeroVerdictData, spread: HeroVerdictData | undefined): HeroVerdictData {
  if (!spread?.quote || verdict.quote) return verdict;
  const sameMoment = verdict.timestamp !== undefined && verdict.timestamp === spread.timestamp;
  if (verdict.bottomLine !== spread.bottomLine && !sameMoment) return verdict;
  return {
    ...verdict,
    quote: spread.quote,
    quoteEn: spread.quoteEn,
    quoteTranslated: spread.quoteTranslated,
    timestamp: verdict.timestamp ?? spread.timestamp,
    badge: verdict.badge ?? spread.badge,
  };
}

function keepsContent(props: UnknownRecord): boolean {
  return TAB_CONTENT_KEYS.some((key) => {
    const value = props[key];
    return Array.isArray(value) && value.length > 0;
  });
}

function withoutVerdict<T extends TabEntry>(tab: T): T {
  const props = Object.fromEntries(Object.entries(tab.props).filter(([key]) => key !== 'verdict'));
  return { ...tab, props };
}

/**
 * Lift a review's verdict into the hero (Kfir 2026-10-22: the hero only). The
 * first comparison tab's verdict with a bottom line wins, else the overview's
 * spread of it. The tab loses its verdict only while it keeps rows or pros /
 * cons — a tab holding nothing but the verdict keeps it rather than go empty.
 * Not a review → nothing lifted, the tabs returned as they came.
 */
export function liftReviewVerdict<T extends TabEntry>(tabs: T[] | null, primaryTag: ContentTag): LiftedVerdict<T> {
  if (primaryTag !== 'review' || !tabs || tabs.length === 0) return { tabs };
  const spread = overviewSpread(tabs);
  const candidates = tabs.map(comparisonVerdict);
  const index = candidates.findIndex((candidate) => candidate !== undefined);
  const stated = candidates[index];
  if (!stated) return { verdict: spread, tabs };
  const verdict = withSpreadQuote(stated, spread);
  if (!keepsContent(tabs[index].props)) return { verdict, tabs };
  return { verdict, tabs: tabs.map((tab, i) => (i === index ? withoutVerdict(tab) : tab)) };
}
