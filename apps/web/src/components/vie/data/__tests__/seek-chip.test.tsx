import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { DirectionProvider } from '@/contexts/DirectionContext';
import { SeekChip } from '../SeekChip';

describe('SeekChip', () => {
  it('should seek when clicked', async () => {
    const user = userEvent.setup();
    const onSeek = vi.fn();
    render(<SeekChip seconds={373} onSeek={onSeek} />);
    await user.click(screen.getByRole('button', { name: 'Jump to 6:13' }));
    expect(onSeek).toHaveBeenCalledWith(373);
  });

  it('should render a static marker without a seek handler', () => {
    render(<SeekChip seconds={373} />);
    expect(screen.getByText('6:13')).toBeInTheDocument();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  it('should format hour-long timestamps as h:mm:ss', () => {
    render(<SeekChip seconds={3725} onSeek={vi.fn()} />);
    expect(screen.getByRole('button', { name: 'Jump to 1:02:05' })).toBeInTheDocument();
  });

  it('should merge a caller className onto the chip', () => {
    render(<SeekChip seconds={5} className="ms-auto" />);
    expect(screen.getByText('0:05')).toHaveClass('ms-auto');
  });

  it.each([
    ['he', 'מעבר אל 6:13'],
    ['ar', 'الانتقال إلى 6:13'],
  ])('should name the chip in the page language when the output is %s', (language, name) => {
    render(
      <DirectionProvider language={language} isRTL>
        <SeekChip seconds={373} onSeek={vi.fn()} />
      </DirectionProvider>,
    );
    expect(screen.getByRole('button', { name })).toBeInTheDocument();
  });
});
