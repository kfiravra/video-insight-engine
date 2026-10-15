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

import { CanvasViewToggle, VieCanvas } from '@/components/vie/canvas/CanvasShell';
import {
  FloatingEdge,
  type EdgeVisualState,
  isDirectionalRelation,
} from '@/components/vie/canvas/FloatingEdge';
import {
  CanvasInspector,
  type InspectorNeighbor,
} from '@/components/vie/canvas/CanvasInspector';
import { NODE_WIDTH, useGraphLayout } from '@/components/vie/canvas/useGraphLayout';
import { Network } from 'lucide-react';

import { GlassCard } from '@/components/vie';
import { ConceptCanvasList, GroupDot } from './ConceptCanvasList';
import { EmptyTabState } from './EmptyTabState';
import { useIsDesktop } from '@/hooks/use-media-query';
import { cn } from '@/lib/utils';
import type { ConceptItem, ConceptConnection, ConceptRelation } from '@vie/types';

// ─── Props ───

interface ConceptCanvasProps {
  concepts: ConceptItem[];
  groups?: string[];
  onSeek?: (seconds: number) => void;
}

// ─── Model ───

const DEFAULT_GROUP = 'Concepts';
const ACCENT = 'var(--vie-accent, var(--primary))';

type ConceptView = 'list' | 'map';
const VIEW_OPTIONS = [
  { value: 'list', label: 'List' },
  { value: 'map', label: 'Map' },
] as const satisfies ReadonlyArray<{ value: ConceptView; label: string }>;

function conceptNodeId(index: number): string {
  return `concept-${index}`;
}

/** Coerce a connection (legacy bare string OR typed object) to `{to, type}`. */
function normalizeConnection(conn: string | ConceptConnection): ConceptConnection {
  if (typeof conn === 'string') return { to: conn, type: 'relatesTo' };
  return { to: conn.to, type: conn.type ?? 'relatesTo' };
}

export interface ConceptGraphModel {
  nodes: Array<{ id: string; concept: ConceptItem; group: string; groupIndex: number }>;
  edges: Array<{ id: string; source: string; target: string; relation: ConceptRelation }>;
  /** id → connected neighbours (both directions), with the relation that links them. */
  adjacency: Map<string, InspectorNeighbor[]>;
  groups: string[];
}

/**
 * Build the concept graph from raw props. Resolves typed/legacy connections to
 * real concept ids (dropping hallucinated / self references), assigns a group
 * lane per concept, and collects an undirected adjacency map for the inspector.
 * Pure + exported for unit testing.
 */
export function buildConceptGraph(
  concepts: ConceptItem[],
  groupsProp?: string[],
): ConceptGraphModel {
  const safe = (concepts ?? []).filter((c) => c && c.name && c.definition);

  // Group lanes: prefer the assembler's ordered list, else derive from concepts.
  const orderedGroups: string[] = [...(groupsProp ?? [])];
  const groupForConcept = safe.map((c) => (c.group?.trim() ? c.group.trim() : DEFAULT_GROUP));
  for (const group of groupForConcept) {
    if (!orderedGroups.includes(group)) orderedGroups.push(group);
  }
  if (orderedGroups.length === 0) orderedGroups.push(DEFAULT_GROUP);

  const nameToId = new Map<string, string>();
  safe.forEach((concept, i) => nameToId.set(concept.name.trim().toLowerCase(), conceptNodeId(i)));

  const nodes = safe.map((concept, i) => {
    const group = groupForConcept[i];
    return { id: conceptNodeId(i), concept, group, groupIndex: Math.max(0, orderedGroups.indexOf(group)) };
  });

  const adjacency = new Map<string, InspectorNeighbor[]>();
  const pushNeighbor = (fromId: string, toId: string, relation: ConceptRelation): void => {
    const node = nodes.find((n) => n.id === toId);
    if (!node) return;
    const list = adjacency.get(fromId) ?? [];
    if (list.some((n) => n.id === toId)) return;
    list.push({ id: toId, name: node.concept.name, emoji: node.concept.emoji, relation });
    adjacency.set(fromId, list);
  };

  const edges: ConceptGraphModel['edges'] = [];
  const seenPairs = new Set<string>();
  safe.forEach((concept, i) => {
    const sourceId = conceptNodeId(i);
    for (const raw of concept.connections ?? []) {
      const { to, type } = normalizeConnection(raw);
      const targetId = nameToId.get(to.trim().toLowerCase());
      if (!targetId || targetId === sourceId) continue;
      // Adjacency is undirected (inspector shows everything connected).
      pushNeighbor(sourceId, targetId, type);
      pushNeighbor(targetId, sourceId, type);
      // One drawn edge per unordered pair to avoid overlapping reciprocal lines.
      const pairKey = [sourceId, targetId].sort().join('|');
      if (seenPairs.has(pairKey)) continue;
      seenPairs.add(pairKey);
      edges.push({ id: `${sourceId}->${targetId}`, source: sourceId, target: targetId, relation: type });
    }
  });

  return { nodes, edges, adjacency, groups: orderedGroups };
}

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

// ─── Canvas ───

/**
 * Concept map rendered on @xyflow/react. Concepts are laid out by a deterministic
 * Dagre layout — tiered top→bottom by connection rank, clustered into per-group
 * horizontal lanes (`useGraphLayout`). Nodes are locked (`nodesDraggable={false}`)
 * with bounded pan; there is no localStorage persistence. Relationship type is
 * encoded on the edges by line-style + arrowhead (`FloatingEdge`), never hue.
 *
 * Selecting a concept docks the `CanvasInspector` (rendered outside the flow
 * node tree so it is never z-trapped), brightens the selected neighbourhood and
 * dims the rest. Opens in the list view on every viewport — definition,
 * example and seek chip inline per concept (R8 / D8); the map is a toggle.
 */
export const ConceptCanvas = memo(function ConceptCanvas({
  concepts,
  groups: groupsProp,
  onSeek,
}: ConceptCanvasProps) {
  const isDesktop = useIsDesktop();
  const model = useMemo(() => buildConceptGraph(concepts, groupsProp), [concepts, groupsProp]);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [view, setView] = useState<ConceptView>('list');
  // Flipped true by React Flow's onInit (canvas measured + first paint ready).
  // Until then a skeleton fills the canvas box to cover the blank mount window.
  const [canvasReady, setCanvasReady] = useState(false);

  const handleSelect = useCallback((id: string) => setSelectedId(id), []);
  const handleClose = useCallback(() => setSelectedId(null), []);
  const handleCanvasInit = useCallback(() => setCanvasReady(true), []);

  const layout = useGraphLayout(
    model.nodes.map((n) => ({ id: n.id, group: n.group })),
    model.edges.map((e) => ({ source: e.source, target: e.target })),
    model.groups,
  );

  // Highlight set = selected node + its neighbours.
  const highlightSet = useMemo(() => {
    if (!selectedId) return null;
    const set = new Set<string>([selectedId]);
    for (const neighbor of model.adjacency.get(selectedId) ?? []) set.add(neighbor.id);
    return set;
  }, [selectedId, model.adjacency]);

  // Selection/highlight decoration is derived during render (single source of
  // truth) rather than pushed in via effects — that previously let edge state
  // desync from the model when `concepts` changed mid-selection.
  const baseNodes = useMemo<Node<ConceptNodeData>[]>(() => {
    return model.nodes.map((n) => ({
      id: n.id,
      type: 'concept',
      position: layout.positions[n.id] ?? { x: 0, y: 0 },
      draggable: false,
      data: {
        concept: n.concept,
        groupIndex: n.groupIndex,
        selected: n.id === selectedId,
        dimmed: highlightSet != null && !highlightSet.has(n.id),
        onSelect: handleSelect,
        nodeId: n.id,
      },
    }));
  }, [model.nodes, layout, handleSelect, selectedId, highlightSet]);

  const baseEdges = useMemo<Edge[]>(() => {
    return model.edges.map((e) => {
      const state: EdgeVisualState =
        selectedId == null
          ? 'rest'
          : e.source === selectedId || e.target === selectedId
            ? 'active'
            : 'dimmed';
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
    });
  }, [model.edges, selectedId]);

  const [nodes, setNodes, onNodesChange] = useNodesState<Node<ConceptNodeData>>(baseNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>(baseEdges);

  // Keep React Flow's controlled state in sync as the model or selection
  // changes. RF keys node measurements by id, so replacing node objects of the
  // same id preserves the measurements the FloatingEdge geometry depends on.
  useEffect(() => setNodes(baseNodes), [baseNodes, setNodes]);
  useEffect(() => setEdges(baseEdges), [baseEdges, setEdges]);

  const handleNodeClick: NodeMouseHandler = useCallback(
    (_event, node) => setSelectedId(node.id),
    [],
  );

  const selectedConcept = useMemo(
    () => model.nodes.find((n) => n.id === selectedId)?.concept ?? null,
    [model.nodes, selectedId],
  );
  const selectedNeighbors = selectedId ? model.adjacency.get(selectedId) ?? [] : [];

  if (!model.nodes.length) {
    return <EmptyTabState message="No concepts were extracted for this video." icon={Network} />;
  }

  const counts = `${model.nodes.length} concepts · ${model.groups.length} groups · ${model.edges.length} links`;
  const canvasHeight = Math.min(640, Math.max(420, layout.height + 120));

  return (
    <div className="space-y-3" data-slot="concept-canvas">
      {/* Header strip: restrained analytics + view toggle */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs font-medium tabular-nums text-muted-foreground" data-testid="concept-canvas-counts">
          {counts}
        </p>
        <CanvasViewToggle label="Concept view" value={view} options={VIEW_OPTIONS} onChange={setView} />
      </div>

      <div className="relative">
        {view === 'map' ? (
          <div className="relative" style={{ height: canvasHeight }}>
            <VieCanvas
              nodes={nodes}
              edges={edges}
              nodeTypes={NODE_TYPES}
              edgeTypes={EDGE_TYPES}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              onNodeClick={handleNodeClick}
              onPaneClick={handleClose}
              onInit={handleCanvasInit}
              nodesDraggable={false}
              nodesConnectable={false}
              nodeWidth={NODE_WIDTH}
              height={canvasHeight}
            />
            {!canvasReady && (
              <Skeleton
                aria-hidden="true"
                className="absolute inset-0 rounded-2xl bg-muted/40 motion-reduce:animate-none"
              />
            )}
          </div>
        ) : (
          <ConceptCanvasList
            model={model}
            selectedId={selectedId}
            onSelect={handleSelect}
            onSeek={onSeek}
          />
        )}

        {selectedConcept && (
          <CanvasInspector
            concept={selectedConcept}
            neighbors={selectedNeighbors}
            onSelectNeighbor={handleSelect}
            onClose={handleClose}
            onSeek={onSeek}
            isDesktop={isDesktop}
          />
        )}
      </div>

      {/* Legends (map only): relationship key + group key (both single-accent, no hue) */}
      {view === 'map' && (
        <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 rounded-lg border border-border/60 bg-muted/10 px-3 py-2">
          <RelationLegend />
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            {model.groups.map((group, i) => (
              <span key={group} className="inline-flex items-center gap-1.5 text-[11px] text-muted-foreground">
                <GroupDot index={i} />
                {group}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
});
