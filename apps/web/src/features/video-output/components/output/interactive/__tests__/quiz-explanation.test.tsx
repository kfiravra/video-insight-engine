import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { QuizExplanation } from '../QuizExplanation';

describe('QuizExplanation', () => {
  it('should seek when the chip is clicked', async () => {
    const user = userEvent.setup();
    const onSeek = vi.fn();
    render(<QuizExplanation text="Said at (2:40) exactly." onSeek={onSeek} />);
    await user.click(screen.getByRole('button', { name: 'Jump to 2:40' }));
    expect(onSeek).toHaveBeenCalledWith(160);
  });

  it('should render a static chip without a seek handler', () => {
    render(<QuizExplanation text="Said at (2:40) exactly." />);
    expect(screen.getByText('2:40')).toBeInTheDocument();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  describe('question timestamp (output-quality 1.13)', () => {
    it('should render the answer timestamp as a chip when the text cites no time', async () => {
      const user = userEvent.setup();
      const onSeek = vi.fn();
      render(<QuizExplanation text="Said later in the talk." timestamp={272} onSeek={onSeek} />);
      await user.click(screen.getByRole('button', { name: 'Jump to 4:32' }));
      expect(onSeek).toHaveBeenCalledWith(272);
    });

    it('should not add a second chip when the text already cites a time', () => {
      render(<QuizExplanation text="Said at (2:40) exactly." timestamp={160} onSeek={vi.fn()} />);
      expect(screen.getAllByRole('button', { name: /jump to/i })).toHaveLength(1);
    });

    it('should render a chip for a timestamp at the very start', () => {
      render(<QuizExplanation text="The opening line." timestamp={0} />);
      expect(screen.getByText('0:00')).toBeInTheDocument();
    });
  });

  // p1 review R6: the explanation's (m:ss) is the model's word, unchecked; the
  // question's timestamp passed the enrichment's range check.
  describe('checked timestamp wins', () => {
    it('should seek to the checked timestamp when the cited time is beyond tolerance', async () => {
      const user = userEvent.setup();
      const onSeek = vi.fn();
      render(<QuizExplanation text="Said at (59:40) exactly." timestamp={900} onSeek={onSeek} />);
      await user.click(screen.getByRole('button', { name: /jump to/i }));
      expect(onSeek).toHaveBeenCalledWith(900);
    });

    it('should keep the cited time when it sits within tolerance of the timestamp', async () => {
      const user = userEvent.setup();
      const onSeek = vi.fn();
      render(<QuizExplanation text="Said at (2:40) exactly." timestamp={165} onSeek={onSeek} />);
      await user.click(screen.getByRole('button', { name: 'Jump to 2:40' }));
      expect(onSeek).toHaveBeenCalledWith(160);
    });

    it('should keep the cited time when no timestamp was checked', () => {
      render(<QuizExplanation text="Said at (59:40) exactly." onSeek={vi.fn()} />);
      expect(screen.getByRole('button', { name: 'Jump to 59:40' })).toBeInTheDocument();
    });

    it('should append no chip when the caller seeks to the timestamp itself', () => {
      render(<QuizExplanation text="Said later." timestamp={272} stampChip={false} onSeek={vi.fn()} />);
      expect(screen.queryByRole('button', { name: /jump to/i })).not.toBeInTheDocument();
    });
  });
});
