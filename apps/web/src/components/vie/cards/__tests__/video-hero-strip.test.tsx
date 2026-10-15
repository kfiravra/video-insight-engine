import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { VideoHeroStrip } from '../VideoHeroStrip';

afterEach(() => {
  vi.restoreAllMocks();
});

/** Lay the tabs out past a 250 px strip: the third tab's end sits beyond it. */
function mockOverflowingLayout(): void {
  const rightEdges: Record<string, number> = { 'tab-overview': 120, 'tab-key_points': 240, 'tab-quizzes': 360 };
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    const right = this.getAttribute('role') === 'tablist' ? 250 : (rightEdges[this.id] ?? 0);
    return { left: 0, top: 0, right, bottom: 0, width: right, height: 0, x: 0, y: 0, toJSON: () => ({}) } as DOMRect;
  });
}

const SAMPLE_TABS = [
  { id: 'overview', label: 'Overview', emoji: '📚', dataSource: '' },
  { id: 'key_points', label: 'Key Points', emoji: '🎯', dataSource: '' },
  { id: 'quizzes', label: 'Quizzes', emoji: '✅', dataSource: '' },
];

describe('VideoHeroStrip', () => {
  it('should render a tablist named "Video insight sections" with one tab per entry', () => {
    render(<VideoHeroStrip tabs={SAMPLE_TABS} activeTabId="overview" trailingPending={[]} />);

    expect(screen.getByRole('tablist', { name: /video insight sections/i })).toBeInTheDocument();
    expect(screen.getAllByRole('tab')).toHaveLength(SAMPLE_TABS.length);
  });

  it('should mark the active tab with aria-selected=true', () => {
    render(<VideoHeroStrip tabs={SAMPLE_TABS} activeTabId="key_points" trailingPending={[]} />);

    expect(screen.getByRole('tab', { selected: true })).toHaveTextContent('Key Points');
  });

  it('should fire onTabSelect with the tab id when a tab is clicked', async () => {
    const onTabSelect = vi.fn();
    const user = userEvent.setup();
    render(<VideoHeroStrip tabs={SAMPLE_TABS} activeTabId="overview" onTabSelect={onTabSelect} trailingPending={[]} />);

    await user.click(screen.getByRole('tab', { name: /quizzes/i }));

    expect(onTabSelect).toHaveBeenCalledWith('quizzes');
  });

  it('should print the shipped item count in place of the planner count in a tab label', () => {
    render(
      <VideoHeroStrip
        tabs={[{ id: 'args', label: '9 Core Arguments', emoji: '🧠', dataSource: '', itemCount: 24 }]}
        activeTabId="args"
        trailingPending={[]}
      />,
    );

    expect(screen.getByRole('tab', { name: '24 Core Arguments' })).toBeInTheDocument();
  });

  it('should drop the planner count from a tab label when the shipped count is unknown', () => {
    render(
      <VideoHeroStrip
        tabs={[{ id: 'args', label: '9 Core Arguments', emoji: '🧠', dataSource: '' }]}
        activeTabId="args"
        trailingPending={[]}
      />,
    );

    expect(screen.getByRole('tab', { name: 'Core Arguments' })).toBeInTheDocument();
  });

  it('should apply the domain gradient to the active tab', () => {
    const gradient = 'linear-gradient(135deg, oklch(70% 0.2 280), oklch(60% 0.2 320))';
    render(<VideoHeroStrip tabs={SAMPLE_TABS} activeTabId="overview" domainGradient={gradient} trailingPending={[]} />);

    expect(screen.getByRole('tab', { selected: true }).getAttribute('style') ?? '').toContain('linear-gradient');
  });

  it('should mark a completed, inactive tab with a check', () => {
    render(
      <VideoHeroStrip
        tabs={SAMPLE_TABS}
        activeTabId="overview"
        completedTabs={new Set(['quizzes'])}
        trailingPending={[]}
      />,
    );

    expect(screen.getByRole('tab', { name: /quizzes completed/i })).toBeInTheDocument();
  });

  it('should trail the live tabs with the planned ones still to come', () => {
    render(
      <VideoHeroStrip
        tabs={SAMPLE_TABS}
        activeTabId="overview"
        trailingPending={[{ id: 'steps', label: 'Steps', emoji: '👣' }]}
      />,
    );

    expect(screen.getByText('Steps')).toBeInTheDocument();
    expect(screen.getAllByRole('tab')).toHaveLength(SAMPLE_TABS.length);
  });

  it('should show a "+N" overflow pill when tabs sit past the strip edge', () => {
    mockOverflowingLayout();
    render(<VideoHeroStrip tabs={SAMPLE_TABS} activeTabId="overview" trailingPending={[]} />);

    expect(screen.getByRole('button', { name: 'Show 1 more sections' })).toHaveTextContent('+1');
  });

  it('should scroll the strip towards its end when the pill is clicked', async () => {
    mockOverflowingLayout();
    const scrollBy = vi.fn();
    HTMLElement.prototype.scrollBy = scrollBy;
    const user = userEvent.setup();
    render(<VideoHeroStrip tabs={SAMPLE_TABS} activeTabId="overview" trailingPending={[]} />);

    await user.click(screen.getByRole('button', { name: 'Show 1 more sections' }));

    expect(scrollBy).toHaveBeenCalledWith(expect.objectContaining({ left: expect.any(Number), behavior: 'smooth' }));
  });

  it('should count tabs past the start edge in a right-to-left strip', () => {
    // Mirror of the LTR layout: the strip spans 0–250 from the right, so the
    // third tab (its left edge at -110) hangs past the start edge.
    const leftEdges: Record<string, number> = { 'tab-overview': 130, 'tab-key_points': 10, 'tab-quizzes': -110 };
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      const left = this.getAttribute('role') === 'tablist' ? 0 : (leftEdges[this.id] ?? 0);
      return { left, top: 0, right: 250, bottom: 0, width: 250 - left, height: 0, x: left, y: 0, toJSON: () => ({}) } as DOMRect;
    });
    const realGetComputedStyle = window.getComputedStyle.bind(window);
    vi.spyOn(window, 'getComputedStyle').mockImplementation((element: Element) => {
      const declaration = realGetComputedStyle(element);
      Object.defineProperty(declaration, 'direction', { value: 'rtl' });
      return declaration;
    });
    render(<VideoHeroStrip tabs={SAMPLE_TABS} activeTabId="overview" trailingPending={[]} />);

    expect(screen.getByRole('button', { name: 'Show 1 more sections' })).toBeInTheDocument();
  });

  it('should not show an overflow pill when every tab fits', () => {
    render(<VideoHeroStrip tabs={SAMPLE_TABS} activeTabId="overview" trailingPending={[]} />);

    expect(screen.queryByRole('button', { name: /^\+\d/ })).not.toBeInTheDocument();
  });
});
