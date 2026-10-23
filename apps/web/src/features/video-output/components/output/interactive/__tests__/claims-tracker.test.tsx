import { describe, it, expect, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { DirectionProvider } from '@/contexts/DirectionContext';
import { ClaimsTracker } from '../ClaimsTracker';
import type { ClaimItem } from '@vie/types';

const claims: ClaimItem[] = [
  {
    claim: 'The plan creates 1,200 jobs',
    source: 'Mayor Diaz',
    status: 'contested',
    sourceCitation: 'Independent analysts put the figure near 700.',
    timestamp: 210,
  },
  {
    claim: 'Fares will rise 15% in 2026',
    source: 'Transit Authority',
    status: 'supported',
    timestamp: 540,
  },
  {
    claim: 'The city faces a structural deficit',
    status: 'stated',
    timestamp: 760,
  },
];

describe('ClaimsTracker', () => {
  it('renders every claim with its text and source', () => {
    render(<ClaimsTracker claims={claims} />);
    expect(screen.getByText('The plan creates 1,200 jobs')).toBeInTheDocument();
    expect(screen.getByText('Mayor Diaz')).toBeInTheDocument();
    expect(screen.getByText('Fares will rise 15% in 2026')).toBeInTheDocument();
    expect(screen.getByText('Transit Authority')).toBeInTheDocument();
  });

  it('renders a status badge per claim', () => {
    render(<ClaimsTracker claims={claims} />);
    const list = screen.getByRole('list', { name: /tracked claims/i });
    expect(within(list).getByText('Supported')).toBeInTheDocument();
    expect(within(list).getByText('Contested')).toBeInTheDocument();
    expect(within(list).getByText('Stated')).toBeInTheDocument();
  });

  it('shows the source citation when present', () => {
    render(<ClaimsTracker claims={claims} />);
    expect(
      screen.getByText('Independent analysts put the figure near 700.'),
    ).toBeInTheDocument();
  });

  it('filters claims to a single status when a filter toggle is clicked', async () => {
    const user = userEvent.setup();
    render(<ClaimsTracker claims={claims} />);
    // Filters are toggle buttons in a group (no tabpanels → not a tablist).
    const group = screen.getByRole('group', { name: /filter claims by status/i });
    const contested = within(group).getByRole('button', { name: /contested/i });
    expect(contested).toHaveAttribute('aria-pressed', 'false');
    await user.click(contested);
    expect(contested).toHaveAttribute('aria-pressed', 'true');
    const list = screen.getByRole('list', { name: /tracked claims/i });
    expect(within(list).getByText('The plan creates 1,200 jobs')).toBeInTheDocument();
    expect(within(list).queryByText('Fares will rise 15% in 2026')).not.toBeInTheDocument();
  });

  it('promotes the source citation into a labeled evidence block', () => {
    render(<ClaimsTracker claims={claims} />);
    // Exactly one claim carries a citation → one "Evidence" eyebrow.
    expect(screen.getAllByText('Evidence')).toHaveLength(1);
  });

  it('seeks to the claim timestamp when the timestamp button is clicked', async () => {
    const user = userEvent.setup();
    const onSeek = vi.fn();
    render(<ClaimsTracker claims={claims} onSeek={onSeek} />);
    await user.click(screen.getByRole('button', { name: /jump to 3:30/i }));
    expect(onSeek).toHaveBeenCalledWith(210);
  });

  it('should name the seek chip in the page language when the output is Hebrew', () => {
    render(
      <DirectionProvider language="he" isRTL>
        <ClaimsTracker claims={claims} onSeek={vi.fn()} />
      </DirectionProvider>,
    );
    expect(screen.getByRole('button', { name: 'מעבר אל 3:30' })).toBeInTheDocument();
  });

  it('renders an empty-state message when there are no claims', () => {
    render(<ClaimsTracker claims={[]} />);
    expect(screen.getByRole('status')).toHaveTextContent(/no claims/i);
  });
});

describe('ClaimsTracker quotes and time', () => {
  const quoted: ClaimItem[] = [
    { claim: 'Compute doubles yearly', source: 'Host', status: 'stated', quote: 'It doubles every year.', timestamp: 4282 },
  ];

  it('should show the claim quote when the claim carries one', () => {
    render(<ClaimsTracker claims={quoted} />);
    expect(screen.getByText('It doubles every year.')).toBeInTheDocument();
  });

  it('should print an hour-aware time on a claim past 1:00:00', () => {
    render(<ClaimsTracker claims={quoted} onSeek={vi.fn()} />);
    expect(screen.getByRole('button', { name: /1:11:22/ })).toBeInTheDocument();
  });

  it('should seek to the claim second when its chip is clicked', async () => {
    const onSeek = vi.fn();
    render(<ClaimsTracker claims={quoted} onSeek={onSeek} />);
    await userEvent.setup().click(screen.getByRole('button', { name: /1:11:22/ }));
    expect(onSeek).toHaveBeenCalledWith(4282);
  });
});
