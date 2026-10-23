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

  it('should leave a verdict-only comparison tab out of the tabs when its verdict is lifted', () => {
    const tabs = [OVERVIEW, comparisonTab({ comparisons: [], pros: [], cons: [], verdict: VERDICT })];

    expect(liftReviewVerdict(tabs, 'review').tabs?.map((tab) => tab.id)).toEqual(['overview']);
  });

  it('should keep a verdict-only comparison tab that carries attachments when its verdict is lifted', () => {
    const tab: TabEntry = {
      ...comparisonTab({ verdict: VERDICT }),
      attachments: [{ slot: 'top', component: 'tip_callout', props: { text: 'Check the fit first.' } }],
    };

    expect(liftReviewVerdict([tab], 'review').tabs?.[0].props).not.toHaveProperty('verdict');
  });

  it('should strip the verdict from both comparison tabs when a review ships Pros & cons and vs competitors', () => {
    const tabs = [
      OVERVIEW,
      { ...comparisonTab({ pros: ['Better battery'], cons: ['Price'], comparisons: [], verdict: VERDICT }), id: 'pros_cons' },
      { ...comparisonTab({ comparisons: ROWS, pros: [], cons: [], verdict: VERDICT }, 'comparison_radar'), id: 'vs' },
    ];

    const lifted = liftReviewVerdict(tabs, 'review').tabs ?? [];

    expect(lifted.filter((tab) => 'verdict' in tab.props).map((tab) => tab.id)).toEqual([]);
  });

  it('should keep a comparison tab whose verdict states a different bottom line', () => {
    const other = { ...VERDICT, bottomLine: 'A different call on another product.' };
    const tabs = [comparisonTab({ comparisons: ROWS, verdict: VERDICT }), comparisonTab({ comparisons: ROWS, verdict: other })];

    expect(liftReviewVerdict(tabs, 'review').tabs?.[1].props.verdict).toBe(other);
  });

  it("should take the quote from the overview's spread when it states the same verdict", () => {
    const { verdict } = liftReviewVerdict(S07_TABS, 'review');

    expect(verdict?.quote).toBe(QUOTE);
  });

  it("should not borrow the overview's quote when it is said at another moment on the same bottom line", () => {
    // The spread puts a stated rating first: its moment and quote, the verdict's bottom line.
    const ratingSpread: TabEntry = {
      ...OVERVIEW,
      props: { data: { score: 8, maxScore: 10, label: 'Great', timestamp: 600, quote: "I'd give these an eight.", bottomLine: BOTTOM_LINE } },
    };

    const { verdict } = liftReviewVerdict([ratingSpread, S07_TABS[1]], 'review');

    expect(verdict?.quote).toBeUndefined();
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

  it("should take only the bottom line and badge from the overview when a stated rating leads its spread", () => {
    const ratingSpread: TabEntry = {
      ...OVERVIEW,
      props: { data: { score: 8, maxScore: 10, label: 'Great', timestamp: 600, quote: "I'd give these an eight.", badge: 'recommended', bottomLine: BOTTOM_LINE } },
    };

    const { verdict } = liftReviewVerdict([ratingSpread, comparisonTab({ comparisons: ROWS })], 'review');

    expect(verdict).toEqual({ bottomLine: BOTTOM_LINE, badge: 'recommended', bestFor: [], notFor: [] });
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

  it('should keep a stated score when the verdict sentence has no timestamp', () => {
    // Assembly ships the score only from a stated rating, which carries its own anchor.
    const unanchored = { ...VERDICT, timestamp: undefined, score: 8, maxScore: 10 };
    const tabs = [comparisonTab({ comparisons: ROWS, verdict: unanchored })];

    expect(liftReviewVerdict(tabs, 'review').verdict).toMatchObject({ score: 8, maxScore: 10 });
  });

  it('should carry the stated sub-scores into the hero verdict when assembly ships them', () => {
    const subScores = [
      { category: 'Battery', score: 9, timestamp: 300, quote: 'battery gets a nine' },
      { category: 'Camera', score: 7, timestamp: 420, quote: 'camera a seven' },
    ];
    const tabs = [comparisonTab({ pros: ['Battery'], verdict: { ...VERDICT, subScores } })];

    expect(liftReviewVerdict(tabs, 'review').verdict?.subScores).toEqual([
      { category: 'Battery', score: 9 },
      { category: 'Camera', score: 7 },
    ]);
  });

  it('should skip a sub-score row without a category or a number when one ships malformed', () => {
    const subScores = [{ category: '', score: 9 }, { category: 'Fit', score: '8/10' }, { category: 'Sound', score: 8 }];
    const tabs = [comparisonTab({ comparisons: ROWS, verdict: { ...VERDICT, subScores } })];

    expect(liftReviewVerdict(tabs, 'review').verdict?.subScores).toEqual([{ category: 'Sound', score: 8 }]);
  });

  it('should leave out blank Best for entries when the extraction ships them', () => {
    const tabs = [comparisonTab({ comparisons: ROWS, verdict: { ...VERDICT, bestFor: ['', 'Commuters'] } })];

    expect(liftReviewVerdict(tabs, 'review').verdict?.bestFor).toEqual(['Commuters']);
  });
});
