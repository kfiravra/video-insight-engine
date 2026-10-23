import { describe, it, expect } from 'vitest';
import { render, screen, within } from '@testing-library/react';

import { ClaimsTracker } from '../ClaimsTracker';
import { OverviewInteractive } from '../OverviewInteractive';
import type { ClaimItem } from '@vie/types';

const claims: ClaimItem[] = [
  { claim: 'The plan creates 1,200 jobs', source: 'Mayor Diaz', status: 'contested', timestamp: 210 },
  { claim: 'Fares will rise 15% in 2026', source: 'Transit Authority', status: 'supported', timestamp: 540 },
];

const highlights = [
  { emoji: '🔥', text: 'Sear the meat first' },
  { emoji: '🧂', text: 'Salt the water well' },
];

/** Whether every list item in `list` is one of its direct children (a valid <ul>). */
function itemsAreDirectChildren(list: HTMLElement): boolean {
  const rows = within(list).getAllByRole('listitem');
  return rows.length > 0 && rows.every((row) => row.parentElement === list);
}

describe('list markup inside staggered entrances', () => {
  it('should keep every claim a direct child of the tracked-claims list when the claims fade in', () => {
    render(<ClaimsTracker claims={claims} />);
    expect(itemsAreDirectChildren(screen.getByRole('list', { name: /tracked claims/i }))).toBe(true);
  });

  it('should keep every overview highlight a direct child of its list when the highlights fade in', () => {
    render(<OverviewInteractive highlights={highlights} />);
    expect(itemsAreDirectChildren(screen.getByText('Sear the meat first').closest('ul') as HTMLElement)).toBe(true);
  });
});
