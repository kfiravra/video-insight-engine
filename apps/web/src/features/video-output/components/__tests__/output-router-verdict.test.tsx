import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import type { ContentTag, TabEntry, VIEResponseMeta } from '@vie/types';

import { OutputRouter } from '../OutputRouter';

// S07 (AirPods Pro 2 review) as stored: the comparison tab ships rows and a
// verdict; the overview carries the same verdict's stated scalars.
const BOTTOM_LINE = 'AirPods Pro 2 are really, really good — the H2 chip is the underrated upgrade.';

const OVERVIEW: TabEntry = {
  id: 'overview',
  label: 'Overview',
  emoji: '⭐',
  component: 'overview',
  props: { data: { badge: 'recommended', bottomLine: BOTTOM_LINE, timestamp: 641, quote: 'It is the better battery life.' } },
};

const COMPARISON: TabEntry = {
  id: 'verdict_pros_cons',
  label: 'Verdict & Pros/Cons',
  emoji: '⚖️',
  component: 'comparison',
  props: {
    comparisons: [
      { feature: 'Noise cancellation', thisProduct: 'Same level', competitor: 'Same level', competitorName: 'Sony WF-1000XM4' },
    ],
    pros: [],
    cons: [],
    verdict: { badge: 'recommended', bottomLine: BOTTOM_LINE, bestFor: ['Commuters'], notFor: [], timestamp: 641 },
  },
};

function metaFor(primaryTag: ContentTag): VIEResponseMeta {
  return {
    videoId: 'xyz12345678',
    videoTitle: 'AirPods Pro 2 Review',
    creator: 'Marques Brownlee',
    contentTags: [primaryTag],
    modifiers: [],
    primaryTag,
    userGoal: 'decide whether to buy',
  };
}

function renderRouter(primaryTag: ContentTag): void {
  render(
    <OutputRouter
      title="AirPods Pro 2 Review"
      videoSummaryId="vid-s07"
      tabs={[OVERVIEW, COMPARISON]}
      meta={metaFor(primaryTag)}
      synthesis={{ tldr: 'The H2 chip is the upgrade.', keyTakeaways: [], masterSummary: '', seoDescription: '' }}
    />,
  );
}

describe('OutputRouter — review verdict', () => {
  it('should render the review verdict once, in the hero, when the comparison tab keeps its rows', () => {
    renderRouter('review');

    expect(screen.getAllByText(BOTTOM_LINE)).toHaveLength(1);
  });

  it('should hand the lifted verdict to the hero row when the page is a review', () => {
    renderRouter('review');

    expect(screen.getByRole('button', { name: /^Verdict\s*recommended$/i })).toBeInTheDocument();
  });

  it('should keep the comparison rows in the tab when its verdict moves to the hero', () => {
    renderRouter('review');

    expect(screen.getAllByText('Noise cancellation').length).toBeGreaterThan(0);
  });

  it('should leave the verdict in the tab when the page is not a review', () => {
    renderRouter('tech');

    expect(screen.queryByRole('button', { name: /^Verdict/i })).not.toBeInTheDocument();
    expect(screen.getByText(BOTTOM_LINE)).toBeInTheDocument();
  });
});
