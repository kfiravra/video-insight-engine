import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect } from 'vitest';
import type { ReactNode } from 'react';
import { HeroDigest } from '../HeroDigest';
import { VideoPlayerProvider } from '@/features/video-output/contexts/VideoPlayerContext';
import { DirectionProvider } from '@/contexts/DirectionContext';

const TAKEAWAYS = ['Agents write the code now. [1:00]', 'Review is the job. [20:45]'];
const WORTH = 'Leads deciding how far to let agents in.';
const SKIP = 'You want a how-to: no code is written on stage.';

interface DigestOptions {
  showTakeaways?: boolean;
  showWorth?: boolean;
  isPhone?: boolean;
  wrap?: (ui: ReactNode) => ReactNode;
}

function renderDigest({ showTakeaways = true, showWorth = true, isPhone = false, wrap }: DigestOptions = {}): HTMLElement {
  const digest = (
    <HeroDigest
      keyTakeaways={TAKEAWAYS}
      worthItFor={WORTH}
      skipIf={SKIP}
      showTakeaways={showTakeaways}
      showWorth={showWorth}
      isStreaming={false}
      isPhone={isPhone}
    />
  );
  const { container } = render(<VideoPlayerProvider>{wrap ? wrap(digest) : digest}</VideoPlayerProvider>);
  return container;
}

const hebrew = (ui: ReactNode) => (
  <DirectionProvider language="he" isRTL>
    {ui}
  </DirectionProvider>
);

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

    expect(screen.getByText(WORTH)).toBeInTheDocument();
  });

  it('should leave out the worth-it lines when they do not show', () => {
    renderDigest({ showWorth: false });

    expect(screen.queryByText(WORTH)).not.toBeInTheDocument();
  });

  describe('desktop', () => {
    it('should keep the worth-it lines open when on desktop', () => {
      renderDigest();

      expect(screen.getByText(WORTH).closest('[inert]')).toBeNull();
    });

    it('should render no "Worth it?" row when on desktop', () => {
      renderDigest();

      expect(screen.queryByRole('button', { name: 'Worth it?' })).not.toBeInTheDocument();
    });
  });

  describe('phone', () => {
    it('should fold the worth-it lines into a collapsed "Worth it?" row when on a phone', () => {
      renderDigest({ isPhone: true });

      expect(screen.getByRole('button', { name: 'Worth it?' })).toHaveAttribute('aria-expanded', 'false');
    });

    it('should keep both lines out of the focus order and the accessibility tree when the row is collapsed', () => {
      renderDigest({ isPhone: true });

      expect(screen.getByText(SKIP).closest('[inert]')).not.toBeNull();
    });

    it('should place the "Worth it?" row after the key takeaways row when on a phone', () => {
      renderDigest({ isPhone: true });

      const takeaways = screen.getByRole('button', { name: /key takeaways/i });
      const worth = screen.getByRole('button', { name: 'Worth it?' });

      expect(takeaways.compareDocumentPosition(worth) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    });

    it('should show both lines when the "Worth it?" row is opened', async () => {
      const user = userEvent.setup();
      renderDigest({ isPhone: true });

      await user.click(screen.getByRole('button', { name: 'Worth it?' }));

      expect(screen.getByText(WORTH).closest('[inert]')).toBeNull();
    });

    it('should collapse the row again when it is tapped a second time', async () => {
      const user = userEvent.setup();
      renderDigest({ isPhone: true });
      const row = screen.getByRole('button', { name: 'Worth it?' });

      await user.click(row);
      await user.click(row);

      expect(row).toHaveAttribute('aria-expanded', 'false');
    });

    it('should render the "Worth it?" row alone when a phone page has no takeaways', () => {
      renderDigest({ isPhone: true, showTakeaways: false });

      expect(screen.getByRole('button', { name: 'Worth it?' })).toBeInTheDocument();
    });

    it('should label the row in Hebrew when the output is Hebrew', () => {
      renderDigest({ isPhone: true, wrap: hebrew });

      expect(screen.getByRole('button', { name: 'שווה צפייה?' })).toBeInTheDocument();
    });
  });
});
