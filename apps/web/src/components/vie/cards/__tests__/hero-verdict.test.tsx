import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, afterEach } from 'vitest';
import type { ComponentProps, ReactNode } from 'react';
import { VideoHero } from '../VideoHero';
import { VideoPlayerProvider } from '@/features/video-output/contexts/VideoPlayerContext';
import { DirectionProvider } from '@/contexts/DirectionContext';
import type { HeroVerdictData } from '@/features/video-output/lib/hero-verdict';

// S07 (AirPods Pro 2 review) as the router lifts it from the stored doc.
const VERDICT: HeroVerdictData = {
  badge: 'recommended',
  bottomLine: 'AirPods Pro 2 are really, really good — the H2 chip is the underrated upgrade.',
  bestFor: ['"AirPods people" who wear AirPods everywhere', 'Anyone who wants better sound at the same price'],
  notFor: [],
  timestamp: 641,
  quote: "It's the better battery life. It's the better sound quality.",
};

const SUB_SCORES = [
  { category: 'Battery', score: 9 },
  { category: 'Camera', score: 7 },
];

const REVIEW_HERO: ComponentProps<typeof VideoHero> = {
  title: 'AirPods Pro 2 Review: 1 Underrated Thing!',
  creator: 'Marques Brownlee',
  duration: 677,
  youtubeId: 'xyz12345678',
  primaryTag: 'review',
  tldr: "AirPods Pro 2's best upgrade is the H2 chip.",
  keyTakeaways: ['The H2 chip is the underrated upgrade. [4:00]'],
  worthItFor: 'People deciding whether the new AirPods are worth it.',
  skipIf: 'You want lab-measured battery or sound figures.',
  verdict: VERDICT,
};

function renderHero(props: Partial<ComponentProps<typeof VideoHero>> = {}, wrap?: (ui: ReactNode) => ReactNode) {
  const ui = <VideoHero {...REVIEW_HERO} {...props} />;
  return render(<VideoPlayerProvider>{wrap ? wrap(ui) : ui}</VideoPlayerProvider>);
}

const originalMatchMedia = window.matchMedia;

/** Make the phone media query match for one test (the setup mock answers
 *  false to everything except prefers-reduced-motion). */
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

describe('VideoHero — review verdict', () => {
  describe('desktop', () => {
    it('should open the verdict row with its badge when the review states one', () => {
      renderHero();

      expect(screen.getByRole('button', { name: /^Verdict\s*recommended$/i })).toHaveAttribute('aria-expanded', 'true');
    });

    it('should show the bottom line when the verdict row is open', () => {
      renderHero();

      expect(screen.getByText(VERDICT.bottomLine).closest('[inert]')).toBeNull();
    });

    it("should show the reviewer's words when the verdict carries a quote", () => {
      renderHero();

      expect(screen.getByText(VERDICT.quote as string)).toBeInTheDocument();
    });

    it("should open the player when the verdict's timestamp chip is clicked", async () => {
      const user = userEvent.setup();
      renderHero();

      await user.click(screen.getByRole('button', { name: 'Jump to 10:41' }));

      expect(screen.getByRole('button', { name: 'Hide' })).toHaveAttribute('aria-expanded', 'true');
    });

    it('should render no score when the verdict carries none', () => {
      renderHero();

      expect(screen.queryByRole('img', { name: /^Score:/ })).not.toBeInTheDocument();
    });

    it('should render the score when the reviewer gives one at the verdict moment', () => {
      renderHero({ verdict: { ...VERDICT, score: 8, maxScore: 10 } });

      expect(screen.getByRole('img', { name: /^Score: 8\.0/ })).toBeInTheDocument();
    });

    it('should show a stated score when the verdict sentence has no timestamp', () => {
      renderHero({ verdict: { ...VERDICT, timestamp: undefined, score: 8, maxScore: 10 } });

      expect(screen.getByRole('img', { name: /^Score: 8\.0/ })).toBeInTheDocument();
    });

    it('should show stated sub-scores in the hero when the reviewer scores categories', () => {
      renderHero({ verdict: { ...VERDICT, score: 8, maxScore: 10, subScores: SUB_SCORES } });

      const list = screen.getByRole('list', { name: 'Scores by category' });
      expect(within(list).getAllByRole('listitem')).toHaveLength(2);
    });

    it('should show each sub-score against the stated scale when the reviewer gives one', () => {
      renderHero({ verdict: { ...VERDICT, score: 8, maxScore: 10, subScores: SUB_SCORES } });

      expect(screen.getByText('9/10')).toBeInTheDocument();
    });

    it('should show a bare sub-score when the reviewer states no scale', () => {
      renderHero({ verdict: { ...VERDICT, subScores: SUB_SCORES } });

      const list = screen.getByRole('list', { name: 'Scores by category' });
      expect(within(list).getByText('9')).toBeInTheDocument();
    });

    it('should render no sub-score list when the verdict carries none', () => {
      renderHero();

      expect(screen.queryByRole('list', { name: 'Scores by category' })).not.toBeInTheDocument();
    });

    it('should replace Worth it for with Best for when the reviewer names who it is for', () => {
      renderHero();

      expect(screen.getByText('Best for')).toBeInTheDocument();
      expect(screen.queryByText('Worth it for')).not.toBeInTheDocument();
    });

    it('should list every Best for entry when the reviewer names several', () => {
      renderHero();

      expect(screen.getByText(VERDICT.bestFor[1])).toBeInTheDocument();
    });

    it('should keep the Skip if line when the reviewer names no Not for', () => {
      renderHero();

      expect(screen.getByText(REVIEW_HERO.skipIf as string)).toBeInTheDocument();
    });

    it('should replace Skip if with Not for when the reviewer names who should pass', () => {
      renderHero({ verdict: { ...VERDICT, notFor: ['Long-haul fliers who need over-ear isolation'] } });

      expect(screen.getByText('Not for')).toBeInTheDocument();
      expect(screen.queryByText('Skip if')).not.toBeInTheDocument();
    });

    it('should fall back to Worth it for when the reviewer names no Best for', () => {
      renderHero({ verdict: { ...VERDICT, bestFor: [] } });

      expect(screen.getByText(REVIEW_HERO.worthItFor as string)).toBeInTheDocument();
    });

    it('should render the verdict row without a badge when the reviewer gives no call', () => {
      renderHero({ verdict: { ...VERDICT, badge: undefined } });

      expect(screen.getByRole('button', { name: /^Verdict$/i })).toBeInTheDocument();
    });

    it('should keep the plain worth-it lines when the page carries no verdict', () => {
      renderHero({ verdict: undefined });

      expect(screen.getByText('Worth it for')).toBeInTheDocument();
    });
  });

  describe('phone', () => {
    it('should collapse the verdict row when on a phone', () => {
      mockPhoneViewport();
      renderHero();

      expect(screen.getByRole('button', { name: /^Verdict\s*recommended$/i })).toHaveAttribute('aria-expanded', 'false');
    });

    it('should show no separate "Worth it?" row when the verdict row holds the lines on a phone', () => {
      mockPhoneViewport();
      renderHero();

      expect(screen.queryByRole('button', { name: 'Worth it?' })).not.toBeInTheDocument();
    });

    it('should reveal the bottom line when the verdict row is opened on a phone', async () => {
      mockPhoneViewport();
      const user = userEvent.setup();
      renderHero();

      await user.click(screen.getByRole('button', { name: /^Verdict\s*recommended$/i }));

      expect(screen.getByText(VERDICT.bottomLine).closest('[inert]')).toBeNull();
    });
  });

  describe('right-to-left', () => {
    it('should label the verdict row and the lists in Hebrew when the output is Hebrew', () => {
      renderHero({}, (ui) => (
        <DirectionProvider language="he" isRTL>
          {ui}
        </DirectionProvider>
      ));

      expect(screen.getByText('פסק הדין')).toBeInTheDocument();
      expect(screen.getByText('מתאים במיוחד ל')).toBeInTheDocument();
    });

    it('should name the sub-score list in Hebrew when the output is Hebrew', () => {
      renderHero({ verdict: { ...VERDICT, subScores: SUB_SCORES } }, (ui) => (
        <DirectionProvider language="he" isRTL>
          {ui}
        </DirectionProvider>
      ));

      expect(screen.getByRole('list', { name: 'ציונים לפי קטגוריה' })).toBeInTheDocument();
    });
  });
});
