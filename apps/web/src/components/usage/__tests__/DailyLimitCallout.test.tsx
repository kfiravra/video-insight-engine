import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { DailyLimitCallout } from '../DailyLimitCallout';

function renderCallout(props: Parameters<typeof DailyLimitCallout>[0]) {
  return render(
    <MemoryRouter>
      <DailyLimitCallout {...props} />
    </MemoryRouter>,
  );
}

describe('DailyLimitCallout', () => {
  it('should render the limit amount when provided', () => {
    renderCallout({ limitUsd: 2, resetAtIso: null });

    expect(screen.getByRole('alert')).toBeInTheDocument();
    expect(screen.getByText(/\$2\.00/)).toBeInTheDocument();
  });

  it('should expose an upgrade CTA linking to the billing settings', () => {
    renderCallout({ limitUsd: 2, resetAtIso: null });

    const cta = screen.getByTestId('daily-limit-upgrade');
    expect(cta).toHaveAttribute('href', '/settings/billing');
  });

  it('should describe the reset window in human terms', () => {
    // 2h 30m ahead: render latency can only move it to 2h 29m, never past an
    // hour boundary (an exact 3h made a same-millisecond render print "3h").
    const futureIso = new Date(Date.now() + 150 * 60 * 1000).toISOString();
    renderCallout({ limitUsd: 2, resetAtIso: futureIso });

    expect(screen.getByRole('alert')).toHaveTextContent(/resets in 2h(\s\d+m)?/i);
  });

  it('should fall back to "midnight UTC" when no reset timestamp is supplied', () => {
    renderCallout({ limitUsd: null, resetAtIso: null });

    expect(screen.getByRole('alert')).toHaveTextContent(/midnight UTC/);
  });
});
