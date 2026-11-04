import { describe, it, expect } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { useState } from 'react';
import { HeaderSlotContext } from '@/components/layout/header-slot';
import type { VideoBilling } from '@/api/videos';
import { VideoTokenLine } from '../VideoTokenLine';
import { renderIn } from './usage-fixtures';

function billing(overrides: Partial<VideoBilling>): VideoBilling {
  return { state: 'held', estimatedTokens: 840, chargedTokens: null, ...overrides };
}

/** A stand-in for Layout: provides the header slot the line portals into. */
function WithHeaderSlot({ value }: { value: VideoBilling }) {
  const [slot, setSlot] = useState<HTMLElement | null>(null);
  return (
    <HeaderSlotContext.Provider value={slot}>
      <header data-testid="header">
        <div ref={setSlot} />
      </header>
      <main data-testid="main">
        <VideoTokenLine billing={value} />
      </main>
    </HeaderSlotContext.Provider>
  );
}

describe('VideoTokenLine', () => {
  it.each([
    ['held', billing({}), '≈ 840 tokens, in progress'],
    ['charged', billing({ state: 'charged', chargedTokens: 758 }), '758 tokens'],
    ['charged with one token', billing({ state: 'charged', chargedTokens: 1 }), '1 token'],
    ['free', billing({ state: 'free', estimatedTokens: 0 }), 'Free'],
    ['released', billing({ state: 'released' }), 'Not charged'],
  ])('should word a %s run', (_name, value, expected) => {
    renderIn(<VideoTokenLine billing={value} />);

    expect(screen.getByTestId('video-token-line')).toHaveTextContent(expected);
  });

  it('should fall back to the estimate for a charged run that names no charge', () => {
    renderIn(<VideoTokenLine billing={billing({ state: 'charged', chargedTokens: null })} />);

    expect(screen.getByTestId('video-token-line')).toHaveTextContent('840 tokens');
  });

  it('should keep the digits tabular and print no price', () => {
    renderIn(<VideoTokenLine billing={billing({ state: 'charged', chargedTokens: 1_443 })} />);

    const line = screen.getByTestId('video-token-line');
    expect([line.className.includes('tabular-nums'), line.textContent?.includes('$')]).toEqual([true, false]);
  });

  it('should render nothing without billing', () => {
    renderIn(<VideoTokenLine billing={undefined} />);

    expect(screen.queryByTestId('video-token-line')).toBeNull();
  });

  it('should sit in the header slot when a layout provides one, costing the page no row', () => {
    render(<WithHeaderSlot value={billing({ state: 'charged', chargedTokens: 758 })} />);

    expect(within(screen.getByTestId('header')).getByTestId('video-token-line')).toHaveTextContent('758 tokens');
    expect(within(screen.getByTestId('main')).queryByTestId('video-token-line')).toBeNull();
  });
});
