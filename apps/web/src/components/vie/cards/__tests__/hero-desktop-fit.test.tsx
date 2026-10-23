import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, afterEach } from 'vitest';
import type { ComponentProps, ReactNode } from 'react';
import { VideoHero } from '../VideoHero';
import { VideoPlayerProvider } from '@/features/video-output/contexts/VideoPlayerContext';
import { DirectionProvider } from '@/contexts/DirectionContext';

// The desktop hero fits the first screen at 1280 × 800 (output-quality 4.6a):
// the brief in two lines + "more", three takeaways + "+N", one line per
// worth-it line. Collapsed text stays readable to screen readers.
const BRIEF = Array.from(
  { length: 6 },
  (_, i) => `Sentence ${i + 1} of the brief walks through what the speaker argues and why it matters.`,
).join(' ');
const TAKEAWAYS = Array.from({ length: 7 }, (_, i) => `Takeaway number ${i + 1} [${i + 1}:00]`);
const WORTH = 'Engineering leads deciding how far to let agents write the code they ship next quarter.';

const HERO: ComponentProps<typeof VideoHero> = {
  title: 'Rails World 2026 Opening Keynote',
  creator: 'Ruby on Rails',
  duration: 3787,
  youtubeId: 'vDjW_dRyKXY',
  tldr: 'Agents ended hand-written code; respond with optimism.',
  brief: BRIEF,
  keyTakeaways: TAKEAWAYS,
  worthItFor: WORTH,
  skipIf: 'You want a how-to: no code is written on stage.',
};

function renderHero(props: Partial<ComponentProps<typeof VideoHero>> = {}, wrap?: (ui: ReactNode) => ReactNode) {
  const ui = <VideoHero {...HERO} {...props} />;
  return render(<VideoPlayerProvider>{wrap ? wrap(ui) : ui}</VideoPlayerProvider>);
}

const hebrew = (ui: ReactNode) => (
  <DirectionProvider language="he" isRTL>
    {ui}
  </DirectionProvider>
);

const originalMatchMedia = window.matchMedia;

function mockPhoneViewport(): void {
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: /max-width: 767px/.test(query) || /prefers-reduced-motion/.test(query),
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }));
}

afterEach(() => {
  window.matchMedia = originalMatchMedia;
});

describe('VideoHero — desktop first-screen fit', () => {
  describe('brief', () => {
    it('should open with a "more" button reporting the brief as folded when on desktop', () => {
      renderHero();

      expect(screen.getByRole('button', { name: 'Read more of the brief' })).toHaveAttribute('aria-expanded', 'false');
    });

    it('should keep the folded brief whole for screen readers when on desktop', () => {
      renderHero();

      const region = screen.getByRole('region', { name: 'Brief' });

      expect(region).toHaveTextContent('Sentence 6 of the brief');
      expect(region.closest('[aria-hidden="true"], [inert]')).toBeNull();
    });

    it('should keep focus on the toggle when the brief is unrolled', async () => {
      const user = userEvent.setup();
      renderHero();
      const toggle = screen.getByRole('button', { name: 'Read more of the brief' });

      await user.click(toggle);

      expect(screen.getByRole('button', { name: 'Hide brief' })).toHaveFocus();
    });

    it('should show a short brief whole with no "more" when it fits in two lines', () => {
      renderHero({ brief: 'DHH opens with portrait painters.' });

      expect(screen.queryByRole('button', { name: 'Read more of the brief' })).not.toBeInTheDocument();
    });

    it('should render no collapsed "Brief" row when on desktop', () => {
      renderHero();

      expect(screen.queryByRole('button', { name: 'Brief' })).not.toBeInTheDocument();
    });

    it('should count the key question against the two-line budget when on desktop', () => {
      renderHero({ brief: 'B'.repeat(100), keyQuestion: `${'Q'.repeat(150)}?` });

      expect(screen.getByRole('button', { name: 'Read more of the brief' })).toBeInTheDocument();
    });

    it('should name the "more" button in Hebrew when the output is Hebrew', () => {
      renderHero({}, hebrew);

      expect(screen.getByRole('button', { name: 'עוד מהתקציר' })).toBeInTheDocument();
    });

    it('should keep the collapsed "Brief" row when on a phone', () => {
      mockPhoneViewport();
      renderHero();

      expect(screen.getByRole('button', { name: 'Brief' })).toHaveAttribute('aria-expanded', 'false');
    });
  });

  describe('takeaways', () => {
    it('should show three takeaways when on desktop', () => {
      renderHero();

      expect(screen.getAllByRole('button', { name: /^Jump to/ })).toHaveLength(3);
    });

    it('should keep "+N" reachable when the takeaways row is collapsed on desktop', async () => {
      const user = userEvent.setup();
      renderHero();

      await user.click(screen.getByRole('button', { name: /key takeaways/i }));

      expect(screen.getByRole('button', { name: /^\+4 · More takeaways$/ })).toBeInTheDocument();
    });

    it('should open a collapsed takeaways row when "+N" is clicked on desktop', async () => {
      const user = userEvent.setup();
      renderHero();
      const row = screen.getByRole('button', { name: /key takeaways/i });
      await user.click(row);

      await user.click(screen.getByRole('button', { name: /^\+4 · More takeaways$/ }));

      expect(row).toHaveAttribute('aria-expanded', 'true');
    });

    it('should show every takeaway once "+N" is clicked on desktop', async () => {
      const user = userEvent.setup();
      renderHero();

      await user.click(screen.getByRole('button', { name: /^\+4 · More takeaways$/ }));

      expect(screen.getAllByRole('button', { name: /^Jump to/ })).toHaveLength(7);
    });
  });

  describe('worth-it lines', () => {
    it('should keep the whole worth-it line in the page when it is cut to one line on desktop', () => {
      renderHero();

      expect(screen.getByText(WORTH)).toHaveAttribute('title', WORTH);
    });

    it('should read a review list as one line when on desktop', () => {
      renderHero({
        primaryTag: 'review',
        verdict: { badge: 'recommended', bottomLine: 'Worth the upgrade.', bestFor: ['Commuters', 'Runners'], notFor: [] },
      });

      expect(screen.getByText('Commuters · Runners')).toBeInTheDocument();
    });

    it('should keep the full stacked lines inside the "Worth it?" row when on a phone', async () => {
      mockPhoneViewport();
      const user = userEvent.setup();
      renderHero();

      await user.click(screen.getByRole('button', { name: 'Worth it?' }));

      expect(screen.getByText(WORTH)).not.toHaveAttribute('title');
    });
  });
});
