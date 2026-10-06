import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, renderHook, act } from '@testing-library/react';

import { VieCanvas } from '../CanvasShell';
import { useVieFlowTheme } from '../useVieFlowTheme';

describe('useVieFlowTheme', () => {
  afterEach(() => {
    delete document.documentElement.dataset.theme;
  });

  it.each(['dark', 'lagoon'])('should report dark when data-theme is %s', (theme) => {
    document.documentElement.dataset.theme = theme;
    const { result } = renderHook(() => useVieFlowTheme());
    expect(result.current.isDark).toBe(true);
  });

  it('should report light when data-theme is light', () => {
    document.documentElement.dataset.theme = 'light';
    const { result } = renderHook(() => useVieFlowTheme());
    expect(result.current.isDark).toBe(false);
  });

  it('should follow a data-theme change after mount', async () => {
    document.documentElement.dataset.theme = 'light';
    const { result } = renderHook(() => useVieFlowTheme());
    await act(async () => {
      document.documentElement.dataset.theme = 'dark';
      await Promise.resolve();
    });
    expect(result.current.isDark).toBe(true);
  });
});

describe('VieCanvas', () => {
  afterEach(() => {
    delete document.documentElement.dataset.theme;
  });

  it('should render React Flow in dark mode under the dark theme', () => {
    document.documentElement.dataset.theme = 'dark';
    const { container } = render(<VieCanvas nodes={[]} edges={[]} />);
    expect(container.querySelector('.react-flow')).toHaveClass('dark');
  });

  it('should render provided children inside the ReactFlow wrapper', () => {
    render(
      <VieCanvas nodes={[]} edges={[]}>
        <div data-testid="canvas-child">hello</div>
      </VieCanvas>,
    );
    const child = screen.getByTestId('canvas-child');
    expect(child).toBeInTheDocument();
    expect(child.closest('[data-slot="vie-canvas"]')).not.toBeNull();
  });

  it('should not render the minimap by default', () => {
    const { container } = render(<VieCanvas nodes={[]} edges={[]} />);
    expect(container.querySelector('.react-flow__minimap')).toBeNull();
  });

  it('should render the controls panel by default', () => {
    const { container } = render(<VieCanvas nodes={[]} edges={[]} />);
    expect(container.querySelector('.react-flow__controls')).not.toBeNull();
  });

  it('should accept bounded-pan + locked-node passthrough props without crashing', () => {
    // translateExtent / nodeExtent / nodesDraggable all flow through `...rest`
    // into ReactFlow — the concept canvas relies on this for static layout.
    const { container } = render(
      <VieCanvas
        nodes={[]}
        edges={[]}
        nodesDraggable={false}
        translateExtent={[
          [0, 0],
          [800, 600],
        ]}
      >
        <div data-testid="bounded-child">bounded</div>
      </VieCanvas>,
    );
    expect(screen.getByTestId('bounded-child')).toBeInTheDocument();
    expect(container.querySelector('[data-slot="vie-canvas"]')).not.toBeNull();
  });

  it('should accept a custom edgeTypes registration (e.g. FloatingEdge)', () => {
    const FakeEdge = () => null;
    const { container } = render(
      <VieCanvas nodes={[]} edges={[]} edgeTypes={{ floating: FakeEdge }} />,
    );
    expect(container.querySelector('[data-slot="vie-canvas"]')).not.toBeNull();
  });
});
