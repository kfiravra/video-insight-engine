import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

vi.mock('../../Celebration', () => ({
  Celebration: ({ title }: { title: string }) => <div data-testid="celebration">{title}</div>,
}));

import { ChecklistInteractive } from '../ChecklistInteractive';

const items = [
  { label: 'Flour', amount: 2, unit: 'cups', group: 'Dry' },
  { label: 'Sugar', amount: 1, unit: 'cup', group: 'Dry' },
  { label: 'Milk', group: 'Wet' },
];

describe('ChecklistInteractive list markup', () => {
  it('should render every item as a direct child of its list when the items stagger in', () => {
    render(<ChecklistInteractive items={items} groups={['Dry', 'Wet']} tabLabel="Ingredients" />);

    const lists = screen.getAllByRole('list');
    const rows = screen.getAllByRole('listitem');

    expect(rows).toHaveLength(items.length);
    expect(rows.every((row) => lists.includes(row.parentElement as HTMLElement))).toBe(true);
  });
});
