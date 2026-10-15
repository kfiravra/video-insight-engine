import { memo, useCallback, useEffect, useMemo, useState } from 'react';
import {
  Handle,
  MarkerType,
  Position,
  useEdgesState,
  useNodesState,
  type Edge,
  type Node,
  type NodeMouseHandler,
  type NodeTypes,
  type EdgeTypes,
} from '@xyflow/react';

import { Skeleton } from '@/components/ui/skeleton';
import { VieCanvas } from '@/components/vie/canvas/CanvasShell';
import {
  FloatingEdge,
  type EdgeVisualState,
  isDirectionalRelation,
} from '@/components/vie/canvas/FloatingEdge';
import { CanvasInspector } from '@/components/vie/canvas/CanvasInspector';
import { NODE_WIDTH, useGraphLayout } from '@/components/vie/canvas/useGraphLayout';
import { GlassCard } from '@/components/vie';
import { cn } from '@/lib/utils';
import type { ConceptItem } from '@vie/types';

import { GroupDot } from './ConceptCanvasList';
import type { ConceptGraphModel } from './concept-graph';

const ACCENT = 'var(--vie-accent, var(--primary))';

// ─── Node component ───

interface ConceptNodeData extends Record<string, unknown> {
  concept: ConceptItem;
  groupIndex: number;
  selected: boolean;
  dimmed: boolean;
  onSelect: (id: string) => void;
  nodeId: string;
}

const ConceptNode = memo(function ConceptNode({ data }: { data: ConceptNodeData }) {
  const { concept, groupIndex, selected, dimmed, onSelect, nodeId } = data;
  return (
    <div
      data-slot="vie-concept-node"
      data-selected={selected || undefined}
      className={cn('transition-opacity duration-200', dimmed && 'opacity-30')}
    >
      {/* Hidden handles — FloatingEdge computes its own center-to-center geometry,
          but React Flow still requires a source + target handle to wire an edge. */}
      <Handle
        type="target"
        position={Position.Top}
        isConnectable={false}
        className="!h-px !w-px !min-w-0 !border-0 !bg-transparent !opacity-0"
      />
      <Handle
        type="source"
        position={Position.Bottom}
        isConnectable={false}
        className="!h-px !w-px !min-w-0 !border-0 !bg-transparent !opacity-0"
      />
      <GlassCard
        variant={selected ? 'interactive' : 'default'}
        className={cn(
          'w-[184px] cursor-pointer p-2.5 text-start transition-colors',
          'hover:border-[color:var(--vie-accent,var(--primary))]/60',
          selected &&
            'border-[color:var(--vie-accent,var(--primary))] ring-2 ring-[color:var(--vie-accent,var(--primary))]/40',
        )}
      >
        <button
          type="button"
          onClick={(event) => {
            event.stopPropagation();
            onSelect(nodeId);
          }}
          className="flex w-full items-center gap-2 text-start outline-none focus-visible:underline"
          aria-pressed={selected}
        >
          <span className="text-xl leading-none" aria-hidden="true">
            {concept.emoji || '💡'}
          </span>
          <span className="flex-1 truncate text-sm font-semibold text-foreground">
            {concept.name}
          </span>
          <GroupDot index={groupIndex} />
        </button>
      </GlassCard>
    </div>
  );
});

const NODE_TYPES: NodeTypes = { concept: ConceptNode };
const EDGE_TYPES: EdgeTypes = { floating: FloatingEdge };

// ─── Legends ───

const RELATION_LEGEND: Array<{ label: string; dash?: string }> = [
  { label: 'causes / requires' },
  { label: 'contrasts', dash: '7 5' },
  { label: 'related', dash: '1.5 6' },
];

function RelationLegend() {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
      {RELATION_LEGEND.map((item) => (
        <span key={item.label} className="inline-flex items-center gap-1.5 text-[11px] text-muted-foreground">
          <svg width="22" height="6" viewBox="0 0 22 6" aria-hidden="true">
            <line
              x1="1"
              y1="3"
              x2="21"
              y2="3"
              stroke={ACCENT}
              strokeWidth="1.6"
              strokeDasharray={item.dash}
              strokeLinecap="round"
            />
          </svg>
          {item.label}
        </span>
      ))}
    </div>
  );
}

/** Relationship key + group key (both single-accent, no hue). */
function MapLegends({ groups }: { groups: string[] }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 rounded-lg border border-border/60 bg-muted/10 px-3 py-2">
      <RelationLegend />
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        {groups.map((group, i) => (
          <span key={group} className="inline-flex items-center gap-1.5 text-[11px] text-muted-foreground">
            <GroupDot index={i} />
            {group}
          </span>
        ))}
      </div>
    </div>
  );
}

// ─── Map ───

export interface ConceptMapProps {
  model: ConceptGraphModel;
  selectedId: string | null;
  onSelect: (id: string) => void;
  onClose: () => void;
  onSeek?: (seconds: number) => void;
  isDesktop: boolean;
}

/** The map opens on its first lane — the first group that has concepts
 *  (`useGraphLayout` lays the lanes out left to right in group order). */
function firstLaneNodeIds(model: ConceptGraphModel): string[] {
  const firstGroup = model.groups.find((group) => model.nodes.some((n) => n.group === group));
  return model.nodes.filter((n) => n.group === firstGroup).map((n) => n.id);
}

/** React Flow node set with the selection decoration derived during render
 *  (single source of truth) — pushing it in via effects let edge state desync
 *  from the model when `concepts` changed mid-selection. */
function useDecoratedGraph(
  model: ConceptGraphModel,
  positions: Record<string, { x: number; y: number }>,
  selectedId: string | null,
  onSelect: (id: string) => void,
) {
  // Highlight set = selected node + its neighbours.
  const highlightSet = useMemo(() => {
    if (!selectedId) return null;
    const set = new Set<string>([selectedId]);
    for (const neighbor of model.adjacency.get(selectedId) ?? []) set.add(neighbor.id);
    return set;
  }, [selectedId, model.adjacency]);

  const nodes = useMemo<Node<ConceptNodeData>[]>(
    () =>
      model.nodes.map((n) => ({
        id: n.id,
        type: 'concept',
        position: positions[n.id] ?? { x: 0, y: 0 },
        draggable: false,
        data: {
          concept: n.concept,
          groupIndex: n.groupIndex,
          selected: n.id === selectedId,
          dimmed: highlightSet != null && !highlightSet.has(n.id),
          onSelect,
          nodeId: n.id,
        },
      })),
    [model.nodes, positions, onSelect, selectedId, highlightSet],
  );

  const edges = useMemo<Edge[]>(
    () =>
      model.edges.map((e) => {
        const state: EdgeVisualState =
          selectedId == null ? 'rest' : e.source === selectedId || e.target === selectedId ? 'active' : 'dimmed';
        return {
          id: e.id,
          source: e.source,
          target: e.target,
          type: 'floating',
          data: { relation: e.relation, state },
          markerEnd: isDirectionalRelation(e.relation)
            ? { type: MarkerType.ArrowClosed, color: ACCENT, width: 16, height: 16 }
            : undefined,
        };
      }),
    [model.edges, selectedId],
  );

  return { nodes, edges };
}

/**
 * The map view of the concept canvas: a deterministic Dagre layout (tiered
 * top→bottom by connection rank, clustered into per-group lanes —
 * `useGraphLayout`) on @xyflow/react, locked nodes, relationship type encoded
 * on the edges by line-style + arrowhead (`FloatingEdge`), the inspector
 * docked over the canvas. Opens on the first lane rather than centring every
 * lane at the zoom floor (which cut the first lane on phones). Mounted only
 * while the map is the chosen view, so the layout and React Flow state never
 * run for the list.
 */
export const ConceptMap = memo(function ConceptMap({
  model,
  selectedId,
  onSelect,
  onClose,
  onSeek,
  isDesktop,
}: ConceptMapProps) {
  const layout = useGraphLayout(
    model.nodes.map((n) => ({ id: n.id, group: n.group })),
    model.edges.map((e) => ({ source: e.source, target: e.target })),
    model.groups,
  );
  const { nodes: baseNodes, edges: baseEdges } = useDecoratedGraph(model, layout.positions, selectedId, onSelect);
  const startNodeIds = useMemo(() => firstLaneNodeIds(model), [model]);
  const [nodes, setNodes, onNodesChange] = useNodesState<Node<ConceptNodeData>>(baseNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>(baseEdges);
  // Keep React Flow's controlled state in sync as the model or selection
  // changes. RF keys node measurements by id, so replacing node objects of the
  // same id preserves the measurements the FloatingEdge geometry depends on.
  useEffect(() => setNodes(baseNodes), [baseNodes, setNodes]);
  useEffect(() => setEdges(baseEdges), [baseEdges, setEdges]);

  // Flipped true by React Flow's onInit (canvas measured + first paint ready).
  // Until then a skeleton fills the canvas box to cover the blank mount window.
  const [canvasReady, setCanvasReady] = useState(false);
  const handleCanvasInit = useCallback(() => setCanvasReady(true), []);
  const handleNodeClick: NodeMouseHandler = useCallback((_event, node) => onSelect(node.id), [onSelect]);

  const selectedConcept = model.nodes.find((n) => n.id === selectedId)?.concept ?? null;
  const selectedNeighbors = selectedId ? model.adjacency.get(selectedId) ?? [] : [];
  const canvasHeight = Math.min(640, Math.max(420, layout.height + 120));

  return (
    <>
      <div className="relative" style={{ height: canvasHeight }}>
        <VieCanvas
          nodes={nodes}
          edges={edges}
          nodeTypes={NODE_TYPES}
          edgeTypes={EDGE_TYPES}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onNodeClick={handleNodeClick}
          onPaneClick={onClose}
          onInit={handleCanvasInit}
          nodesDraggable={false}
          nodesConnectable={false}
          nodeWidth={NODE_WIDTH}
          startNodeIds={startNodeIds}
          height={canvasHeight}
        />
        {!canvasReady && (
          <Skeleton aria-hidden="true" className="absolute inset-0 rounded-2xl bg-muted/40 motion-reduce:animate-none" />
        )}
        {selectedConcept && (
          <CanvasInspector
            concept={selectedConcept}
            neighbors={selectedNeighbors}
            onSelectNeighbor={onSelect}
            onClose={onClose}
            onSeek={onSeek}
            isDesktop={isDesktop}
          />
        )}
      </div>
      <MapLegends groups={model.groups} />
    </>
  );
});
