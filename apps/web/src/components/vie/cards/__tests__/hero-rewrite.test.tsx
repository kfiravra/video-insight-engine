import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, afterEach } from 'vitest';
import type { ComponentProps, ReactNode } from 'react';
import { VideoHero } from '../VideoHero';
import { VideoPlayerProvider } from '@/features/video-output/contexts/VideoPlayerContext';
import { DirectionProvider } from '@/contexts/DirectionContext';

// The rewritten hero (output-quality 2.2 → 2.3): S01's hour-long talk.
const NEW_HERO: ComponentProps<typeof VideoHero> = {
  title: 'Rails World 2026 Opening Keynote',
  creator: 'Ruby on Rails',
  duration: 3787,
  youtubeId: 'vDjW_dRyKXY',
  tldr: 'November 24, 2025 was software’s Kodak Brownie moment.',
  brief: 'DHH opens with his great-great-grandfather, a portrait painter.',
  masterSummary: 'An older synthesis paragraph the rewrite replaces.',
  keyTakeaways: [
    '37signals has gone pencils down on hand-written code [20:45]',
    'Nobody can predict this economy, so the only play is total optimism [1:00:25]',
    'English is a better programming language than Ruby',
    'Abstractions are choke points when agents mutate code [43:03]',
    'Every app should ship a CLI [45:15]',
  ],
  worthItFor: 'Engineering leads deciding how far to let agents write code.',
  skipIf: 'You want a how-to: no code is written on stage.',
  readingMinutes: 5,
};

function renderHero(props: Partial<ComponentProps<typeof VideoHero>> = {}, wrap?: (ui: ReactNode) => ReactNode) {
  const ui = <VideoHero {...NEW_HERO} {...props} />;
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

describe('VideoHero — rewritten hero', () => {
  describe('takeaway seek chips', () => {
    it('should render an m:ss marker as a seek chip when a takeaway closes with one', () => {
      renderHero();

      expect(screen.getByRole('button', { name: 'Jump to 20:45' })).toBeInTheDocument();
    });

    it('should render an h:mm:ss marker as a seek chip when the moment is past the hour', () => {
      renderHero();

      expect(screen.getByRole('button', { name: 'Jump to 1:00:25' })).toBeInTheDocument();
    });

    it('should never show the marker as text when it becomes a chip', () => {
      renderHero();

      expect(screen.queryByText(/\[20:45\]/)).not.toBeInTheDocument();
    });

    it('should render the claim without a chip when a takeaway has no marker', () => {
      renderHero({ keyTakeaways: ['English is a better programming language than Ruby'] });

      expect(screen.queryByRole('button', { name: /^Jump to/ })).not.toBeInTheDocument();
    });

    it('should open the player when a takeaway chip is clicked', async () => {
      const user = userEvent.setup();
      renderHero();

      await user.click(screen.getByRole('button', { name: 'Jump to 20:45' }));

      expect(screen.getByRole('button', { name: 'Hide' })).toHaveAttribute('aria-expanded', 'true');
    });

    it('should keep the takeaways open when the seek came from a takeaway chip', async () => {
      const user = userEvent.setup();
      renderHero();

      await user.click(screen.getByRole('button', { name: 'Jump to 20:45' }));

      expect(screen.getByRole('button', { name: /key takeaways/i })).toHaveAttribute('aria-expanded', 'true');
    });

    it('should render old plain takeaways unchanged when none carry a marker', () => {
      renderHero({ keyTakeaways: ['Takeaway one', 'Takeaway two'] });

      expect(screen.getByText('Takeaway one')).toBeInTheDocument();
    });
  });

  describe('brief', () => {
    it('should render the rewritten brief when meta carries one', () => {
      renderHero();

      expect(screen.getByText(NEW_HERO.brief as string)).toBeInTheDocument();
    });

    it('should not render masterSummary when the rewritten brief is present', () => {
      renderHero();

      expect(screen.queryByText(NEW_HERO.masterSummary as string)).not.toBeInTheDocument();
    });

    it('should fall back to masterSummary when an older row has no brief', () => {
      renderHero({ brief: undefined });

      expect(screen.getByText(NEW_HERO.masterSummary as string)).toBeInTheDocument();
    });

    it('should fall back to masterSummary when the brief is blank', () => {
      renderHero({ brief: '   ' });

      expect(screen.getByText(NEW_HERO.masterSummary as string)).toBeInTheDocument();
    });
  });

  describe('worth it for / skip if', () => {
    it('should show the worth-it-for line under its label', () => {
      renderHero();

      expect(screen.getByText('Worth it for')).toBeInTheDocument();
      expect(screen.getByText(NEW_HERO.worthItFor as string)).toBeInTheDocument();
    });

    it('should show the skip-if line under its label', () => {
      renderHero();

      expect(screen.getByText('Skip if')).toBeInTheDocument();
      expect(screen.getByText(NEW_HERO.skipIf as string)).toBeInTheDocument();
    });

    it('should hide both lines when the row carries neither', () => {
      renderHero({ worthItFor: undefined, skipIf: undefined });

      expect(screen.queryByText('Worth it for')).not.toBeInTheDocument();
      expect(screen.queryByText('Skip if')).not.toBeInTheDocument();
    });

    it('should show only the line that was written when the other is empty', () => {
      renderHero({ skipIf: '' });

      expect(screen.queryByText('Skip if')).not.toBeInTheDocument();
      expect(screen.getByText('Worth it for')).toBeInTheDocument();
    });
  });

  describe('reading time', () => {
    it('should show the reading time when the page carries one', () => {
      renderHero();

      expect(screen.getByText('Read: 5 min')).toBeInTheDocument();
    });

    it('should show the watch time in minutes beside it', () => {
      renderHero();

      expect(screen.getByText('Watch: 63 min')).toBeInTheDocument();
    });

    it('should replace the bare duration with the read / watch pair', () => {
      renderHero();

      expect(screen.queryByText('1:03:07')).not.toBeInTheDocument();
    });

    it('should hide the reading line and keep the duration when the page has no reading time', () => {
      renderHero({ readingMinutes: undefined });

      expect(screen.queryByText(/^Read:/)).not.toBeInTheDocument();
      expect(screen.getByText('1:03:07')).toBeInTheDocument();
    });

    it('should show only the reading time when the video length is unknown', () => {
      renderHero({ duration: null });

      expect(screen.getByText('Read: 5 min')).toBeInTheDocument();
      expect(screen.queryByText(/^Watch:/)).not.toBeInTheDocument();
    });
  });

  describe('phone', () => {
    it('should keep the brief collapsed on phones', () => {
      mockPhoneViewport();
      renderHero();

      expect(screen.getByRole('button', { name: /^brief$/i })).toHaveAttribute('aria-expanded', 'false');
    });

    it('should fold the worth-it lines into a third collapsed row when on a phone', () => {
      mockPhoneViewport();
      renderHero();

      expect(screen.getByRole('button', { name: 'Worth it?' })).toHaveAttribute('aria-expanded', 'false');
    });

    it('should keep the worth-it lines out of reach until their row is opened when on a phone', () => {
      mockPhoneViewport();
      renderHero();

      expect(screen.getByText(NEW_HERO.worthItFor as string).closest('[inert]')).not.toBeNull();
    });

    it('should keep the worth-it lines open with no extra row when on desktop', () => {
      renderHero();

      expect(screen.getByText(NEW_HERO.worthItFor as string).closest('[inert]')).toBeNull();
      expect(screen.queryByRole('button', { name: 'Worth it?' })).not.toBeInTheDocument();
    });

    it('should show three takeaways on phones with a "+N" control for the rest', async () => {
      mockPhoneViewport();
      const user = userEvent.setup();
      renderHero();

      await user.click(screen.getByRole('button', { name: /key takeaways/i }));

      expect(screen.getAllByRole('button', { name: /^Jump to/ })).toHaveLength(2);
      expect(screen.getByRole('button', { name: /^\+2 · More takeaways$/ })).toBeInTheDocument();
    });
  });

  describe('right-to-left', () => {
    const hebrew = (ui: ReactNode) => (
      <DirectionProvider language="he" isRTL>
        {ui}
      </DirectionProvider>
    );

    it('should label the worth-it lines in Hebrew when the output is Hebrew', () => {
      renderHero({}, hebrew);

      expect(screen.getByText('שווה צפייה עבור')).toBeInTheDocument();
      expect(screen.getByText('דלגו אם')).toBeInTheDocument();
    });

    it('should render the reading line in Hebrew when the output is Hebrew', () => {
      renderHero({}, hebrew);

      expect(screen.getByText('קריאה: 5 דק׳')).toBeInTheDocument();
      expect(screen.getByText('צפייה: 63 דק׳')).toBeInTheDocument();
    });

    it('should keep the seek chip left-to-right inside a right-to-left hero', () => {
      renderHero({}, hebrew);

      expect(screen.getByRole('button', { name: 'מעבר אל 20:45' })).toHaveAttribute('dir', 'ltr');
    });

    it('should name every takeaway chip in Hebrew when the output is Hebrew', () => {
      renderHero({}, hebrew);

      expect(screen.queryByRole('button', { name: /^Jump to/ })).not.toBeInTheDocument();
    });
  });
});
