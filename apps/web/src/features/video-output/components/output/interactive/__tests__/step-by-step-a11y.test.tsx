import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock('../../Celebration', () => ({
  Celebration: ({ title }: { title: string }) => <div data-testid="celebration">{title}</div>,
}));

import { StepByStepInteractive } from '../StepByStepInteractive';

const steps = [
  { number: 1, title: 'Prep', instruction: 'Chop vegetables', timestamp: 30 },
  { number: 2, title: 'Cook', instruction: 'Sauté in pan', timestamp: 95 },
  { number: 3, title: 'Serve', instruction: 'Plate and garnish' },
];

describe('StepByStepInteractive step controls', () => {
  it('should mark the first step as the current step when the list opens', () => {
    render(<StepByStepInteractive steps={steps} />);
    expect(screen.getByRole('button', { name: 'Go to step 1' })).toHaveAttribute('aria-current', 'step');
  });

  it('should make a step current when the user activates its control from the keyboard', async () => {
    const user = userEvent.setup();
    render(<StepByStepInteractive steps={steps} />);

    screen.getByRole('button', { name: 'Go to step 2' }).focus();
    await user.keyboard('{Enter}');

    expect(screen.getByRole('button', { name: 'Go to step 2' })).toHaveAttribute('aria-current', 'step');
    expect(screen.getByRole('button', { name: 'Go to step 1' })).not.toHaveAttribute('aria-current');
  });

  it('should make a step current when the user clicks anywhere on its row', async () => {
    const user = userEvent.setup();
    render(<StepByStepInteractive steps={steps} />);

    await user.click(screen.getByText('Plate and garnish'));

    expect(screen.getByRole('button', { name: 'Go to step 3' })).toHaveAttribute('aria-current', 'step');
  });

  it('should not nest any of a step row\'s buttons inside another button when seek is available', () => {
    render(<StepByStepInteractive steps={steps} onSeek={vi.fn()} />);

    const nested = screen
      .getAllByRole('button')
      .filter((button) => button.parentElement?.closest('button, [role="button"]'));

    expect(nested).toEqual([]);
  });
});
