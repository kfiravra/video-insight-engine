import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';


import { SpotExplorer } from '../SpotExplorer';

const spots = [
  { name: 'Eiffel Tower', emoji: '🗼', description: 'Iconic landmark', rating: 4.8, cost: '$$$', duration: '2 hours', mapQuery: 'Eiffel Tower Paris' },
  { name: 'Louvre', emoji: '🎨', description: 'World famous museum', tips: 'Go early to avoid crowds' },
  { name: 'Notre Dame', emoji: '⛪', description: 'Historic cathedral' },
];

describe('SpotExplorer', () => {
  it('should render all spots', () => {
    render(<SpotExplorer spots={spots} />);
    // Eiffel Tower appears twice: card title + always-visible map link text.
    expect(screen.getAllByText('Eiffel Tower').length).toBeGreaterThan(0);
    expect(screen.getByText('Louvre')).toBeInTheDocument();
    expect(screen.getByText('Notre Dame')).toBeInTheDocument();
  });

  it('renders an empty state for empty spots', () => {
    const { container } = render(<SpotExplorer spots={[]} />);
    expect(container.textContent).toContain('No highlights were extracted');
  });

  it('should show rating when present', () => {
    render(<SpotExplorer spots={spots} />);
    expect(screen.getByText('4.8')).toBeInTheDocument();
  });

  it('should show cost badge', () => {
    render(<SpotExplorer spots={spots} />);
    expect(screen.getByText('$$$')).toBeInTheDocument();
  });

  it('should show duration', () => {
    render(<SpotExplorer spots={spots} />);
    expect(screen.getByText('2 hours')).toBeInTheDocument();
  });

  it('should show descriptions, tips, and actions without any click', () => {
    render(<SpotExplorer spots={spots} />);
    // Content is visible by default — no accordion, no reveal click.
    expect(screen.getByText('Iconic landmark')).toBeInTheDocument();
    expect(screen.getByText(/Go early to avoid crowds/)).toBeInTheDocument();
    expect(screen.getByLabelText('View Eiffel Tower on Google Maps')).toBeInTheDocument();
  });

  it('should have no "Show more" button and no expand toggles', () => {
    const longDesc = 'A very long description that easily exceeds any old expand threshold for spots by a wide margin.';
    render(<SpotExplorer spots={[{ name: 'Spot', emoji: '📍', description: longDesc, tips: 'Some tip' }]} />);
    expect(screen.queryByText(/show more/i)).not.toBeInTheDocument();
    expect(document.querySelector('[aria-expanded]')).toBeNull();
  });

  it('should show section nav with an All pill and per-section counts', () => {
    const sections = [
      { label: 'Day 1: Paris', spotIndices: [0, 1] },
      { label: 'Day 2: Paris', spotIndices: [2] },
    ];
    render(<SpotExplorer spots={spots} sections={sections} />);
    // "All (N)" leads the nav and is active by default (every spot visible).
    expect(screen.getByText('All (3)')).toBeInTheDocument();
    expect(screen.getByText('Day 1: Paris (2)')).toBeInTheDocument();
    expect(screen.getByText('Day 2: Paris (1)')).toBeInTheDocument();
    expect(screen.getByText('Notre Dame')).toBeInTheDocument();
    // Selecting a section filters the list and shows its header.
    fireEvent.click(screen.getByText('Day 1: Paris (2)'));
    expect(screen.getByText('Day 1: Paris')).toBeInTheDocument();
    expect(screen.queryByText('Notre Dame')).not.toBeInTheDocument();
  });

  it('renders specs as a muted evidence line and pronunciation next to the name', () => {
    render(
      <SpotExplorer
        spots={[
          {
            name: 'すみません',
            emoji: '🗣️',
            description: 'Excuse me / I am sorry',
            pronunciation: 'su-mi-ma-sen',
            specs: 'Polite register, used to get attention',
          },
        ]}
      />,
    );
    expect(screen.getByText('su-mi-ma-sen')).toBeInTheDocument();
    expect(screen.getByText('Polite register, used to get attention')).toBeInTheDocument();
  });

  it('renders the full description exactly once with no clamp state', () => {
    const longDesc =
      'A very long description that easily exceeds the forty character expand threshold for spots.';
    render(<SpotExplorer spots={[{ name: 'Spot', emoji: '📍', description: longDesc }]} />);
    // One full copy at rest — no clamped duplicate, no expanded twin.
    expect(screen.getAllByText(longDesc)).toHaveLength(1);
  });

  describe('timestamp and quote (output-quality 0a.4)', () => {
    const keyIdea = {
      name: 'Half as much code in 20 months',
      emoji: '💻',
      description: 'Lines of code are a fuzzy measure, but the drop is undeniable.',
      timestamp: 373,
      quote: 'One line of Ruby is worth far more than a line of Rust.',
    };

    it('should render a seek chip that seeks the player when a timestamp is present', async () => {
      const user = userEvent.setup();
      const onSeek = vi.fn();
      render(<SpotExplorer spots={[keyIdea]} onSeek={onSeek} />);
      await user.click(screen.getByRole('button', { name: 'Jump to 6:13' }));
      expect(onSeek).toHaveBeenCalledWith(373);
    });

    it('should render the timestamp as a static chip when no seek handler is given', () => {
      render(<SpotExplorer spots={[keyIdea]} />);
      expect(screen.getByText('6:13')).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /jump to/i })).not.toBeInTheDocument();
    });

    it('should render the quote in italics when present', () => {
      render(<SpotExplorer spots={[keyIdea]} onSeek={vi.fn()} />);
      const quote = screen.getByText(keyIdea.quote);
      expect(quote.tagName).toBe('BLOCKQUOTE');
      expect(quote).toHaveClass('italic');
    });

    it('should render hour-long timestamps as h:mm:ss', () => {
      render(<SpotExplorer spots={[{ ...keyIdea, timestamp: 3725 }]} onSeek={vi.fn()} />);
      expect(screen.getByRole('button', { name: 'Jump to 1:02:05' })).toBeInTheDocument();
    });

    it('should render no chip and no quote when the fields are absent', () => {
      render(<SpotExplorer spots={spots} onSeek={vi.fn()} />);
      expect(screen.queryByRole('button', { name: /jump to/i })).not.toBeInTheDocument();
      expect(document.querySelector('blockquote')).toBeNull();
    });

    it('should render no chip for a negative or NaN timestamp', () => {
      render(
        <SpotExplorer
          spots={[{ ...keyIdea, timestamp: -3 }, { ...keyIdea, name: 'NaN idea', timestamp: Number.NaN }]}
          onSeek={vi.fn()}
        />,
      );
      expect(screen.queryByRole('button', { name: /jump to/i })).not.toBeInTheDocument();
    });

    it('should ignore null timestamp and blank quote from the assembler', () => {
      render(<SpotExplorer spots={[{ ...keyIdea, timestamp: null, quote: '   ' }]} onSeek={vi.fn()} />);
      expect(screen.queryByRole('button', { name: /jump to/i })).not.toBeInTheDocument();
      expect(document.querySelector('blockquote')).toBeNull();
    });
  });
});
