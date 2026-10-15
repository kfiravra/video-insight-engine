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
});
