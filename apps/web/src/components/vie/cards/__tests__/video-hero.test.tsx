import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, afterEach } from 'vitest';
import type { ReactNode } from 'react';
import { VideoHero } from '../VideoHero';
import { VideoPlayerProvider, useVideoPlayer } from '@/features/video-output/contexts/VideoPlayerContext';

const DEFAULT_PROPS = {
  title: 'Test Video Title',
  creator: 'Test Channel',
  duration: 754,
  tldr: 'A short summary of the video content.',
  keyTakeaways: ['Takeaway one', 'Takeaway two', 'Takeaway three'],
  masterSummary: 'A comprehensive overview of the entire video.',
  youtubeId: 'dQw4w9WgXcQ',
};

const SAMPLE_TABS = [
  { id: 'overview', label: 'Overview', emoji: '📚', dataSource: '' },
  { id: 'key_points', label: 'Key Points', emoji: '🎯', dataSource: '' },
  { id: 'quizzes', label: 'Quizzes', emoji: '✅', dataSource: '' },
];

function renderWithPlayer(ui: ReactNode): ReturnType<typeof render> {
  return render(<VideoPlayerProvider>{ui}</VideoPlayerProvider>);
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

/** Test-only sibling that exposes seekTo so tests can simulate a timestamp
 *  click from elsewhere in the output (MomentTrack, VisualEvidence, …). */
function SeekProbe() {
  const { seekTo } = useVideoPlayer();
  return (
    <button type="button" onClick={() => seekTo(42)}>
      seek-probe
    </button>
  );
}

describe('VideoHero', () => {
  it('should render title, creator, and duration', () => {
    renderWithPlayer(<VideoHero {...DEFAULT_PROPS} />);

    expect(screen.getByText('Test Video Title')).toBeInTheDocument();
    expect(screen.getByText('Test Channel')).toBeInTheDocument();
    expect(screen.getByText('12:34')).toBeInTheDocument();
  });

  it('should render the TLDR inline (no expand step)', () => {
    renderWithPlayer(<VideoHero {...DEFAULT_PROPS} />);

    expect(screen.getByText(DEFAULT_PROPS.tldr)).toBeInTheDocument();
  });

  it('should render a Watch button when youtubeId is provided', () => {
    renderWithPlayer(<VideoHero {...DEFAULT_PROPS} />);

    expect(screen.getByRole('button', { name: /watch/i })).toBeInTheDocument();
  });

  it('should toggle Watch → Hide and flip aria-expanded when clicked', async () => {
    const user = userEvent.setup();
    renderWithPlayer(<VideoHero {...DEFAULT_PROPS} />);

    const watchBtn = screen.getByRole('button', { name: /watch/i });
    expect(watchBtn).toHaveAttribute('aria-expanded', 'false');

    await user.click(watchBtn);

    const hideBtn = screen.getByRole('button', { name: /hide/i });
    expect(hideBtn).toHaveAttribute('aria-expanded', 'true');
  });

  it('should render a tab bar when tabs are provided', () => {
    renderWithPlayer(
      <VideoHero {...DEFAULT_PROPS} tabs={SAMPLE_TABS} activeTabId="overview" onTabSelect={() => {}} />,
    );

    expect(screen.getByRole('tablist', { name: /video insight sections/i })).toBeInTheDocument();
    expect(screen.getAllByRole('tab')).toHaveLength(SAMPLE_TABS.length);
  });

  it('should not render a tab bar when no tabs are provided', () => {
    renderWithPlayer(<VideoHero {...DEFAULT_PROPS} />);

    expect(screen.queryByRole('tablist')).not.toBeInTheDocument();
    expect(screen.queryAllByRole('tab')).toHaveLength(0);
  });

  it('should handle missing optional props gracefully', () => {
    renderWithPlayer(<VideoHero title="Minimal Video" />);
    expect(screen.getByText('Minimal Video')).toBeInTheDocument();
  });

  it('should format hours correctly', () => {
    renderWithPlayer(<VideoHero title="Long Video" duration={7265} />);
    expect(screen.getByText('2:01:05')).toBeInTheDocument();
  });

  it('should not render the Watch button without youtubeId', () => {
    renderWithPlayer(<VideoHero {...DEFAULT_PROPS} youtubeId={undefined} />);

    expect(screen.queryByRole('button', { name: /watch/i })).not.toBeInTheDocument();
  });

  it('should show skeleton placeholders for brief and takeaways while streaming', () => {
    const { container } = renderWithPlayer(<VideoHero title="Loading test" />);

    expect(screen.getByText('Loading test')).toBeInTheDocument();
    // Any pulsing placeholder div is fine — the test asserts the streaming
    // state is visually represented, not the exact selector shape.
    expect(container.querySelector('.animate-pulse')).toBeInTheDocument();
  });

  it('should expose the title with an aria-labelledby section', () => {
    renderWithPlayer(<VideoHero {...DEFAULT_PROPS} />);

    const section = screen.getByRole('region', { name: /test video title/i });
    expect(section).toBeInTheDocument();
  });

  describe('Briefing Surface (Hero owns the Overview)', () => {
    it('should render the identity strip with domain label when primaryTag is provided', () => {
      renderWithPlayer(<VideoHero {...DEFAULT_PROPS} primaryTag="learning" />);
      // Domain label appears in the identity strip, joined by dots with the
      // other metadata fields.
      expect(screen.getByText('Learning')).toBeInTheDocument();
      expect(screen.getByText('12:34')).toBeInTheDocument();
      expect(screen.getByText('Test Channel')).toBeInTheDocument();
    });

    it('should render an optional level chip in the identity strip', () => {
      renderWithPlayer(<VideoHero {...DEFAULT_PROPS} primaryTag="tech" level="Advanced" />);
      expect(screen.getByText('Advanced')).toBeInTheDocument();
    });

    it('should render the tldr inline above the brief', () => {
      renderWithPlayer(<VideoHero {...DEFAULT_PROPS} />);
      expect(screen.getByText(DEFAULT_PROPS.tldr)).toBeInTheDocument();
    });

    it('should render masterSummary as a Brief section open on desktop', () => {
      renderWithPlayer(<VideoHero {...DEFAULT_PROPS} />);
      expect(screen.getByRole('button', { name: /^brief$/i })).toHaveAttribute('aria-expanded', 'true');
      expect(screen.getByText(DEFAULT_PROPS.masterSummary)).toBeInTheDocument();
    });

    it('should collapse the Brief by default on phones', () => {
      mockPhoneViewport();
      renderWithPlayer(<VideoHero {...DEFAULT_PROPS} />);
      expect(screen.getByRole('button', { name: /^brief$/i })).toHaveAttribute('aria-expanded', 'false');
    });

    it('should toggle the Brief when its disclosure is clicked', async () => {
      const user = userEvent.setup();
      renderWithPlayer(<VideoHero {...DEFAULT_PROPS} />);
      const briefBtn = screen.getByRole('button', { name: /^brief$/i });
      await user.click(briefBtn);
      expect(briefBtn).toHaveAttribute('aria-expanded', 'false');
      await user.click(briefBtn);
      expect(briefBtn).toHaveAttribute('aria-expanded', 'true');
    });

    it('should render the key question as the first line of the brief when present', () => {
      renderWithPlayer(
        <VideoHero
          {...DEFAULT_PROPS}
          keyQuestion="What does agent-written code mean for programmers?"
          masterSummary={'First paragraph.\n\nSecond paragraph.'}
        />,
      );
      const question = screen.getByText('What does agent-written code mean for programmers?');
      const firstParagraph = screen.getByText('First paragraph.');
      expect(question.compareDocumentPosition(firstParagraph) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
      expect(screen.getByText('Second paragraph.')).toBeInTheDocument();
    });

    it('should not render a Brief section when masterSummary is absent', () => {
      renderWithPlayer(<VideoHero {...DEFAULT_PROPS} masterSummary={undefined} />);
      expect(screen.queryByRole('button', { name: /^brief$/i })).not.toBeInTheDocument();
    });

    it('should render Key takeaways disclosure with a count when takeaways are present', () => {
      renderWithPlayer(<VideoHero {...DEFAULT_PROPS} />);
      // Label carries the count: "Key takeaways · 3".
      expect(screen.getByRole('button', { name: /key takeaways/i })).toHaveTextContent('3');
      // Children stay mounted even while collapsed (grid-rows morph).
      expect(screen.getByText('Takeaway one')).toBeInTheDocument();
      expect(screen.getByText('Takeaway two')).toBeInTheDocument();
      expect(screen.getByText('Takeaway three')).toBeInTheDocument();
    });

    it('should show six takeaways on desktop with a "+N" more control for the rest', () => {
      const tooMany = Array.from({ length: 9 }, (_, i) => `Takeaway ${i + 1}`);
      renderWithPlayer(<VideoHero {...DEFAULT_PROPS} keyTakeaways={tooMany} />);
      expect(screen.getByText('Takeaway 6')).toBeInTheDocument();
      expect(screen.queryByText('Takeaway 7')).not.toBeInTheDocument();
      expect(screen.getByRole('button', { name: /^\+3 · More takeaways$/ })).toBeInTheDocument();
    });

    it('should reveal all takeaways when "+N" is clicked', async () => {
      const user = userEvent.setup();
      const tooMany = Array.from({ length: 9 }, (_, i) => `Takeaway ${i + 1}`);
      renderWithPlayer(<VideoHero {...DEFAULT_PROPS} keyTakeaways={tooMany} />);
      await user.click(screen.getByRole('button', { name: /^\+3 · More takeaways$/ }));
      expect(screen.getByText('Takeaway 9')).toBeInTheDocument();
    });

    it('should collapse the takeaways by default on phones', () => {
      mockPhoneViewport();
      renderWithPlayer(<VideoHero {...DEFAULT_PROPS} />);
      expect(screen.getByRole('button', { name: /key takeaways/i })).toHaveAttribute('aria-expanded', 'false');
    });

    it('should show three takeaways on phones with a "+N" more control', () => {
      mockPhoneViewport();
      const five = Array.from({ length: 5 }, (_, i) => `Takeaway ${i + 1}`);
      renderWithPlayer(<VideoHero {...DEFAULT_PROPS} keyTakeaways={five} />);
      expect(screen.getByText('Takeaway 3')).toBeInTheDocument();
      expect(screen.queryByText('Takeaway 4')).not.toBeInTheDocument();
      expect(screen.getByRole('button', { name: /^\+2 · More takeaways$/ })).toBeInTheDocument();
    });

    it('should not show a more control when every takeaway fits', () => {
      renderWithPlayer(<VideoHero {...DEFAULT_PROPS} />);
      expect(screen.queryByRole('button', { name: /More takeaways/ })).not.toBeInTheDocument();
    });

    it('should clamp a long brief until "Show all" is clicked', async () => {
      const user = userEvent.setup();
      const long = Array.from({ length: 8 }, (_, i) => `Sentence number ${i + 1} of a long brief that runs well past five lines.`).join(' ');
      renderWithPlayer(<VideoHero {...DEFAULT_PROPS} masterSummary={long} />);
      const showAll = screen.getByRole('button', { name: /^show all$/i });
      expect(showAll).toBeInTheDocument();
      await user.click(showAll);
      expect(screen.getByRole('button', { name: /^hide$/i })).toBeInTheDocument();
    });

    it('should render a domain chip for podcast, news, gaming and sport', () => {
      renderWithPlayer(<VideoHero {...DEFAULT_PROPS} primaryTag="podcast" />);
      expect(screen.getByText('Podcast')).toBeInTheDocument();
    });

    it('should not render the Key takeaways block when none are present and not streaming', () => {
      renderWithPlayer(<VideoHero {...DEFAULT_PROPS} keyTakeaways={undefined} />);
      expect(screen.queryByRole('button', { name: /key takeaways/i })).not.toBeInTheDocument();
    });

    it('should not render a separate YouTube link (Watch is the only video action)', () => {
      renderWithPlayer(<VideoHero {...DEFAULT_PROPS} />);
      expect(screen.queryByRole('link', { name: /youtube/i })).not.toBeInTheDocument();
      expect(screen.getByRole('button', { name: /watch/i })).toBeInTheDocument();
    });
  });

  describe('Key takeaways watch flow', () => {
    it('should default to open on mount', () => {
      renderWithPlayer(<VideoHero {...DEFAULT_PROPS} />);

      const takeawaysBtn = screen.getByRole('button', { name: /key takeaways/i });
      expect(takeawaysBtn).toHaveAttribute('aria-expanded', 'true');
    });

    it('should collapse and reopen when its toggle is clicked', async () => {
      const user = userEvent.setup();
      renderWithPlayer(<VideoHero {...DEFAULT_PROPS} />);

      const takeawaysBtn = screen.getByRole('button', { name: /key takeaways/i });
      await user.click(takeawaysBtn);
      expect(takeawaysBtn).toHaveAttribute('aria-expanded', 'false');

      await user.click(takeawaysBtn);
      expect(takeawaysBtn).toHaveAttribute('aria-expanded', 'true');
    });

    it('should not read or write localStorage for the takeaways state', async () => {
      const user = userEvent.setup();
      renderWithPlayer(<VideoHero {...DEFAULT_PROPS} />);

      await user.click(screen.getByRole('button', { name: /key takeaways/i }));

      expect(localStorage.getItem).not.toHaveBeenCalledWith('vie-hero-takeaways-open');
      expect(localStorage.setItem).not.toHaveBeenCalledWith('vie-hero-takeaways-open', expect.anything());
    });

    it('should auto-collapse the first time the player is opened', async () => {
      const user = userEvent.setup();
      renderWithPlayer(<VideoHero {...DEFAULT_PROPS} />);

      const takeawaysBtn = screen.getByRole('button', { name: /key takeaways/i });
      expect(takeawaysBtn).toHaveAttribute('aria-expanded', 'true');

      await user.click(screen.getByRole('button', { name: /watch/i }));

      expect(takeawaysBtn).toHaveAttribute('aria-expanded', 'false');
    });

    it('should auto-collapse the first time a seek fires', async () => {
      const user = userEvent.setup();
      renderWithPlayer(
        <>
          <VideoHero {...DEFAULT_PROPS} />
          <SeekProbe />
        </>,
      );

      await user.click(screen.getByRole('button', { name: /seek-probe/i }));

      expect(screen.getByRole('button', { name: /key takeaways/i })).toHaveAttribute('aria-expanded', 'false');
    });

    it('should never re-collapse after the user manually reopens', async () => {
      const user = userEvent.setup();
      renderWithPlayer(
        <>
          <VideoHero {...DEFAULT_PROPS} />
          <SeekProbe />
        </>,
      );

      // First engage auto-collapses.
      await user.click(screen.getByRole('button', { name: /watch/i }));
      const takeawaysBtn = screen.getByRole('button', { name: /key takeaways/i });
      expect(takeawaysBtn).toHaveAttribute('aria-expanded', 'false');

      // User reopens by hand — later seeks must leave it alone.
      await user.click(takeawaysBtn);
      expect(takeawaysBtn).toHaveAttribute('aria-expanded', 'true');

      await user.click(screen.getByRole('button', { name: /seek-probe/i }));
      expect(takeawaysBtn).toHaveAttribute('aria-expanded', 'true');
    });

    it('should not auto-collapse when the user toggled before engaging', async () => {
      const user = userEvent.setup();
      renderWithPlayer(<VideoHero {...DEFAULT_PROPS} />);

      const takeawaysBtn = screen.getByRole('button', { name: /key takeaways/i });
      // Manual close + reopen marks user ownership before any engagement.
      await user.click(takeawaysBtn);
      await user.click(takeawaysBtn);
      expect(takeawaysBtn).toHaveAttribute('aria-expanded', 'true');

      await user.click(screen.getByRole('button', { name: /watch/i }));

      expect(takeawaysBtn).toHaveAttribute('aria-expanded', 'true');
    });

    it('should not auto-collapse while streaming', async () => {
      const user = userEvent.setup();
      renderWithPlayer(<VideoHero {...DEFAULT_PROPS} isStreaming />);

      await user.click(screen.getByRole('button', { name: /watch/i }));

      expect(screen.getByRole('button', { name: /key takeaways/i })).toHaveAttribute('aria-expanded', 'true');
    });
  });

  describe('Escape closes the player', () => {
    it('should close the open player on Escape', async () => {
      const user = userEvent.setup();
      renderWithPlayer(<VideoHero {...DEFAULT_PROPS} />);

      await user.click(screen.getByRole('button', { name: /watch/i }));
      expect(screen.getByRole('button', { name: /hide/i })).toHaveAttribute('aria-expanded', 'true');

      await user.keyboard('{Escape}');

      expect(screen.getByRole('button', { name: /watch/i })).toHaveAttribute('aria-expanded', 'false');
    });

    it('should not close the player when Escape is pressed inside an input', async () => {
      const user = userEvent.setup();
      renderWithPlayer(
        <>
          <VideoHero {...DEFAULT_PROPS} />
          <input aria-label="note" />
        </>,
      );

      await user.click(screen.getByRole('button', { name: /watch/i }));
      await user.click(screen.getByRole('textbox', { name: /note/i }));
      await user.keyboard('{Escape}');

      expect(screen.getByRole('button', { name: /hide/i })).toHaveAttribute('aria-expanded', 'true');
    });
  });
});
