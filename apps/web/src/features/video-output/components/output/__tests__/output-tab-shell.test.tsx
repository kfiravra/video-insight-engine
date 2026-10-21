import { render, screen } from '@testing-library/react';
import { describe, it, expect, beforeEach } from 'vitest';
import type { SynthesisResult } from '@vie/types';
import { VideoPlayerProvider } from '@/features/video-output/contexts/VideoPlayerContext';
import { CommandDeck } from '../OutputTabShell';
import { TabCoordinationProvider } from '../TabCoordinationContext';

// The stream's corrected synthesis_complete superset once the hero rewrite ran (2.2).
const HERO_SYNTHESIS: SynthesisResult = {
  tldr: 'November 24, 2025 was software’s Kodak Brownie moment.',
  keyTakeaways: ['37signals has gone pencils down on hand-written code [20:45]'],
  masterSummary: 'DHH opens with his great-great-grandfather.',
  seoDescription: 'DHH on agents.',
  brief: 'DHH opens with his great-great-grandfather.',
  worthItFor: 'Engineering leads deciding how far to let agents in.',
  skipIf: 'You want a how-to: no code is written on stage.',
  readingMinutes: 5,
};

function renderDeck(synthesis: SynthesisResult): void {
  render(
    <VideoPlayerProvider>
      <TabCoordinationProvider videoId="v1" initialTab="ideas">
        <CommandDeck
          title="Rails World 2026 Opening Keynote"
          duration={3787}
          synthesis={synthesis}
          tabDefs={[{ id: 'ideas', label: 'Key Ideas', emoji: '💡', dataSource: '' }]}
          pendingTabs={[]}
          domainGradient=""
          primaryTag="learning"
          isStreaming={false}
        />
      </TabCoordinationProvider>
    </VideoPlayerProvider>,
  );
}

describe('CommandDeck', () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  it('should hand the rewritten brief to the hero', () => {
    renderDeck(HERO_SYNTHESIS);

    expect(screen.getByText(HERO_SYNTHESIS.brief as string)).toBeInTheDocument();
  });

  it('should hand the worth-it-for and skip-if lines to the hero', () => {
    renderDeck(HERO_SYNTHESIS);

    expect(screen.getByText(HERO_SYNTHESIS.worthItFor as string)).toBeInTheDocument();
    expect(screen.getByText(HERO_SYNTHESIS.skipIf as string)).toBeInTheDocument();
  });

  it('should hand the reading time to the hero', () => {
    renderDeck(HERO_SYNTHESIS);

    expect(screen.getByText('Read: 5 min')).toBeInTheDocument();
  });

  it('should render an older synthesis without the hero rewrite lines', () => {
    renderDeck({
      tldr: HERO_SYNTHESIS.tldr,
      keyTakeaways: HERO_SYNTHESIS.keyTakeaways,
      masterSummary: 'An older synthesis paragraph.',
      seoDescription: HERO_SYNTHESIS.seoDescription,
    });

    expect(screen.getByText('An older synthesis paragraph.')).toBeInTheDocument();
    expect(screen.queryByText('Worth it for')).not.toBeInTheDocument();
  });
});
