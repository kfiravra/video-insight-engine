/**
 * The concept graph model behind ConceptCanvas: typed/legacy connections
 * resolved to concept ids, a group lane per concept, and the undirected
 * adjacency the inspector lists. Pure — shared by the list and the map and
 * unit-tested directly.
 */
import type { InspectorNeighbor } from '@/components/vie/canvas/CanvasInspector';
import type { ConceptItem, ConceptConnection, ConceptRelation } from '@vie/types';

const DEFAULT_GROUP = 'Concepts';

export function conceptNodeId(index: number): string {
  return `concept-${index}`;
}

/** Coerce a connection (legacy bare string OR typed object) to `{to, type}`. */
function normalizeConnection(conn: string | ConceptConnection): ConceptConnection {
  if (typeof conn === 'string') return { to: conn, type: 'relatesTo' };
  return { to: conn.to, type: conn.type ?? 'relatesTo' };
}

export interface ConceptGraphNode {
  id: string;
  concept: ConceptItem;
  group: string;
  groupIndex: number;
}

export interface ConceptGraphEdge {
  id: string;
  source: string;
  target: string;
  relation: ConceptRelation;
}

export interface ConceptGraphModel {
  nodes: ConceptGraphNode[];
  edges: ConceptGraphEdge[];
  /** id → connected neighbours (both directions), with the relation that links them. */
  adjacency: Map<string, InspectorNeighbor[]>;
  groups: string[];
}

/**
 * Build the concept graph from raw props. Resolves typed/legacy connections to
 * real concept ids (dropping hallucinated / self references), assigns a group
 * lane per concept, and collects an undirected adjacency map for the inspector.
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

  const edges: ConceptGraphEdge[] = [];
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
