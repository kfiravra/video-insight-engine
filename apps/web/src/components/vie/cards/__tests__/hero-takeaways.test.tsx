import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect } from 'vitest';
import { HeroTakeaways } from '../HeroTakeaways';
import { VideoPlayerProvider } from '@/features/video-output/contexts/VideoPlayerContext';

// Nine takeaways: seven show on desktop, the "+2" toggle reveals the rest.
const NINE_TAKEAWAYS = Array.from({ length: 9 }, (_, i) => `Takeaway ${i + 1}`);

function renderTakeaways(): void {
  render(
    <VideoPlayerProvider>
      <HeroTakeaways takeaways={NINE_TAKEAWAYS} isStreaming={false} isPhone={false} />
    </VideoPlayerProvider>,
  );
}

describe('HeroTakeaways', () => {
  describe('more-takeaways toggle', () => {
    it('should point aria-controls at the takeaway list', () => {
      renderTakeaways();

      const toggle = screen.getByRole('button', { name: /^\+2 · More takeaways$/ });

      expect(document.getElementById(toggle.getAttribute('aria-controls') ?? '')).toBe(screen.getByRole('list'));
    });

    it('should report the list as collapsed while takeaways are hidden', () => {
      renderTakeaways();

      expect(screen.getByRole('button', { name: /^\+2 · More takeaways$/ })).toHaveAttribute('aria-expanded', 'false');
    });

    it('should show all seven takeaways on desktop when an hour-long video carries seven', () => {
      render(
        <VideoPlayerProvider>
          <HeroTakeaways takeaways={NINE_TAKEAWAYS.slice(0, 7)} isStreaming={false} isPhone={false} />
        </VideoPlayerProvider>,
      );

      expect(screen.getAllByRole('listitem')).toHaveLength(7);
    });

    it('should report the list as expanded once every takeaway shows', async () => {
      const user = userEvent.setup();
      renderTakeaways();

      await user.click(screen.getByRole('button', { name: /^\+2 · More takeaways$/ }));

      expect(screen.getByRole('button', { name: 'Hide takeaways' })).toHaveAttribute('aria-expanded', 'true');
    });
  });
});
