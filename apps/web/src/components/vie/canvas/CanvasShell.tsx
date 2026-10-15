import { memo, type ReactNode } from 'react';
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  Controls,
  MiniMap,
  type ColorMode,
  type Edge,
  type Node,
  type ReactFlowProps,
} from '@xyflow/react';

import '@xyflow/react/dist/style.css';

import { cn } from '@/lib/utils';

import { NODE_WIDTH } from './useGraphLayout';
import { useVieFlowTheme } from './useVieFlowTheme';

// ─── Zoom floor ───

/** Narrowest a node card may render on screen, in px (R8: nodes stay legible). */
const MIN_NODE_PX = 120;
const DEFAULT_MIN_ZOOM = 0.2;

/**
 * Zoom floor for a canvas whose narrowest node card is `nodeWidth` px wide:
 * the requested floor, raised wherever it would let that card shrink under
 * `MIN_NODE_PX`. `fitView` honours the floor, so a wide layout overflows the
 * viewport (pannable) instead of collapsing into a band of specks.
 */
function clampMinZoom(nodeWidth: number, requested: number = DEFAULT_MIN_ZOOM): number {
  if (!(nodeWidth > 0)) return requested;
  return Math.max(requested, MIN_NODE_PX / nodeWidth);
}

// ─── Canvas ───

export interface VieCanvasProps<
  NodeType extends Node = Node,
  EdgeType extends Edge = Edge,
> extends Omit<ReactFlowProps<NodeType, EdgeType>, 'colorMode' | 'height'> {
  children?: ReactNode;
  /** Render the MiniMap overlay. Defaults to false. */
  showMinimap?: boolean;
  /** Container className override. */
  className?: string;
  /** Container height — defaults to 480 px. Pass `'100%'` for layout-driven sizing. */
  height?: number | string;
  /** Design width of the consumer's narrowest node card, in px — drives the zoom floor. */
  nodeWidth?: number;
}

const tokenStyle = {
  width: '100%',
  '--xy-node-background-color-default': 'var(--card)',
  '--xy-node-border-default': '1px solid var(--border)',
  '--xy-node-color-default': 'var(--foreground)',
  '--xy-edge-stroke-default': 'var(--border)',
  '--xy-handle-background-color-default': 'var(--vie-accent, var(--primary))',
  '--xy-handle-border-color-default': 'var(--background)',
} as React.CSSProperties;

/**
 * Theme-aware ReactFlow wrapper. Wraps `<ReactFlowProvider>` once at the
 * component boundary so consumers don't need their own provider. Maps VIE
 * design tokens to Xyflow CSS custom props so node/edge defaults read against
 * the active theme.
 *
 * Disables scroll-zoom by default to avoid hijacking page scroll, and hides
 * the upstream attribution chip (we display it elsewhere in the design
 * system).
 *
 * Bounded pan (`translateExtent` / `nodeExtent`), static nodes
 * (`nodesDraggable={false}`), and custom `edgeTypes` (e.g. the concept canvas
 * `FloatingEdge`) all pass straight through via `...rest`. `minZoom` is a
 * requested floor; the effective floor is `clampMinZoom(nodeWidth, minZoom)`
 * so no node card ever renders narrower than `MIN_NODE_PX`.
 */
function VieCanvasInner<
  NodeType extends Node = Node,
  EdgeType extends Edge = Edge,
>({
  children,
  showMinimap = false,
  className,
  height = 480,
  nodeWidth = NODE_WIDTH,
  defaultEdgeOptions,
  panOnDrag = true,
  zoomOnScroll = false,
  fitView = true,
  minZoom = DEFAULT_MIN_ZOOM,
  ...rest
}: VieCanvasProps<NodeType, EdgeType>) {
  const { isDark } = useVieFlowTheme();
  const colorMode: ColorMode = isDark ? 'dark' : 'light';

  return (
    <ReactFlowProvider>
      <div
        data-slot="vie-canvas"
        className={cn(
          'relative overflow-hidden rounded-2xl border border-border bg-background',
          className,
        )}
        style={{ ...tokenStyle, height }}
      >
        <ReactFlow
          colorMode={colorMode}
          proOptions={{ hideAttribution: true }}
          defaultEdgeOptions={{ type: 'smoothstep', ...defaultEdgeOptions }}
          fitView={fitView}
          panOnDrag={panOnDrag}
          zoomOnScroll={zoomOnScroll}
          minZoom={clampMinZoom(nodeWidth, minZoom)}
          {...rest}
        >
          <Background gap={16} size={1} color="var(--border)" />
          <Controls position="bottom-right" />
          {showMinimap && (
            <MiniMap
              pannable
              zoomable
              maskColor="oklch(from var(--background) l c h / 0.7)"
              nodeStrokeColor="var(--border)"
              nodeColor="var(--card)"
            />
          )}
          {children}
        </ReactFlow>
      </div>
    </ReactFlowProvider>
  );
}

/**
 * Theme-aware ReactFlow wrapper, memoized. The `as typeof VieCanvasInner` cast
 * preserves the generic node/edge type parameters through `memo()` so callers
 * can pass a strongly-typed `onNodesChange` without an `as never` escape hatch.
 */
export const VieCanvas = memo(VieCanvasInner) as typeof VieCanvasInner;

// ─── View toggle ───

export interface CanvasViewOption<V extends string> {
  value: V;
  label: string;
}

export interface CanvasViewToggleProps<V extends string> {
  /** Accessible tablist name — keep it distinct from the page's hero tablist. */
  label: string;
  value: V;
  options: ReadonlyArray<CanvasViewOption<V>>;
  onChange: (value: V) => void;
}

/**
 * List ⇄ graph switch shared by the canvases. Canvases open in list mode on
 * every viewport (D8); the graph is the opt-in view behind this toggle.
 */
export function CanvasViewToggle<V extends string>({
  label,
  value,
  options,
  onChange,
}: CanvasViewToggleProps<V>) {
  return (
    <div
      role="tablist"
      aria-label={label}
      className="inline-flex items-center rounded-lg border border-border bg-muted/20 p-0.5 text-xs font-semibold"
    >
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          role="tab"
          aria-selected={value === option.value}
          onClick={() => onChange(option.value)}
          className={cn(
            'rounded-md px-3 py-1 transition-colors',
            value === option.value
              ? 'bg-[var(--vie-accent,var(--primary))] text-[var(--vie-accent-foreground)]'
              : 'text-muted-foreground hover:text-foreground',
          )}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}
