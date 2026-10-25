/// <reference types="@testing-library/jest-dom" />
import { describe, it, expect } from 'vitest';
import { render, screen, within } from '@testing-library/react';

import { ComparisonInteractive } from '../ComparisonInteractive';
import { groupByCompetitor } from '../comparison/competitor-groups';

const rows = [
  { feature: 'Noise cancellation', thisProduct: 'Class-leading', competitor: 'Strong', competitorName: 'Sony WF-1000XM4', winner: 'left' as const },
  { feature: 'Battery', thisProduct: '6 h', competitor: '8 h', competitorName: 'Sony WF-1000XM4', winner: 'right' as const },
  { feature: 'Noise cancellation', thisProduct: 'Class-leading', competitor: 'Good', competitorName: 'Bose QC', winner: 'left' as const },
  { feature: 'Price', thisProduct: '$249', competitor: '$299', competitorName: 'Jabra Elite' },
];

// Model-written casing variants of one competitor; the assembler merges them ("Sony" and "sony" are one).
const CASE_VARIANTS = [
  { feature: 'ANC', thisProduct: 'Class-leading', competitor: 'Strong', competitorName: 'Sony WF-1000XM4' },
  { feature: 'Fit', thisProduct: 'Snug', competitor: 'Loose', competitorName: 'Sony WF-1000XM4 ' },
  { feature: 'Battery', thisProduct: '6 h', competitor: '8 h', competitorName: 'sony WF-1000XM4' },
];

describe('ComparisonInteractive per-competitor tables', () => {
  it('should render one table per competitor when the rows name two or more', () => {
    render(<ComparisonInteractive comparisons={rows} type="table" view="table" />);
    expect(screen.getAllByRole('table')).toHaveLength(3);
  });

  it('should head each table with its own competitor when the rows name two or more', () => {
    render(<ComparisonInteractive comparisons={rows} type="table" view="table" />);
    const [first] = screen.getAllByRole('table');
    expect(within(first).getByRole('columnheader', { name: 'Sony WF-1000XM4' })).toBeInTheDocument();
  });

  it('should label each table with a vs eyebrow when the rows name two or more', () => {
    render(<ComparisonInteractive comparisons={rows} type="table" view="table" />);
    expect(screen.getByRole('heading', { name: 'vs Bose QC' })).toBeInTheDocument();
  });

  it('should keep a repeated feature in each competitor table when two competitors share it', () => {
    render(<ComparisonInteractive comparisons={rows} type="table" view="table" />);
    expect(screen.getAllByText('Noise cancellation')).toHaveLength(2);
  });

  it('should render a single table without an eyebrow when there is one competitor', () => {
    render(<ComparisonInteractive comparisons={rows.slice(0, 2)} type="table" view="table" />);
    expect(screen.queryByRole('heading', { name: /^vs / })).not.toBeInTheDocument();
  });

  it('should show no wins tally when rows carry winners', () => {
    render(<ComparisonInteractive comparisons={rows} type="table" view="table" />);
    expect(screen.queryByText(/wins \d+ of \d+ categories/)).not.toBeInTheDocument();
  });

  // pC review: the radar splits per competitor like the tables — never one
  // pooled "Competitor" series with a spoke per rival (D13).
  describe('radar with two or more competitors', () => {
    const numeric = (name: string) => [
      { feature: 'Battery', thisProduct: '6', competitor: '8', competitorName: name, winner: 'right' as const },
      { feature: 'Weight', thisProduct: '5', competitor: '6', competitorName: name, winner: 'left' as const },
      { feature: 'Price', thisProduct: '249', competitor: '299', competitorName: name, winner: 'left' as const },
    ];

    it('should draw one radar per competitor when each has three numeric axes', () => {
      render(<ComparisonInteractive comparisons={[...numeric('Sony'), ...numeric('Bose')]} view="radar" />);
      expect(screen.getAllByRole('img', { name: /^Radar comparing / }).map((el) => el.getAttribute('aria-label'))).toEqual([
        'Radar comparing This Product and Sony',
        'Radar comparing This Product and Bose',
      ]);
    });

    it('should head each radar with its own competitor when there are two or more', () => {
      render(<ComparisonInteractive comparisons={[...numeric('Sony'), ...numeric('Bose')]} view="radar" />);
      expect(screen.getByRole('heading', { name: 'vs Bose' })).toBeInTheDocument();
    });

    it('should draw no radar when a competitor has fewer than three numeric axes', () => {
      render(<ComparisonInteractive comparisons={[...numeric('Sony'), ...numeric('Bose').slice(0, 2)]} view="radar" />);
      expect(screen.queryByRole('img', { name: /^Radar comparing / })).not.toBeInTheDocument();
    });

    it('should show the per-competitor tables when no radar can be drawn', () => {
      render(<ComparisonInteractive comparisons={[...numeric('Sony'), ...numeric('Bose').slice(0, 2)]} view="radar" />);
      expect(screen.getAllByRole('table')).toHaveLength(2);
    });

    it('should never crown a pooled Competitor series when the rows name two competitors', () => {
      render(<ComparisonInteractive comparisons={[...numeric('Sony'), ...numeric('Bose')]} view="radar" />);
      expect(screen.queryByText(/^Competitor wins by/)).not.toBeInTheDocument();
    });
  });

  it('should keep one table when two rows spell the competitor in different case', () => {
    render(<ComparisonInteractive comparisons={CASE_VARIANTS} type="table" view="table" />);
    expect(screen.getAllByRole('table')).toHaveLength(1);
  });

  it('should group versus cards per competitor when the rows name two or more', () => {
    render(<ComparisonInteractive comparisons={rows} type="versus" view="table" />);
    expect(screen.getByRole('heading', { name: 'vs Jabra Elite' })).toBeInTheDocument();
  });
});

describe('groupByCompetitor', () => {
  it('should keep first-seen order and the original row index when grouping', () => {
    const groups = groupByCompetitor(rows);
    expect(groups.map((g) => [g.name, g.rows.map((r) => r.index)])).toEqual([
      ['Sony WF-1000XM4', [0, 1]],
      ['Bose QC', [2]],
      ['Jabra Elite', [3]],
    ]);
  });

  it('should keep the first-seen spelling when two rows spell the competitor in different case', () => {
    expect(groupByCompetitor(CASE_VARIANTS).map((g) => [g.name, g.rows.length])).toEqual([['Sony WF-1000XM4', 3]]);
  });
});
