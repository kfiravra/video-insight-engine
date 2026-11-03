import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { StatBanner } from '../StatBanner';

const stats = [
  { label: 'Duration', value: '12 min', emoji: '⏱️' },
  { label: 'Steps', value: '8' },
];

describe('StatBanner', () => {
  it('renders each stat label and value', () => {
    render(<StatBanner stats={stats} />);
    expect(screen.getByText('Duration')).toBeInTheDocument();
    expect(screen.getByText('12 min')).toBeInTheDocument();
    expect(screen.getByText('Steps')).toBeInTheDocument();
    expect(screen.getByText('8')).toBeInTheDocument();
  });

  it('exposes stats as a labelled list', () => {
    render(<StatBanner stats={stats} />);
    const list = screen.getByRole('list', { name: /key stats/i });
    expect(list).toBeInTheDocument();
  });

  it('lays the stats out as a responsive grid: two columns on phones, auto-fit tiles from sm up', () => {
    render(<StatBanner stats={stats} />);
    const list = screen.getByRole('list', { name: /key stats/i });
    expect(list.className).toContain('grid');
    expect(list.className).toContain('grid-cols-2');
    expect(list.className).toContain('repeat(auto-fit,minmax(140px,1fr))');
    expect(list.className).not.toContain('flex-wrap');
  });

  it('never uses inline-block tiles or mid-word breaking', () => {
    const { container } = render(
      <StatBanner stats={[{ label: 'Throughput', value: 'Extraordinarily-long-value' }]} />,
    );
    expect(container.innerHTML).not.toContain('inline-block');
    expect(container.innerHTML).not.toContain('break-all');
    expect(container.querySelector('.break-words')).not.toBeNull();
  });

  it('renders a numeric value instead of throwing', () => {
    render(<StatBanner stats={[{ label: 'Steps', value: 8 as unknown as string }]} />);
    expect(screen.getByText('8')).toBeInTheDocument();
  });

  it('renders nothing when no stat has both a label and value', () => {
    const { container } = render(
      <StatBanner stats={[{ label: '', value: '' }, { label: 'X', value: '' }]} />,
    );
    expect(container.innerHTML).toBe('');
  });

  it('should accept the assembler tile fields meaning and timestamp', () => {
    const tiles = [{ label: 'Deaths', value: '1 million', meaning: 'Iran-Iraq war toll', timestamp: 242 }];
    render(<StatBanner stats={tiles} />);
    expect(screen.getByText('1 million')).toBeInTheDocument();
  });

  it('renders nothing for an empty stats array', () => {
    const { container } = render(<StatBanner stats={[]} />);
    expect(container.innerHTML).toBe('');
  });
});

describe('StatBanner meaning and seek', () => {
  const tiles = [{ label: 'Latency', value: '40 ms', meaning: 'Fast enough for voice.', timestamp: 125 }];

  it('should show the meaning line when a tile carries one', () => {
    render(<StatBanner stats={tiles} />);
    expect(screen.getByText('Fast enough for voice.')).toBeInTheDocument();
  });

  it('should seek the player when the tile chip is clicked', async () => {
    const onSeek = vi.fn();
    render(<StatBanner stats={tiles} onSeek={onSeek} />);
    await userEvent.setup().click(screen.getByRole('button', { name: /2:05/ }));
    expect(onSeek).toHaveBeenCalledWith(125);
  });

  it('should show a static time marker when no seek handler exists', () => {
    render(<StatBanner stats={tiles} />);
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });
});
