import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { CrossTabButton } from '../CrossTabButton';

describe('CrossTabButton', () => {
  it('should make the reason the link text and demote the label to a Next subline', () => {
    render(
      <CrossTabButton label="24 Core Arguments" reason="Explore how the arguments connect" onClick={vi.fn()} />,
    );
    const button = screen.getByRole('button');
    expect(button).toHaveTextContent('Explore how the arguments connect');
    expect(button).toHaveTextContent('Next: 24 Core Arguments');
  });

  it('should fall back to "Next: label" when no reason is given', () => {
    render(<CrossTabButton label="Test Yourself" description="Five questions" onClick={vi.fn()} />);
    expect(screen.getByText('Next: Test Yourself')).toBeInTheDocument();
    expect(screen.getByText('Five questions')).toBeInTheDocument();
  });

  it('should call onClick when activated', async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    render(<CrossTabButton label="Quiz" reason="Check what stuck" onClick={onClick} />);
    await user.click(screen.getByRole('button'));
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});
