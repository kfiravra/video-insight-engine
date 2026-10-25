import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { HeroTabButton } from '../HeroTabButton';

// The plan-done Moments tab streams `provisional: true` and is replaced under
// the same id at complete (D5): the strip says "Preview" only in between.
const MOMENTS = { id: 'key_moments', label: 'Key Moments', emoji: '🎬', dataSource: '' };

describe('HeroTabButton', () => {
  it('should label a provisional tab "Preview" in the strip', () => {
    render(<HeroTabButton tab={{ ...MOMENTS, provisional: true }} isActive={false} isCompleted={false} />);

    expect(screen.getByRole('tab', { name: /key moments preview/i })).toBeInTheDocument();
  });

  it('should drop the Preview marker once the real tab replaces the provisional one', () => {
    render(<HeroTabButton tab={MOMENTS} isActive={false} isCompleted={false} />);

    expect(screen.getByRole('tab', { name: 'Key Moments' })).toBeInTheDocument();
  });
});
