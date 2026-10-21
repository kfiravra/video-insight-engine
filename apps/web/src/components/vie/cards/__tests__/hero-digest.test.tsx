import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { HeroDigest } from '../HeroDigest';
import { VideoPlayerProvider } from '@/features/video-output/contexts/VideoPlayerContext';

const TAKEAWAYS = ['Agents write the code now. [1:00]', 'Review is the job. [20:45]'];

interface DigestOptions {
  showTakeaways?: boolean;
  showWorth?: boolean;
}

function renderDigest({ showTakeaways = true, showWorth = true }: DigestOptions = {}): HTMLElement {
  const { container } = render(
    <VideoPlayerProvider>
      <HeroDigest
        keyTakeaways={TAKEAWAYS}
        worthItFor="Leads deciding how far to let agents in."
        skipIf="You want a how-to: no code is written on stage."
        showTakeaways={showTakeaways}
        showWorth={showWorth}
        isStreaming={false}
        isPhone={false}
      />
    </VideoPlayerProvider>,
  );
  return container;
}

describe('HeroDigest', () => {
  it('should render nothing when neither the takeaways nor the worth-it lines show', () => {
    const container = renderDigest({ showTakeaways: false, showWorth: false });

    expect(container).toBeEmptyDOMElement();
  });

  it('should render the takeaways with their seek chips when they show', () => {
    renderDigest({ showWorth: false });

    expect(screen.getByRole('button', { name: 'Jump to 20:45' })).toBeInTheDocument();
  });

  it('should render the worth-it lines when only they show', () => {
    renderDigest({ showTakeaways: false });

    expect(screen.getByText('Leads deciding how far to let agents in.')).toBeInTheDocument();
  });

  it('should leave out the worth-it lines when they do not show', () => {
    renderDigest({ showWorth: false });

    expect(screen.queryByText('Leads deciding how far to let agents in.')).not.toBeInTheDocument();
  });
});
