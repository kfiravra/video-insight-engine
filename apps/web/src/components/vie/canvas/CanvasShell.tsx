import { memo, useMemo, type ReactNode } from 'react';
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  Controls,
  MiniMap,
  type ColorMode,
  type Edge,
  type FitViewOptions,
  type Node,
  type ReactFlowProps,
} from '@xyflow/react';

import '@xyflow/react/dist/style.css';

import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';

import { useVieFlowTheme } from './useVieFlowTheme';

// ─── Zoom floor ───

/** Narrowest a node card may render on screen, in px (R8: nodes stay legible). */
const MIN_NODE_PX = 120;
const DEFAULT_MIN_ZOOM = 0.2;

/**
 * Zoom floor for a canvas whose narrowest node card is `nodeWidth` px wide:
 * the requested floor, raised wherever it would let that card shrink under
 * `MIN_NODE_PX`. `fitView` honours the floor, so a wide layout overflows the
 * viewport (pannable) instead of collapsing into a band of specks. Opt-in:
 * without a `nodeWidth` the requested floor stands.
 */
function clampMinZoom(nodeWidth: number | undefined, requested: number): number {
  if (nodeWidth === undefined || !(nodeWidth > 0)) return requested;
  return Math.max(requested, MIN_NODE_PX / nodeWidth);
}

// ─── Opening view ───

/** Closest an opening view zooms in: start nodes show at their design size, never blown up. */
const START_MAX_ZOOM = 1;

/**
 * `fitView` options that open the canvas on its start nodes instead of the
 * whole graph: fitting everything centres a long graph at the zoom floor, so
 * the view opened mid-procedure. Only `maxZoom` is set — the floor stays
 * `clampMinZoom`'s, so start nodes keep the ≥ `MIN_NODE_PX` legibility.
 * Consumer options win key by key; without start nodes they pass through.
 */
function startFitViewOptions<NodeType extends Node>(
  startNodeIds: readonly string[] | undefined,
  fitViewOptions: FitViewOptions<NodeType> | undefined,
): FitViewOptions<NodeType> | undefined {
  if (!startNodeIds?.length) return fitViewOptions;
  return { nodes: startNodeIds.map((id) => ({ id })), maxZoom: START_MAX_ZOOM, ...fitViewOptions };
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
  /** Design width of the consumer's narrowest node card, in px — opts in to
   *  the zoom floor that keeps it legible. Omit it and `minZoom` is used as is. */
  nodeWidth?: number;
  /** Ids of the nodes the view opens on — the start of the graph (first
   *  steps, first lane), framed at ≤ 100 % zoom. Omit to fit the whole graph.
   *  Pass a stable (memoised) array: React Flow re-reads changed options. */
  startNodeIds?: readonly string[];
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
 * requested floor; a consumer that passes `nodeWidth` gets it raised to
 * `clampMinZoom(nodeWidth, minZoom)` so its node cards never render narrower
 * than `MIN_NODE_PX`. A consumer that passes `startNodeIds` opens on those
 * nodes (`startFitViewOptions`); the controls' fit button still shows all.
 */
function VieCanvasInner<
  NodeType extends Node = Node,
  EdgeType extends Edge = Edge,
>({
  children,
  showMinimap = false,
  className,
  height = 480,
  nodeWidth,
  startNodeIds,
  defaultEdgeOptions,
  panOnDrag = true,
  zoomOnScroll = false,
  fitView = true,
  fitViewOptions,
  minZoom = DEFAULT_MIN_ZOOM,
  ...rest
}: VieCanvasProps<NodeType, EdgeType>) {
  const { isDark } = useVieFlowTheme();
  const colorMode: ColorMode = isDark ? 'dark' : 'light';
  // Memoised: React Flow's store re-applies `fitViewOptions` whenever its identity changes.
  const openingFit = useMemo(
    () => startFitViewOptions(startNodeIds, fitViewOptions),
    [startNodeIds, fitViewOptions],
  );

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
          fitViewOptions={openingFit}
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
  /** Accessible group name ("Concept view", "Steps view"). */
  label: string;
  value: V;
  options: ReadonlyArray<CanvasViewOption<V>>;
  onChange: (value: V) => void;
}

/**
 * List ⇄ graph switch shared by the canvases. Canvases open in list mode on
 * every viewport (D8); the graph is the opt-in view behind this toggle. A
 * group of pressed/unpressed buttons — plain Tab order, no roving focus.
 */
export function CanvasViewToggle<V extends string>({
  label,
  value,
  options,
  onChange,
}: CanvasViewToggleProps<V>) {
  return (
    <div
      role="group"
      aria-label={label}
      className="inline-flex items-center rounded-lg border border-border bg-muted/20 p-0.5 text-xs font-semibold"
    >
      {options.map((option) => {
        const pressed = value === option.value;
        return (
          <Button
            key={option.value}
            variant="ghost"
            size="bare"
            aria-pressed={pressed}
            onClick={() => onChange(option.value)}
            className={cn(
              'rounded-md px-3 py-1 text-xs font-semibold transition-colors',
              pressed
                ? 'bg-[var(--vie-accent,var(--primary))] text-[var(--vie-accent-foreground)] hover:bg-[var(--vie-accent,var(--primary))] hover:text-[var(--vie-accent-foreground)]'
                : 'text-muted-foreground hover:bg-transparent hover:text-foreground',
            )}
          >
            {option.label}
          </Button>
        );
      })}
    </div>
  );
}
