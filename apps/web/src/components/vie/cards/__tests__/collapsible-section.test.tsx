import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { Button } from '@/components/ui/button';
import { CollapsibleSection } from '../CollapsibleSection';

function renderSection(open: boolean, onToggle = vi.fn()) {
  return render(
    <CollapsibleSection baseId="sec" label="Brief" open={open} onToggle={onToggle}>
      <Button>inside</Button>
    </CollapsibleSection>,
  );
}

describe('CollapsibleSection', () => {
  it('should expose the toggle with aria-expanded and aria-controls', () => {
    renderSection(true);
    const toggle = screen.getByRole('button', { name: 'Brief' });
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    expect(toggle).toHaveAttribute('aria-controls', 'sec-content');
  });

  it('should keep open content in the accessibility tree', () => {
    renderSection(true);
    expect(screen.getByRole('button', { name: 'inside' })).toBeInTheDocument();
  });

  it('should make collapsed content inert and hidden from assistive tech while keeping it mounted', () => {
    renderSection(false);
    const content = document.getElementById('sec-content') as HTMLElement;
    expect(content).toHaveAttribute('inert');
    expect(content).toHaveAttribute('aria-hidden', 'true');
    expect(screen.queryByRole('button', { name: 'inside' })).not.toBeInTheDocument();
    expect(screen.getByText('inside')).toBeInTheDocument();
  });

  it('should keep a row action out of the toggle so it does not join its accessible name', () => {
    render(
      <CollapsibleSection baseId="sec" label="Key takeaways" action={<Button>+4 more</Button>} open onToggle={vi.fn()}>
        <p>inside</p>
      </CollapsibleSection>,
    );
    expect(screen.getByRole('button', { name: 'Key takeaways' })).toBeInTheDocument();
  });

  it('should keep a row action reachable when the section is collapsed', () => {
    render(
      <CollapsibleSection baseId="sec" label="Key takeaways" action={<Button>+4 more</Button>} open={false} onToggle={vi.fn()}>
        <p>inside</p>
      </CollapsibleSection>,
    );
    expect(screen.getByRole('button', { name: '+4 more' })).toBeInTheDocument();
  });

  it('should call onToggle when the eyebrow row is clicked', async () => {
    const user = userEvent.setup();
    const onToggle = vi.fn();
    renderSection(false, onToggle);
    await user.click(screen.getByRole('button', { name: 'Brief' }));
    expect(onToggle).toHaveBeenCalledTimes(1);
  });
});
