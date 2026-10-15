import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, renderHook, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useStore } from '@xyflow/react';

import { CanvasViewToggle, VieCanvas } from '../CanvasShell';
import { useVieFlowTheme } from '../useVieFlowTheme';

/** Reads the zoom floor React Flow actually holds, from inside the provider. */
function MinZoomProbe() {
  const minZoom = useStore((state) => state.minZoom);
  return <span data-testid="min-zoom">{minZoom}</span>;
}

function probedMinZoom(): number {
  return Number(screen.getByTestId('min-zoom').textContent);
}

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

  it('should keep the requested zoom floor when no nodeWidth is given (clamp is opt-in)', () => {
    render(
      <VieCanvas nodes={[]} edges={[]}>
        <MinZoomProbe />
      </VieCanvas>,
    );
    expect(probedMinZoom()).toBe(0.2);
  });

  it('should raise the zoom floor so a 184 px node never renders under 120 px (R8)', () => {
    render(
      <VieCanvas nodes={[]} edges={[]} nodeWidth={184}>
        <MinZoomProbe />
      </VieCanvas>,
    );
    expect(probedMinZoom()).toBeCloseTo(120 / 184, 3);
  });

  it('should scale the zoom floor to the consumer node width', () => {
    render(
      <VieCanvas nodes={[]} edges={[]} nodeWidth={260}>
        <MinZoomProbe />
      </VieCanvas>,
    );
    expect(probedMinZoom()).toBeCloseTo(120 / 260, 3);
  });

  it('should keep a requested minZoom that is already above the floor', () => {
    render(
      <VieCanvas nodes={[]} edges={[]} minZoom={0.9}>
        <MinZoomProbe />
      </VieCanvas>,
    );
    expect(probedMinZoom()).toBe(0.9);
  });

  it('should accept a custom edgeTypes registration (e.g. FloatingEdge)', () => {
    const FakeEdge = () => null;
    const { container } = render(
      <VieCanvas nodes={[]} edges={[]} edgeTypes={{ floating: FakeEdge }} />,
    );
    expect(container.querySelector('[data-slot="vie-canvas"]')).not.toBeNull();
  });
});

describe('CanvasViewToggle', () => {
  const options = [
    { value: 'list', label: 'List' },
    { value: 'map', label: 'Map' },
  ] as const;

  it('should mark the current view as the pressed button', () => {
    render(<CanvasViewToggle label="Concept view" value="list" options={options} onChange={() => {}} />);
    expect(screen.getByRole('button', { name: 'List' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: 'Map' })).toHaveAttribute('aria-pressed', 'false');
  });

  it('should report the chosen view when another button is clicked', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<CanvasViewToggle label="Concept view" value="list" options={options} onChange={onChange} />);
    await user.click(screen.getByRole('button', { name: 'Map' }));
    expect(onChange).toHaveBeenCalledWith('map');
  });

  it('should expose a named group, never a tablist that could collide with the hero strip', () => {
    render(<CanvasViewToggle label="Steps view" value="list" options={options} onChange={() => {}} />);
    expect(screen.getByRole('group', { name: 'Steps view' })).toBeInTheDocument();
    expect(screen.queryByRole('tablist')).not.toBeInTheDocument();
  });
});
