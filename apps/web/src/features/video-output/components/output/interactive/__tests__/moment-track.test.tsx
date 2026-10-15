import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, within, type RenderResult } from '@testing-library/react';

import { MomentTrack, type MomentItem } from '../MomentTrack';

const mixedItems: MomentItem[] = [
  { time: '0:00', seconds: 0, label: 'Introduction', description: 'Welcome to the video', mood: 'calm' },
  { time: '2:00', seconds: 120, endSeconds: 187, label: 'Best moment', description: 'The peak of the video', mood: 'exciting', tags: ['highlight'] },
  { time: '2:30', seconds: 150, label: 'Main Topic', speaker: 'John', mood: 'excited' },
  { time: '5:00', seconds: 300, endSeconds: 345, label: 'Key insight', mood: 'calm' },
  { time: '8:00', seconds: 480, label: 'Conclusion', mood: 'calm' },
];

const onlyMoments: MomentItem[] = [
  { time: '0:00', seconds: 0, label: 'Intro', mood: 'calm' },
  { time: '2:30', seconds: 150, label: 'Middle' },
  { time: '5:00', seconds: 300, label: 'Outro', mood: 'calm' },
];

const onlyClips: MomentItem[] = [
  { time: '0:30', seconds: 30, endSeconds: 90, label: 'Clip A', description: 'Nice range', mood: 'highlight' },
  { time: '4:00', seconds: 240, endSeconds: 330, label: 'Clip B', mood: 'demo' },
];

/** Render + switch to the timeline view (grid is the default). */
function renderTimeline(ui: React.ReactElement): RenderResult {
  const result = render(ui);
  fireEvent.click(screen.getByTestId('view-timeline'));
  return result;
}

describe('MomentTrack', () => {
  beforeEach(() => {
    Object.assign(navigator, {
      clipboard: { writeText: vi.fn().mockResolvedValue(undefined) },
    });
  });

  it('renders an empty state for empty items', () => {
    const { container } = render(<MomentTrack items={[]} />);
    expect(container.textContent).toContain('No moments were extracted');
  });

  describe('grid view (default)', () => {
    it('defaults to the grid view with one seek-button card per item', () => {
      render(<MomentTrack items={mixedItems} onSeek={vi.fn()} />);
      expect(screen.getByTestId('moment-grid')).toBeInTheDocument();
      expect(screen.getByTestId('view-grid')).toHaveAttribute('aria-pressed', 'true');
      const cards = screen.getAllByTestId(/^gallery-card-/);
      expect(cards).toHaveLength(mixedItems.length);
      cards.forEach((card) => expect(card.tagName).toBe('BUTTON'));
    });

    it('renders the card hero with frame thumbnail and label below — and no LLM scene badge', () => {
      const withFrame: MomentItem[] = [
        {
          time: '1:00',
          seconds: 60,
          label: 'Framed moment',
          thumbnailUrl: 'https://example.com/frame.jpg',
          frameCaption: 'A slide with the key formula',
          frameSceneType: 'slide',
        },
      ];
      render(<MomentTrack items={withFrame} />);
      const card = screen.getByTestId('gallery-card-0');
      const img = card.querySelector('img');
      expect(img?.getAttribute('src')).toBe('https://example.com/frame.jpg');
      expect(within(card).getByText('Framed moment')).toBeInTheDocument();
      // Scene type is internal pipeline signal — never user-facing.
      expect(within(card).queryByText('slide')).not.toBeInTheDocument();
    });

    it('does not render frameCaption/frameOcr in the grid', () => {
      const withFrame: MomentItem[] = [
        {
          time: '1:00',
          seconds: 60,
          label: 'Framed moment',
          thumbnailUrl: 'https://example.com/frame.jpg',
          frameCaption: 'A slide with the key formula',
          frameOcr: 'y = mx + b',
        },
      ];
      render(<MomentTrack items={withFrame} />);
      expect(screen.queryByText('A slide with the key formula')).not.toBeInTheDocument();
      expect(screen.queryByText(/y = mx \+ b/)).not.toBeInTheDocument();
    });

    it('renders a frameless plate with mono timestamp instead of an image', () => {
      render(<MomentTrack items={[{ time: '2:30', seconds: 150, label: 'No frame here', emoji: '💡' }]} />);
      const card = screen.getByTestId('gallery-card-0');
      expect(card.querySelector('img')).toBeNull();
      expect(within(card).getByText('2:30')).toBeInTheDocument();
      expect(within(card).getByText('💡')).toBeInTheDocument();
    });

    it('clicking an image-backed card opens the lightbox and never seeks', () => {
      const onSeek = vi.fn();
      const withFrame: MomentItem[] = [
        { time: '1:00', seconds: 60, label: 'Framed', thumbnailUrl: 'https://example.com/f.jpg' },
      ];
      render(<MomentTrack items={withFrame} onSeek={onSeek} />);
      fireEvent.click(screen.getByTestId('gallery-card-0'));
      expect(onSeek).not.toHaveBeenCalled();
      const dialog = screen.getByRole('dialog', { name: 'Image lightbox' });
      expect(within(dialog).getByRole('img')).toHaveAttribute('src', 'https://example.com/f.jpg');
      fireEvent.keyDown(document, { key: 'Escape' });
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });

    it('the explicit Jump pill is the seek affordance on image cards', () => {
      const onSeek = vi.fn();
      const withFrame: MomentItem[] = [
        { time: '1:00', seconds: 60, label: 'Framed', thumbnailUrl: 'https://example.com/f.jpg' },
      ];
      render(<MomentTrack items={withFrame} onSeek={onSeek} />);
      fireEvent.click(screen.getByTestId('gallery-jump-0'));
      expect(onSeek).toHaveBeenCalledWith(60);
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });

    it('frameless cards fall back to seeking on click (nothing to show)', () => {
      const onSeek = vi.fn();
      render(<MomentTrack items={onlyMoments} onSeek={onSeek} />);
      fireEvent.click(screen.getByTestId('gallery-card-1'));
      expect(onSeek).toHaveBeenCalledWith(150);
    });

    it('shows a clip-duration pill on clip cards', () => {
      render(<MomentTrack items={[mixedItems[1]]} />);
      // 187 - 120 = 67s → "1m 7s"
      expect(screen.getByText('1m 7s')).toBeInTheDocument();
    });

    it('share button copies the link without triggering a seek', async () => {
      const onSeek = vi.fn();
      const writeText = vi.spyOn(navigator.clipboard, 'writeText');
      // Undescribed so the grid stays the default view (see 0a.6 block below).
      render(<MomentTrack items={[{ ...mixedItems[1], description: undefined }]} onSeek={onSeek} />);
      fireEvent.click(screen.getByTestId('gallery-share-0'));
      expect(writeText).toHaveBeenCalled();
      expect((writeText.mock.calls[0][0] as string)).toMatch(/#t=120,187$/);
      expect(onSeek).not.toHaveBeenCalled();
      expect(await screen.findByText('Link copied')).toBeInTheDocument();
    });

    it('marks the active card when currentTime falls inside a clip range', () => {
      render(<MomentTrack items={mixedItems} currentTime={150} />);
      const active = document.querySelectorAll('[data-active="true"]');
      expect(active.length).toBeGreaterThanOrEqual(1);
      expect(active[0].textContent).toContain('Best moment');
    });
  });

  describe('view toggle and persistence', () => {
    it('switches to the timeline view and persists the choice', () => {
      render(<MomentTrack items={mixedItems} />);
      fireEvent.click(screen.getByTestId('view-timeline'));
      expect(screen.getByRole('list', { name: 'Moment track' })).toBeInTheDocument();
      expect(screen.queryByTestId('moment-grid')).toBeNull();
      expect(screen.getByTestId('view-timeline')).toHaveAttribute('aria-pressed', 'true');
      expect(window.localStorage.setItem).toHaveBeenCalledWith('vie-moment-view', 'timeline');
    });

    it('initializes from a persisted timeline preference', () => {
      window.localStorage.setItem('vie-moment-view', 'timeline');
      render(<MomentTrack items={mixedItems} />);
      expect(screen.getByRole('list', { name: 'Moment track' })).toBeInTheDocument();
      expect(screen.queryByTestId('moment-grid')).toBeNull();
    });

    it('exposes the toggle as a two-button group', () => {
      render(<MomentTrack items={mixedItems} />);
      const group = screen.getByRole('group', { name: 'View' });
      const buttons = within(group).getAllByRole('button');
      expect(buttons).toHaveLength(2);
      expect(buttons[0]).toHaveAttribute('aria-pressed');
      expect(buttons[1]).toHaveAttribute('aria-pressed');
    });
  });

  describe('default view by description share (0a.6 / R7)', () => {
    const described = (n: number, label: string): MomentItem => ({
      time: `${n}:00`,
      seconds: n * 60,
      label,
      description: `What was established at minute ${n}`,
    });
    const bare = (n: number, label: string): MomentItem => ({ time: `${n}:00`, seconds: n * 60, label });

    it('should open in the timeline view when at least half the items carry a description', () => {
      render(<MomentTrack items={[described(1, 'A'), described(2, 'B'), described(3, 'C'), bare(4, 'D')]} />);
      expect(screen.getByRole('list', { name: 'Moment track' })).toBeInTheDocument();
      expect(screen.getByTestId('view-timeline')).toHaveAttribute('aria-pressed', 'true');
    });

    it('should treat exactly half as enough for the timeline default', () => {
      render(<MomentTrack items={[described(1, 'A'), described(2, 'B'), bare(3, 'C'), bare(4, 'D')]} />);
      expect(screen.getByRole('list', { name: 'Moment track' })).toBeInTheDocument();
    });

    it('should keep the grid when fewer than half the items carry a description', () => {
      render(<MomentTrack items={[described(1, 'A'), bare(2, 'B'), bare(3, 'C'), bare(4, 'D')]} />);
      expect(screen.getByTestId('moment-grid')).toBeInTheDocument();
      expect(screen.getByTestId('view-grid')).toHaveAttribute('aria-pressed', 'true');
    });

    it('should not count blank descriptions', () => {
      const blank: MomentItem[] = [
        { time: '1:00', seconds: 60, label: 'A', description: '   ' },
        { time: '2:00', seconds: 120, label: 'B', description: '' },
      ];
      render(<MomentTrack items={blank} />);
      expect(screen.getByTestId('moment-grid')).toBeInTheDocument();
    });

    it('should let an explicit stored grid preference override the timeline default', () => {
      window.localStorage.setItem('vie-moment-view', 'grid');
      render(<MomentTrack items={[described(1, 'A'), described(2, 'B')]} />);
      expect(screen.getByTestId('moment-grid')).toBeInTheDocument();
    });

    it('should re-derive the default when the items change and no choice was stored', () => {
      const { rerender } = render(<MomentTrack items={[bare(1, 'Outline A'), bare(2, 'Outline B')]} />);
      expect(screen.getByTestId('moment-grid')).toBeInTheDocument();
      rerender(<MomentTrack items={[described(1, 'A'), described(2, 'B')]} />);
      expect(screen.getByRole('list', { name: 'Moment track' })).toBeInTheDocument();
    });

    it('should keep the view the user toggled to even after the items change', () => {
      const { rerender } = render(<MomentTrack items={[described(1, 'A'), described(2, 'B')]} />);
      fireEvent.click(screen.getByTestId('view-grid'));
      rerender(<MomentTrack items={[described(1, 'A'), described(2, 'B'), described(3, 'C')]} />);
      expect(screen.getByTestId('moment-grid')).toBeInTheDocument();
    });

    it('should render the full label in the timeline view with no line clamp', () => {
      const longLabel =
        'Opening scenario: a quiet 40-year-old suddenly punches a coworker, has an affair, and starts gambling — the neurologist finds a tumour pressing on the frontal lobe';
      render(<MomentTrack items={[{ ...described(1, longLabel), label: longLabel }, described(2, 'B')]} />);
      const label = screen.getByText(longLabel);
      // The clamp is a style, but it is exactly what the acceptance forbids —
      // jsdom has no layout, so the class is the only observable regression guard.
      expect(label.className).not.toMatch(/line-clamp/);
      expect(label).not.toHaveAttribute('title');
    });
  });

  describe('timeline view', () => {
    it('renders all items', () => {
      renderTimeline(<MomentTrack items={mixedItems} />);
      expect(screen.getByText('Introduction')).toBeInTheDocument();
      expect(screen.getByText('Best moment')).toBeInTheDocument();
      expect(screen.getByText('Main Topic')).toBeInTheDocument();
      expect(screen.getByText('Key insight')).toBeInTheDocument();
      expect(screen.getByText('Conclusion')).toBeInTheDocument();
    });

    it('shows timestamps', () => {
      renderTimeline(<MomentTrack items={mixedItems} />);
      // Time can appear in both the seek chip and the frameless glyph plate.
      expect(screen.getAllByText('0:00').length).toBeGreaterThan(0);
      expect(screen.getAllByText('2:30').length).toBeGreaterThan(0);
    });

    it('shows speaker when present', () => {
      renderTimeline(<MomentTrack items={mixedItems} />);
      expect(screen.getByText('John')).toBeInTheDocument();
    });

    it('shows mood badges', () => {
      renderTimeline(<MomentTrack items={mixedItems} />);
      expect(screen.getAllByText('calm').length).toBeGreaterThan(0);
    });

    it('shows mood filter pills when multiple moods', () => {
      renderTimeline(<MomentTrack items={mixedItems} />);
      expect(screen.getByText('All moods')).toBeInTheDocument();
    });

    it('shows descriptions and tags without any click', () => {
      renderTimeline(<MomentTrack items={mixedItems} />);
      expect(screen.getByText('Welcome to the video')).toBeInTheDocument();
      expect(screen.getByText('The peak of the video')).toBeInTheDocument();
      expect(screen.getByText('highlight')).toBeInTheDocument();
    });

    it('has no expand toggles — clicks are reserved for actions', () => {
      renderTimeline(<MomentTrack items={mixedItems} />);
      expect(document.querySelector('[aria-expanded]')).toBeNull();
      expect(screen.queryByText(/show more/i)).not.toBeInTheDocument();
    });

    it('calls onSeek when a timestamp chip is clicked', () => {
      const onSeek = vi.fn();
      renderTimeline(<MomentTrack items={onlyMoments} onSeek={onSeek} />);
      fireEvent.click(screen.getByRole('button', { name: 'Jump to 0:00' }));
      expect(onSeek).toHaveBeenCalledWith(0);
    });

    it('renders the timestamp chip as a native button so keyboard activation is built-in', () => {
      const onSeek = vi.fn();
      renderTimeline(<MomentTrack items={onlyMoments} onSeek={onSeek} />);
      const chip = screen.getByRole('button', { name: 'Jump to 2:30' });
      expect(chip.tagName).toBe('BUTTON');
      fireEvent.click(chip);
      expect(onSeek).toHaveBeenCalledWith(150);
    });

    it('renders points and clips with distinct data-kind attributes', () => {
      renderTimeline(<MomentTrack items={mixedItems} />);
      expect(document.querySelectorAll('li[data-kind="moment"]').length).toBe(3);
      expect(document.querySelectorAll('li[data-kind="clip"]').length).toBe(2);
    });

    it('sets capsule height proportional to clip duration', () => {
      renderTimeline(<MomentTrack items={mixedItems} />);
      const capsules = document.querySelectorAll('[data-testid^="capsule-"]') as NodeListOf<HTMLElement>;
      expect(capsules.length).toBe(2);
      const heights = Array.from(capsules).map((el) => parseInt(el.style.height, 10));
      expect(heights.every((h) => h >= 32 && h <= 120)).toBe(true);
    });

    it('shows segmented type filter only when dataset has both kinds', () => {
      const { rerender } = renderTimeline(<MomentTrack items={mixedItems} />);
      expect(screen.getByTestId('type-filter-clips')).toBeInTheDocument();
      rerender(<MomentTrack items={onlyMoments} />);
      expect(screen.queryByTestId('type-filter-clips')).toBeNull();
      rerender(<MomentTrack items={onlyClips} />);
      expect(screen.queryByTestId('type-filter-clips')).toBeNull();
    });

    it('filters to clips only when Clips is pressed', () => {
      renderTimeline(<MomentTrack items={mixedItems} />);
      fireEvent.click(screen.getByTestId('type-filter-clips'));
      expect(screen.queryByText('Introduction')).toBeNull();
      expect(screen.getByText('Best moment')).toBeInTheDocument();
      expect(screen.getByText('Key insight')).toBeInTheDocument();
    });

    it('marks the active item when currentTime falls inside a clip range', () => {
      renderTimeline(<MomentTrack items={mixedItems} currentTime={150} />);
      const activeElements = document.querySelectorAll('li[data-active="true"]');
      expect(activeElements.length).toBeGreaterThanOrEqual(1);
      expect(activeElements[0].textContent).toContain('Best moment');
    });

    it('shows live progress for active clip', () => {
      renderTimeline(<MomentTrack items={mixedItems} currentTime={150} />);
      // Best moment starts at 120s, currentTime=150s ⇒ 30s elapsed → "2:00 / 30s"
      expect(screen.getByText(/\/ 30s/)).toBeInTheDocument();
    });

    it('share button copies range hash for clips', async () => {
      const writeText = vi.spyOn(navigator.clipboard, 'writeText');
      renderTimeline(<MomentTrack items={[mixedItems[1]]} />);
      fireEvent.click(screen.getByTestId('share-0'));
      expect(writeText).toHaveBeenCalled();
      expect(writeText.mock.calls[0][0] as string).toMatch(/#t=120,187$/);
      // Await the confirmation so the copied-state update lands inside act().
      expect(await screen.findByText('Link copied')).toBeInTheDocument();
    });

    it('share button copies single-seconds hash for moments', async () => {
      const writeText = vi.spyOn(navigator.clipboard, 'writeText');
      renderTimeline(<MomentTrack items={[mixedItems[0]]} />);
      fireEvent.click(screen.getByTestId('share-0'));
      const arg = writeText.mock.calls[0][0] as string;
      expect(arg).toMatch(/#t=0$/);
      expect(arg).not.toMatch(/,/);
      expect(await screen.findByText('Link copied')).toBeInTheDocument();
    });

    it('shows the frame thumbnail without rendering LLM caption/OCR text', () => {
      const withFrame: MomentItem[] = [
        {
          time: '1:00',
          seconds: 60,
          label: 'Framed moment',
          thumbnailUrl: 'https://example.com/frame.jpg',
          frameCaption: 'A slide with the key formula',
          frameOcr: 'y = mx + b',
        },
      ];
      renderTimeline(<MomentTrack items={withFrame} />);
      const img = document.querySelector('img');
      expect(img).not.toBeNull();
      expect(img?.getAttribute('src')).toBe('https://example.com/frame.jpg');
      expect(document.querySelectorAll('img')).toHaveLength(1);
      // Frame metadata is internal pipeline signal — never user-facing text.
      expect(screen.queryByText('A slide with the key formula')).not.toBeInTheDocument();
      expect(screen.queryByText(/y = mx \+ b/)).not.toBeInTheDocument();
    });

    it('clicking the timeline image opens the lightbox; the timestamp chip still seeks', () => {
      const onSeek = vi.fn();
      const withFrame: MomentItem[] = [
        { time: '1:00', seconds: 60, label: 'Framed', thumbnailUrl: 'https://example.com/f.jpg' },
      ];
      renderTimeline(<MomentTrack items={withFrame} onSeek={onSeek} />);
      fireEvent.click(screen.getByTestId('timeline-expand-0'));
      expect(onSeek).not.toHaveBeenCalled();
      expect(screen.getByRole('dialog', { name: 'Image lightbox' })).toBeInTheDocument();
      fireEvent.keyDown(document, { key: 'Escape' });

      fireEvent.click(screen.getByRole('button', { name: 'Jump to 1:00' }));
      expect(onSeek).toHaveBeenCalledWith(60);
    });

    it('renders the text column before the media column (text leads, image trails)', () => {
      const withFrame: MomentItem[] = [
        { time: '1:00', seconds: 60, label: 'Framed', thumbnailUrl: 'https://example.com/f.jpg' },
      ];
      renderTimeline(<MomentTrack items={withFrame} />);
      const media = document.querySelector('[data-slot="moment-media"]');
      const label = screen.getByText('Framed');
      expect(media).not.toBeNull();
      // The label's column precedes the figure in DOM order → trailing media.
      expect(
        label.compareDocumentPosition(media as Node) & Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
    });

    it('always renders the media column — frameless rows get a glyph plate', () => {
      renderTimeline(<MomentTrack items={onlyMoments} />);
      const media = document.querySelectorAll('[data-slot="moment-media"]');
      expect(media.length).toBe(onlyMoments.length);
      const plates = document.querySelectorAll('[data-testid^="glyph-plate-"]');
      expect(plates.length).toBe(onlyMoments.length);
    });

    it('shows a "Link copied" confirmation after a successful share', async () => {
      renderTimeline(<MomentTrack items={[mixedItems[0]]} />);
      fireEvent.click(screen.getByTestId('share-0'));
      expect(await screen.findByText('Link copied')).toBeInTheDocument();
    });

    it('respects prefers-reduced-motion by skipping the pulse class', () => {
      // Global test setup defaults prefers-reduced-motion to true.
      renderTimeline(<MomentTrack items={onlyMoments} currentTime={0} />);
      expect(document.querySelectorAll('.animate-pulse-once').length).toBe(0);
    });

    it('never marks a row focused under the default (inert) IntersectionObserver', () => {
      renderTimeline(<MomentTrack items={mixedItems} />);
      expect(document.querySelector('[data-focus]')).toBeNull();
    });
  });
});
