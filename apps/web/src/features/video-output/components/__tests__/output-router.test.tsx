import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { TabEntry } from '@vie/types';
import type { StreamTab } from '@/features/video-output/lib/streaming/stream-tabs';

import { OutputRouter } from '../OutputRouter';

function makeMomentItems(count: number, withThumbCount: number): Array<Record<string, unknown>> {
  return Array.from({ length: count }, (_, i) => ({
    time: `${i}:00`,
    seconds: i * 60,
    label: `Moment ${i}`,
    ...(i < withThumbCount ? { thumbnailUrl: `https://example.com/f${i}.jpg` } : {}),
  }));
}

function momentTab(items: Array<Record<string, unknown>>): TabEntry {
  return { id: 'moments', label: 'Moments', emoji: '🎬', component: 'moment_track', props: { items } };
}

const filmstripTab: TabEntry = {
  id: 'filmstrip',
  label: 'Filmstrip',
  emoji: '🎞️',
  component: 'video_filmstrip',
  props: {
    frames: [
      { thumbnailUrl: 'https://example.com/a.jpg', timestamp: 10 },
      { thumbnailUrl: 'https://example.com/b.jpg', timestamp: 20 },
      { thumbnailUrl: 'https://example.com/c.jpg', timestamp: 30 },
    ],
  },
};

const overviewTab: TabEntry = {
  id: 'overview',
  label: 'Overview',
  emoji: '🧭',
  component: 'overview',
  props: { brief: 'A briefing' },
};

function renderRouter(tabs: TabEntry[]) {
  return render(
    <OutputRouter
      title="Test video"
      videoSummaryId="vid-1"
      tabs={tabs}
      meta={null}
      synthesis={null}
    />,
  );
}

describe('OutputRouter tab filtering', () => {
  it('drops the filmstrip tab when a moment_track has rich frame coverage (≥8 thumbnails)', () => {
    renderRouter([momentTab(makeMomentItems(10, 8)), filmstripTab]);
    expect(screen.getAllByText('Moments').length).toBeGreaterThan(0);
    expect(screen.queryByText('Filmstrip')).toBeNull();
  });

  it('keeps the filmstrip tab when the moment_track is frame-sparse', () => {
    renderRouter([momentTab(makeMomentItems(10, 3)), filmstripTab]);
    expect(screen.getAllByText('Filmstrip').length).toBeGreaterThan(0);
  });

  it('keeps the filmstrip tab when there is no moment_track at all', () => {
    renderRouter([filmstripTab]);
    expect(screen.getAllByText('Filmstrip').length).toBeGreaterThan(0);
  });

  it('still filters the overview tab from the strip', () => {
    renderRouter([overviewTab, momentTab(makeMomentItems(4, 0))]);
    expect(screen.getAllByText('Moments').length).toBeGreaterThan(0);
    expect(screen.queryByText('Overview')).toBeNull();
  });
});

describe('OutputRouter while tabs stream in', () => {
  const provisionalMoments: StreamTab = {
    ...momentTab(makeMomentItems(4, 0)),
    position: 3,
    provisional: true,
  };
  const stepsTab: StreamTab = {
    id: 'steps',
    label: 'Steps',
    emoji: '👣',
    component: 'display_section',
    props: { data: {} },
    position: 1,
  };
  const plannedLabels = [
    { id: 'steps', label: 'Steps', emoji: '👣' },
    { id: 'moments', label: 'Moments', emoji: '🎬' },
    { id: 'quiz', label: 'Quiz', emoji: '🧪' },
  ];

  function streamingRouter(
    tabs: StreamTab[] | null,
    extra: { tabsSettled?: boolean; onCancelStream?: () => void } = {},
  ) {
    return (
      <OutputRouter
        title="Test video"
        videoSummaryId="vid-1"
        tabs={tabs}
        meta={null}
        synthesis={null}
        isStreaming
        streamPhase="extraction"
        tabCount={3}
        tabLabels={plannedLabels}
        onCancelStream={() => {}}
        {...extra}
      />
    );
  }

  it('should keep Cancel visible once the first tab has landed', () => {
    render(streamingRouter([provisionalMoments]));

    expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument();
  });

  it('should keep the stage timeline visible once the first tab has landed', () => {
    render(streamingRouter([provisionalMoments]));

    expect(screen.getByRole('list', { name: 'Processing stages' })).toBeInTheDocument();
  });

  it('should show the planned tabs that have not landed as pending pills', () => {
    render(streamingRouter([provisionalMoments]));

    expect(screen.getByText('Quiz')).toBeInTheDocument();
  });

  it('should drop the pending pills once the final tab set is known', () => {
    render(streamingRouter([provisionalMoments], { tabsSettled: true }));

    expect(screen.queryByText('Quiz')).toBeNull();
  });

  it('should cancel the stream when Cancel is clicked while tabs exist', async () => {
    const user = userEvent.setup();
    const onCancelStream = vi.fn();
    render(streamingRouter([provisionalMoments], { onCancelStream }));

    await user.click(screen.getByRole('button', { name: 'Cancel' }));

    expect(onCancelStream).toHaveBeenCalledTimes(1);
  });

  it('should count the planned tabs still to come in the early-preview notice', () => {
    render(streamingRouter([provisionalMoments]));

    expect(screen.getByText(/2 more tabs are on the way/)).toBeInTheDocument();
  });

  it('should mark a provisional tab with the early-preview notice', () => {
    render(streamingRouter([provisionalMoments]));

    expect(screen.getByText('Early preview')).toBeInTheDocument();
  });

  it('should label a provisional tab as an early preview in the tab strip', () => {
    render(streamingRouter([provisionalMoments]));

    expect(screen.getByRole('tab', { name: /moments \(early preview\)/i })).toBeInTheDocument();
  });

  it('should not remount the hero when the first tab lands', () => {
    const { rerender } = render(streamingRouter(null));
    const heading = screen.getByRole('heading', { name: 'Test video' });

    rerender(streamingRouter([provisionalMoments]));

    expect(screen.getByRole('heading', { name: 'Test video' })).toBe(heading);
  });

  it('should keep focus on the first tab when a tab with an earlier position arrives', () => {
    const { rerender } = render(streamingRouter([provisionalMoments]));

    rerender(streamingRouter([stepsTab, provisionalMoments]));

    expect(screen.getByRole('tab', { selected: true })).toHaveAccessibleName(/moments/i);
  });
});

