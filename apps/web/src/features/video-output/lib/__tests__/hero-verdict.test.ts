import { describe, it, expect } from 'vitest';
import type { TabEntry } from '@vie/types';
import { liftReviewVerdict } from '../hero-verdict';

// S07 (AirPods Pro 2 review) as stored: the comparison tab's verdict carries no
// quote; the overview's spread of the same verdict does.
const BOTTOM_LINE = 'AirPods Pro 2 are really, really good — the H2 chip is the underrated upgrade.';
const QUOTE = "It's the better battery life. It's the better sound quality.";

const OVERVIEW: TabEntry = {
  id: 'overview',
  label: 'Overview',
  emoji: '⭐',
  component: 'overview',
  props: { data: { product: 'AirPods Pro 2', badge: 'recommended', bottomLine: BOTTOM_LINE, timestamp: 641, quote: QUOTE } },
};

const ROWS = [
  { feature: 'Noise cancellation', thisProduct: 'Same level', competitor: 'Same level', competitorName: 'Sony WF-1000XM4' },
];

function comparisonTab(props: Record<string, unknown>, component = 'comparison'): TabEntry {
  return { id: 'verdict_pros_cons', label: 'Verdict & Pros/Cons', emoji: '⚖️', component, props };
}

const VERDICT = {
  bottomLine: BOTTOM_LINE,
  bestFor: ['"AirPods people" who wear AirPods everywhere', 'Anyone who wants better sound at the same price'],
  notFor: [],
  timestamp: 641,
  badge: 'recommended',
};

const S07_TABS: TabEntry[] = [OVERVIEW, comparisonTab({ comparisons: ROWS, pros: [], cons: [], verdict: VERDICT })];

describe('liftReviewVerdict', () => {
  it('should lift the comparison verdict into the hero when the page is a review', () => {
    const { verdict } = liftReviewVerdict(S07_TABS, 'review');

    expect(verdict).toMatchObject({ badge: 'recommended', bottomLine: BOTTOM_LINE, timestamp: 641, bestFor: VERDICT.bestFor });
  });

  it('should strip the verdict from the comparison tab when the tab keeps its rows', () => {
    const { tabs } = liftReviewVerdict(S07_TABS, 'review');

    expect(tabs?.[1].props).not.toHaveProperty('verdict');
  });

  it('should keep the comparison rows when the verdict is lifted', () => {
    const { tabs } = liftReviewVerdict(S07_TABS, 'review');

    expect(tabs?.[1].props.comparisons).toBe(ROWS);
  });

  it('should strip the verdict when the tab keeps only pros and cons', () => {
    const tabs = [comparisonTab({ pros: ['Better battery'], cons: [], verdict: VERDICT })];

    expect(liftReviewVerdict(tabs, 'review').tabs?.[0].props).not.toHaveProperty('verdict');
  });

  it('should keep the verdict in the tab when the tab has nothing else', () => {
    const tabs = [comparisonTab({ comparisons: [], pros: [], cons: [], verdict: VERDICT })];

    expect(liftReviewVerdict(tabs, 'review').tabs?.[0].props.verdict).toBe(VERDICT);
  });

  it("should take the quote from the overview's spread when it states the same verdict", () => {
    const { verdict } = liftReviewVerdict(S07_TABS, 'review');

    expect(verdict?.quote).toBe(QUOTE);
  });

  it("should not borrow the overview's quote when it states a different verdict", () => {
    const otherOverview: TabEntry = {
      ...OVERVIEW,
      props: { data: { bottomLine: 'Another call entirely.', timestamp: 12, quote: 'Other words.' } },
    };

    const { verdict } = liftReviewVerdict([otherOverview, S07_TABS[1]], 'review');

    expect(verdict?.quote).toBeUndefined();
  });

  it("should fall back to the overview's stated badge and bottom line when no tab carries a verdict", () => {
    const { verdict } = liftReviewVerdict([OVERVIEW, comparisonTab({ comparisons: ROWS })], 'review');

    expect(verdict).toMatchObject({ badge: 'recommended', bottomLine: BOTTOM_LINE, timestamp: 641, quote: QUOTE });
  });

  it('should lift from a comparison_radar tab when that is the comparison', () => {
    const tabs = [comparisonTab({ comparisons: ROWS, verdict: VERDICT }, 'comparison_radar')];

    expect(liftReviewVerdict(tabs, 'review').verdict?.bottomLine).toBe(BOTTOM_LINE);
  });

  it('should skip a comparison verdict that has no bottom line', () => {
    const tabs = [comparisonTab({ comparisons: ROWS, verdict: { ...VERDICT, bottomLine: '  ' } })];

    expect(liftReviewVerdict(tabs, 'review').verdict).toBeUndefined();
  });

  it('should lift nothing when the page is not a review', () => {
    const { verdict } = liftReviewVerdict(S07_TABS, 'tech');

    expect(verdict).toBeUndefined();
  });

  it('should hand back the same tabs when the page is not a review', () => {
    expect(liftReviewVerdict(S07_TABS, 'tech').tabs).toBe(S07_TABS);
  });

  it('should keep a score the reviewer gives at the verdict moment', () => {
    const tabs = [comparisonTab({ comparisons: ROWS, verdict: { ...VERDICT, score: 8, maxScore: 10 } })];

    expect(liftReviewVerdict(tabs, 'review').verdict).toMatchObject({ score: 8, maxScore: 10 });
  });

  it('should drop a score when the verdict has no timestamp', () => {
    const unanchored = { ...VERDICT, timestamp: undefined, score: 9, maxScore: 10 };
    const tabs = [comparisonTab({ comparisons: ROWS, verdict: unanchored })];

    expect(liftReviewVerdict(tabs, 'review').verdict?.score).toBeUndefined();
  });

  it('should leave out blank Best for entries when the extraction ships them', () => {
    const tabs = [comparisonTab({ comparisons: ROWS, verdict: { ...VERDICT, bestFor: ['', 'Commuters'] } })];

    expect(liftReviewVerdict(tabs, 'review').verdict?.bestFor).toEqual(['Commuters']);
  });
});
