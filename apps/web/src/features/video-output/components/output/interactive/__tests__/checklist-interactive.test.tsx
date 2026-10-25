import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

vi.mock('../../Celebration', () => ({
  Celebration: ({ title }: { title: string }) => <div data-testid="celebration">{title}</div>,
}));

import { ChecklistInteractive } from '../ChecklistInteractive';

const items = [
  { label: 'Flour', amount: 2, unit: 'cups' },
  { label: 'Sugar', amount: 1, unit: 'cup', essential: true },
  { label: 'Salt' },
];

describe('ChecklistInteractive', () => {
  it('should render all items', () => {
    render(<ChecklistInteractive items={items} tabLabel="Ingredients" />);
    expect(screen.getByText('Ingredients')).toBeInTheDocument();
    expect(screen.getByText('Flour')).toBeInTheDocument();
    expect(screen.getByText('Sugar')).toBeInTheDocument();
    expect(screen.getByText('Salt')).toBeInTheDocument();
  });

  it("should show an ask's English beneath its verbatim note when the row carries quoteEn", () => {
    const asks = [
      { label: 'Fund open research', note: '“תממנו מחקר פתוח”', quoteEn: 'Fund open research.' },
      { label: 'Publish the data', note: '“Publish the data.”' },
      { label: 'Share the code', note: '“Share the code.”' },
    ];
    render(<ChecklistInteractive items={asks} tabLabel="Asks" />);
    expect(screen.getByText('Fund open research.')).toHaveAttribute('lang', 'en');
  });

  it('should let a verbatim note read in its own direction when it is a Hebrew line', () => {
    render(<ChecklistInteractive items={[{ label: 'Fund open research', note: '“תממנו מחקר פתוח”' }]} tabLabel="Asks" />);
    expect(screen.getByText('“תממנו מחקר פתוח”')).toHaveAttribute('dir', 'auto');
  });

  it('renders an empty state for empty items', () => {
    const { container } = render(<ChecklistInteractive items={[]} tabLabel="Empty" />);
    expect(container.textContent).toContain('No checklist items were extracted');
  });

  it('should toggle item checked state on click', () => {
    render(<ChecklistInteractive items={items} tabLabel="Items" />);
    const firstItem = screen.getByText('Flour').closest('button')!;
    expect(firstItem).toHaveAttribute('aria-pressed', 'false');
    fireEvent.click(firstItem);
    expect(firstItem).toHaveAttribute('aria-pressed', 'true');
  });

  it('should show progress percentage', () => {
    render(<ChecklistInteractive items={items} tabLabel="Items" />);
    expect(screen.getByText('0%')).toBeInTheDocument();
    fireEvent.click(screen.getByText('Flour').closest('button')!);
    expect(screen.getByText('33%')).toBeInTheDocument();
  });

  it('should show essential badge for essential items', () => {
    render(<ChecklistInteractive items={items} tabLabel="Items" />);
    expect(screen.getByText('essential')).toBeInTheDocument();
  });

  it('should show celebration when all items checked', () => {
    render(<ChecklistInteractive items={[{ label: 'Only item' }]} tabLabel="Items" />);
    fireEvent.click(screen.getByText('Only item').closest('button')!);
    expect(screen.getByTestId('celebration')).toBeInTheDocument();
  });

  it('should show servings scaler when scalable', () => {
    render(<ChecklistInteractive items={items} tabLabel="Items" scalable baseServings={4} />);
    expect(screen.getByText('4 servings')).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText('Increase servings'));
    expect(screen.getByText('5 servings')).toBeInTheDocument();
  });

  it('should not decrease servings below 1', () => {
    render(<ChecklistInteractive items={items} tabLabel="Items" scalable baseServings={1} />);
    expect(screen.getByLabelText('Decrease servings')).toBeDisabled();
  });

  it('should render group headers when groups provided', () => {
    const grouped = [
      { label: 'Flour', group: 'Dry' },
      { label: 'Milk', group: 'Wet' },
    ];
    render(<ChecklistInteractive items={grouped} tabLabel="Items" groups={['Dry', 'Wet']} />);
    expect(screen.getByText('Dry')).toBeInTheDocument();
    expect(screen.getByText('Wet')).toBeInTheDocument();
  });

  describe('aria-live toggle feedback', () => {
    it('should announce progress via the polite live region when an item is toggled', () => {
      render(<ChecklistInteractive items={items} tabLabel="Items" />);
      const region = screen.getByRole('status');
      expect(region).toHaveAttribute('aria-live', 'polite');
      const flour = screen.getByText('Flour').closest('button')!;
      fireEvent.click(flour);
      expect(region).toHaveTextContent('Flour checked. 1 of 3 complete.');
      fireEvent.click(flour);
      expect(region).toHaveTextContent('Flour unchecked. 0 of 3 complete.');
    });

    it('should announce completion when the last item is checked', () => {
      render(<ChecklistInteractive items={items} tabLabel="Items" />);
      for (const label of ['Flour', 'Sugar', 'Salt']) {
        fireEvent.click(screen.getByText(label).closest('button')!);
      }
      expect(screen.getByRole('status')).toHaveTextContent(
        'Salt checked. All 3 items complete.',
      );
    });
  });
});
