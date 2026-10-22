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

  it('should name the other side Competitor when two competitors have no rightLabel', () => {
    const numeric = [
      { feature: 'Battery', thisProduct: '6', competitor: '8', competitorName: 'Sony', winner: 'right' as const },
      { feature: 'Weight', thisProduct: '5', competitor: '6', competitorName: 'Sony', winner: 'left' as const },
      { feature: 'Price', thisProduct: '249', competitor: '299', competitorName: 'Bose', winner: 'left' as const },
    ];
    render(<ComparisonInteractive comparisons={numeric} view="radar" />);
    expect(screen.getByLabelText(/Radar comparing .* and Competitor/)).toBeInTheDocument();
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
});
