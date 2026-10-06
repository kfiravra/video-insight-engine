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

import { useVieFlowTheme } from './useVieFlowTheme';

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
 * `FloatingEdge`) all pass straight through via `...rest`. `minZoom` defaults
 * lower than ReactFlow's 0.5 so wide grouped-lane layouts can `fitView` without
 * clamping; override per-consumer when needed.
 */
function VieCanvasInner<
  NodeType extends Node = Node,
  EdgeType extends Edge = Edge,
>({
  children,
  showMinimap = false,
  className,
  height = 480,
  defaultEdgeOptions,
  panOnDrag = true,
  zoomOnScroll = false,
  fitView = true,
  minZoom = 0.2,
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
          minZoom={minZoom}
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
