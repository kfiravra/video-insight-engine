import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Panel } from './Panel';

describe('Panel', () => {
  it('should render children when no title or actions', () => {
    render(<Panel>hello body</Panel>);
    expect(screen.getByText('hello body')).toBeTruthy();
  });

  it('should render title in header when provided', () => {
    render(<Panel title="Section A">body</Panel>);
    expect(screen.getByText('Section A')).toBeTruthy();
  });

  it('should render actions slot when provided', () => {
    render(
      <Panel title="t" actions={<button>act</button>}>
        body
      </Panel>,
    );
    expect(screen.getByRole('button', { name: 'act' })).toBeTruthy();
  });

  it('should apply raised tone by default', () => {
    const { container } = render(<Panel>x</Panel>);
    const panel = container.querySelector('[data-slot="panel"]');
    expect(panel?.getAttribute('data-tone')).toBe('raised');
    expect(panel?.className).toContain('bg-[var(--color-surface-raised)]');
  });

  it('should apply dim tone class when tone=dim', () => {
    const { container } = render(<Panel tone="dim">x</Panel>);
    const panel = container.querySelector('[data-slot="panel"]');
    expect(panel?.className).toContain('bg-[var(--color-surface-dim)]');
  });

  it('should apply default tone class when tone=default', () => {
    const { container } = render(<Panel tone="default">x</Panel>);
    const panel = container.querySelector('[data-slot="panel"]');
    expect(panel?.className).toContain('bg-[var(--color-surface)]');
  });

  it('should apply md padding by default', () => {
    const { container } = render(<Panel>x</Panel>);
    const panel = container.querySelector('[data-slot="panel"]');
    expect(panel?.className).toContain('p-4');
  });

  it('should apply sm padding when padding=sm', () => {
    const { container } = render(<Panel padding="sm">x</Panel>);
    const panel = container.querySelector('[data-slot="panel"]');
    expect(panel?.className).toContain('p-3');
  });

  it('should apply lg padding when padding=lg', () => {
    const { container } = render(<Panel padding="lg">x</Panel>);
    const panel = container.querySelector('[data-slot="panel"]');
    expect(panel?.className).toContain('p-6');
  });

  it('should omit padding class when padding=none', () => {
    const { container } = render(<Panel padding="none">x</Panel>);
    const panel = container.querySelector('[data-slot="panel"]');
    expect(panel?.className).not.toContain('p-4');
    expect(panel?.className).not.toContain('p-3');
    expect(panel?.className).not.toContain('p-6');
  });

  it('should inset the header when padding=none so the title clears the border', () => {
    const { container } = render(<Panel padding="none" title="Flush">x</Panel>);
    const header = container.querySelector('[data-slot="panel-header"]');
    expect(header?.className).toContain('px-4');
    expect(header?.className).toContain('pt-3');
  });

  it('should not inset the header when the panel already has padding', () => {
    const { container } = render(<Panel padding="md" title="Padded">x</Panel>);
    const header = container.querySelector('[data-slot="panel-header"]');
    expect(header?.className).not.toContain('px-4');
  });

  it('should merge custom className', () => {
    const { container } = render(<Panel className="extra-class">x</Panel>);
    const panel = container.querySelector('[data-slot="panel"]');
    expect(panel?.className).toContain('extra-class');
  });

  it('should not render header when no title or actions', () => {
    const { container } = render(<Panel>body</Panel>);
    expect(container.querySelector('[data-slot="panel-header"]')).toBeNull();
  });
});
