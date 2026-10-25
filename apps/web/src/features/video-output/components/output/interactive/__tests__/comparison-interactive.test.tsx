/// <reference types="@testing-library/jest-dom" />
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';


import { ComparisonInteractive } from '../ComparisonInteractive';
import { ComparisonRadarHero } from '../comparison/ComparisonRadarHero';
import { hasRadarAxes, scoreComparisonAxes } from '../comparison/radar-scoring';

const comparisons = [
  { feature: 'Battery', thisProduct: '4000mAh', competitor: '3500mAh', competitorName: 'Brand X' },
  { feature: 'Display', thisProduct: 'OLED', competitor: 'LCD', competitorName: 'Brand X' },
];

// Four scoreable axes — enough to trigger the radar hero (≥3).
const radarRows = [
  { feature: 'Camera', thisProduct: '48', competitor: '50', competitorName: 'Galaxy S24', winner: 'right' as const },
  { feature: 'Battery', thisProduct: '4422', competitor: '4000', competitorName: 'Galaxy S24', winner: 'left' as const },
  { feature: 'Display', thisProduct: '120', competitor: '120', competitorName: 'Galaxy S24', winner: 'tie' as const },
  { feature: 'Weight', thisProduct: '187', competitor: '227', competitorName: 'Galaxy S24', winner: 'left' as const },
];

// A row with no number on either side — no radar spoke.
const proseRow = { feature: 'Finish', thisProduct: 'Titanium', competitor: 'Aluminium', competitorName: 'Galaxy S24' };

// Price and weight are won by the SMALLER number (lower is better); battery by the larger.
const lowerIsBetterRows = [
  { feature: 'Price', thisProduct: '$799', competitor: '$899', competitorName: 'Galaxy S24', winner: 'left' as const },
  { feature: 'Weight', thisProduct: '187 g', competitor: '227 g', competitorName: 'Galaxy S24', winner: 'left' as const },
  { feature: 'Battery', thisProduct: '4,422 mAh', competitor: '4,000 mAh', competitorName: 'Galaxy S24', winner: 'left' as const },
];

async function tuneFirstAxisUp(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: /tune what matters/i }));
  screen.getAllByRole('slider')[0].focus();
  await user.keyboard('{ArrowRight}');
}

describe('ComparisonInteractive', () => {
  it('should render table comparison', () => {
    render(<ComparisonInteractive comparisons={comparisons} type="table" />);
    expect(screen.getByText('Feature')).toBeInTheDocument();
    expect(screen.getByText('Battery')).toBeInTheDocument();
    expect(screen.getByText('4000mAh')).toBeInTheDocument();
    expect(screen.getByText('Brand X')).toBeInTheDocument();
  });

  it('renders an empty state when no data', () => {
    const { container } = render(<ComparisonInteractive />);
    expect(container.textContent).toContain('No comparison data was extracted');
  });

  it('should render pros and cons', () => {
    render(<ComparisonInteractive pros={['Fast', 'Durable']} cons={['Expensive']} />);
    expect(screen.getByText('Go for it if...')).toBeInTheDocument();
    expect(screen.getByText('Fast')).toBeInTheDocument();
    expect(screen.getByText('Skip it if...')).toBeInTheDocument();
    expect(screen.getByText('Expensive')).toBeInTheDocument();
  });

  it('should render versus mode', () => {
    render(<ComparisonInteractive comparisons={comparisons} type="versus" />);
    expect(screen.getByText('Battery')).toBeInTheDocument();
    // "This Product" appears once per comparison row
    const thisProductEls = screen.getAllByText('This Product');
    expect(thisProductEls.length).toBeGreaterThanOrEqual(1);
  });

  it('should render ReviewSummary header with bottomLine, bestFor and notFor when verdict is provided', () => {
    render(
      <ComparisonInteractive
        comparisons={comparisons}
        verdict={{
          badge: 'recommended',
          bottomLine: 'A solid upgrade for most users.',
          bestFor: ['Power users', 'Photographers'],
          notFor: ['Budget shoppers'],
          score: 8,
          maxScore: 10,
        }}
      />,
    );
    expect(screen.getByText('A solid upgrade for most users.')).toBeInTheDocument();
    expect(screen.getByText('Best For')).toBeInTheDocument();
    expect(screen.getByText('Power users')).toBeInTheDocument();
    expect(screen.getByText('Not For')).toBeInTheDocument();
    expect(screen.getByText('Budget shoppers')).toBeInTheDocument();
    expect(screen.getByText('recommended')).toBeInTheDocument();
  });

  // ─── P3C: unified radar + table + verdict ───

  it('should default to the radar view and reveal the table only after toggling, when there are >=3 scoreable axes', async () => {
    const user = userEvent.setup();
    render(<ComparisonInteractive comparisons={radarRows} type="table" />);
    // Radar hero is the default view; the table is not stacked beneath it.
    expect(screen.getByTestId('comparison-radar-winner-badge')).toBeInTheDocument();
    expect(screen.queryByText('Feature')).not.toBeInTheDocument();
    // Switching to the Table view swaps the radar out for the table.
    await user.click(screen.getByRole('button', { name: 'Table' }));
    expect(screen.getByText('Feature')).toBeInTheDocument();
    expect(screen.getAllByText('Camera').length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByTestId('comparison-radar-winner-badge')).not.toBeInTheDocument();
  });

  it('should NOT show the radar hero with fewer than 3 axes', () => {
    render(<ComparisonInteractive comparisons={comparisons} type="table" />);
    expect(screen.queryByTestId('comparison-radar-winner-badge')).not.toBeInTheDocument();
    // Table still renders.
    expect(screen.getByText('Battery')).toBeInTheDocument();
  });

  it('should render the radar hero when view="radar" is forced (comparison_radar alias path)', async () => {
    const user = userEvent.setup();
    render(<ComparisonInteractive comparisons={radarRows} view="radar" />);
    expect(screen.getByTestId('comparison-radar-winner-badge')).toBeInTheDocument();
    // Per-axis weight sliders live behind the collapsed "Tune what matters" disclosure.
    expect(screen.queryByTestId('comparison-radar-weight-0')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /tune what matters/i }));
    expect(screen.getByTestId('comparison-radar-weight-0')).toBeInTheDocument();
  });

  it('should suppress the radar hero when view="table" even with enough axes', () => {
    render(<ComparisonInteractive comparisons={radarRows} view="table" />);
    expect(screen.queryByTestId('comparison-radar-winner-badge')).not.toBeInTheDocument();
    expect(screen.getByText('Feature')).toBeInTheDocument();
  });

  describe('scoreComparisonAxes', () => {
    it('scales numeric sides so the larger is 10', () => {
      const scored = scoreComparisonAxes([
        { feature: 'Battery', thisProduct: '4000', competitor: '2000', competitorName: 'X' },
      ]);
      expect(scored[0].left).toBe(10);
      expect(scored[0].right).toBe(5);
    });

    it('reads a unit-suffixed or currency-prefixed cell as a number', () => {
      const scored = scoreComparisonAxes([
        { feature: 'Battery', thisProduct: '4,422 mAh', competitor: '$2,211', competitorName: 'X' },
      ]);
      expect(scored[0].left).toBe(10);
      expect(scored[0].right).toBe(5);
    });

    it('should not score a non-numeric row — no winner-based axis (R10)', () => {
      const scored = scoreComparisonAxes([
        { feature: 'Display', thisProduct: 'OLED', competitor: 'LCD', competitorName: 'X', winner: 'left' },
      ]);
      expect(scored).toEqual([]);
    });

    it('should not score a row with an ordinal rank on either side', () => {
      const scored = scoreComparisonAxes([
        { feature: 'Ranking', thisProduct: '1st', competitor: '3RD', competitorName: 'X' },
        { feature: 'Chart position', thisProduct: '22nd', competitor: '48', competitorName: 'X' },
      ]);
      expect(scored).toEqual([]);
    });

    it('should score the smaller side 10 when the stated winner holds the smaller value (price)', () => {
      const [price] = scoreComparisonAxes(lowerIsBetterRows);
      expect(price.left).toBe(10);
    });

    it('should score the larger side of a lower-is-better row as smaller/larger (weight)', () => {
      const [, weight] = scoreComparisonAxes(lowerIsBetterRows);
      expect(weight.right).toBeCloseTo((187 / 227) * 10, 5);
    });

    it('should keep larger-is-better scoring when the stated winner holds the larger value (battery)', () => {
      const [, , battery] = scoreComparisonAxes(lowerIsBetterRows);
      expect(battery).toMatchObject({ left: 10, lowerIsBetter: false });
    });

    it.each([['tie' as const], [undefined]])('should keep larger-is-better scoring when the winner is %s', (winner) => {
      const [price] = scoreComparisonAxes([
        { feature: 'Price', thisProduct: '$799', competitor: '$899', competitorName: 'X', winner },
      ]);
      expect(price.right).toBe(10);
    });

    it('should not read a sentence that merely contains a digit as an axis', () => {
      const scored = scoreComparisonAxes([
        {
          feature: 'Noise cancellation (ranking)',
          thisProduct: 'AirPods Pro 2 — placed on the same level',
          competitor: "Sony WF-1000XM4 (said as 'Sony Mark 4')",
          competitorName: 'Sony WF-1000XM4',
          winner: 'tie',
        },
      ]);
      expect(scored).toEqual([]);
    });
  });

  describe('hasRadarAxes', () => {
    it('should hide the radar when fewer than 3 rows are numeric on both sides', () => {
      const rows = [
        ...comparisons,
        { feature: 'Weight', thisProduct: '187 g', competitor: '227 g', competitorName: 'Brand X' },
        { feature: 'Price', thisProduct: '$249', competitor: 'about the same', competitorName: 'Brand X' },
      ];
      expect(hasRadarAxes(rows)).toBe(false);
    });

    it('should show the radar with 3 numeric axes among prose rows', () => {
      expect(hasRadarAxes([...radarRows, ...comparisons])).toBe(true);
    });
  });

  it('should NOT show the radar hero for prose rows even with >=3 of them', () => {
    const proseRows = [
      { feature: 'ANC', thisProduct: 'AirPods Pro 2 — placed right below', competitor: 'Bose QC II — ranked just above', competitorName: 'Bose', winner: 'right' as const },
      { feature: 'Transparency', thisProduct: 'on a level by itself', competitor: 'Boses, Samsungs and Pixel Buds', competitorName: 'Bose', winner: 'left' as const },
      { feature: 'Overall', thisProduct: 'significant upgrade', competitor: "Samsung's buds are really good", competitorName: 'Samsung', winner: 'tie' as const },
    ];
    render(<ComparisonInteractive comparisons={proseRows} view="radar" />);
    expect(screen.queryByTestId('comparison-radar-winner-badge')).not.toBeInTheDocument();
  });

  describe('ComparisonRadarHero verdict', () => {
    it('should crown the stated winner of lower-is-better rows (cheaper and lighter)', () => {
      render(<ComparisonRadarHero comparisons={lowerIsBetterRows} colLeft="A" colRight="B" />);
      expect(screen.getByTestId('comparison-radar-winner-badge')).toHaveTextContent(/^A wins by/);
    });

    it('should name the spokes where the lower value wins', () => {
      render(<ComparisonRadarHero comparisons={lowerIsBetterRows} colLeft="A" colRight="B" />);
      expect(screen.getByText(/lower wins on Price, Weight\./)).toBeInTheDocument();
    });

    it('should not show the lower-wins note when every spoke is larger-is-better', () => {
      render(<ComparisonRadarHero comparisons={radarRows.slice(0, 3)} colLeft="A" colRight="B" />);
      expect(screen.queryByText(/lower wins on/)).not.toBeInTheDocument();
    });
  });

  describe('ComparisonRadarHero weights', () => {
    it('should keep a tuned weight when a prose row joins and the axes stay the same', async () => {
      const user = userEvent.setup();
      const { rerender } = render(<ComparisonRadarHero comparisons={radarRows} colLeft="A" colRight="B" />);
      await tuneFirstAxisUp(user);

      rerender(<ComparisonRadarHero comparisons={[...radarRows, proseRow]} colLeft="A" colRight="B" />);

      expect(screen.getAllByRole('slider')[0]).toHaveAttribute('aria-valuenow', '6');
    });

    it('should start the tuning fresh when the number of axes changes', async () => {
      const user = userEvent.setup();
      const priceRow = { feature: 'Price', thisProduct: '$799', competitor: '$899', competitorName: 'Galaxy S24' };
      const { rerender } = render(<ComparisonRadarHero comparisons={radarRows} colLeft="A" colRight="B" />);
      await tuneFirstAxisUp(user);

      rerender(<ComparisonRadarHero comparisons={[...radarRows, priceRow]} colLeft="A" colRight="B" />);

      expect(screen.getAllByRole('slider')[0]).toHaveAttribute('aria-valuenow', '5');
    });
  });

  it('should NOT render ReviewSummary when verdict.bottomLine is empty/undefined', () => {
    render(
      <ComparisonInteractive
        comparisons={comparisons}
        verdict={{
          badge: 'recommended',
          bestFor: ['Power users'],
        }}
      />,
    );
    // Header pieces must NOT appear
    expect(screen.queryByText('Best For')).not.toBeInTheDocument();
    expect(screen.queryByText('Power users')).not.toBeInTheDocument();
    expect(screen.queryByText('recommended')).not.toBeInTheDocument();
    // Table still renders
    expect(screen.getByText('Battery')).toBeInTheDocument();
    expect(screen.getByText('Feature')).toBeInTheDocument();
  });
});
