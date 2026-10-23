import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import type { TabEntry } from '@vie/types';

import { HeaderSlotContext } from '@/components/layout/header-slot';
import { LanguageToggle } from '../LanguageToggle';
import { OutputRouter } from '../OutputRouter';

// output-quality pC: the English / source-language toggle lives in the app
// header — its own row above the hero cost the Hebrew page 48 px of the first
// screen (S12 desktop hero 448 px, phone panel at 524).

const TAB: TabEntry = {
  id: 'key_ideas',
  label: 'Key Ideas',
  emoji: '💡',
  component: 'checklist',
  props: { items: [{ label: 'A refund agent credits 8,000 instead of 80.' }] },
};

function renderRouter(wrap: (ui: ReactNode) => ReactNode = (ui) => ui): void {
  const toggle = <LanguageToggle showOriginal={false} onChange={vi.fn()} originalName="עברית" originalCode="he" />;
  render(
    wrap(
      <OutputRouter
        title="Who are LangChain and LangGraph?"
        videoSummaryId="vid-s12"
        tabs={[TAB]}
        meta={null}
        synthesis={{ tldr: 'LangGraph is for agents that loop.', keyTakeaways: [], masterSummary: '', seoDescription: '' }}
        languageToggle={toggle}
      />,
    ),
  );
}

const slots: HTMLElement[] = [];

/** The element Layout's AppHeader hands the page. */
function headerSlot(): HTMLElement {
  const slot = document.createElement('div');
  document.body.appendChild(slot);
  slots.push(slot);
  return slot;
}

afterEach(() => {
  slots.splice(0).forEach((slot) => slot.remove());
});

describe('OutputRouter — language toggle placement', () => {
  it('should render the toggle in the app header slot when the page sits in a Layout', () => {
    const slot = headerSlot();

    renderRouter((ui) => <HeaderSlotContext.Provider value={slot}>{ui}</HeaderSlotContext.Provider>);

    expect(within(slot).getByRole('radiogroup', { name: 'Content language' })).toBeInTheDocument();
  });

  it('should keep the toggle out of the content column when the header slot holds it', () => {
    const slot = headerSlot();

    renderRouter((ui) => <HeaderSlotContext.Provider value={slot}>{ui}</HeaderSlotContext.Provider>);

    expect(screen.getAllByRole('radiogroup', { name: 'Content language' })).toHaveLength(1);
  });

  it('should pin the toggle left-to-right in the header whatever the content direction', () => {
    const slot = headerSlot();

    renderRouter((ui) => <HeaderSlotContext.Provider value={slot}>{ui}</HeaderSlotContext.Provider>);

    expect(within(slot).getByRole('radiogroup').closest('[dir]')).toHaveAttribute('dir', 'ltr');
  });

  it('should render the toggle in place when no header slot exists', () => {
    renderRouter();

    expect(screen.getByRole('radiogroup', { name: 'Content language' })).toBeInTheDocument();
  });
});
