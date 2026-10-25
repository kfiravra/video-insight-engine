import { memo, useCallback, useEffect, useMemo, useState } from 'react';
import { Handle, Position, useEdgesState, useNodesState, type Edge, type Node, type NodeTypes } from '@xyflow/react';

import { VieCanvas } from '@/components/vie/canvas/CanvasShell';
import { GlassCard, VisualEvidence } from '@/components/vie';
import { Skeleton } from '@/components/ui/skeleton';
import { cn } from '@/lib/utils';
import { parseStepDurationSeconds } from '@/features/video-output/lib/step-duration';
import type { StepItem } from '@vie/types';

import { CompleteToggle, DurationChip, StepNumber, type StepData } from './StepFlowChrome';
import { zigzagLayout } from './step-flow-layout';

/** Design width of a step node card; the canvas zoom floor keeps it legible. */
const STEP_NODE_WIDTH = 260;
/** The flow opens on its first steps — the start of the procedure. */
const OPENING_STEPS = 2;

function stepNodeId(index: number): string {
  return `step-${index}`;
}

// ─── Node ───

interface StepNodeData extends Record<string, unknown>, StepData {}

const StepNode = memo(function StepNode({ data }: { data: StepNodeData }) {
  const { step, index, completed, durationSeconds, onToggle, onSeek } = data;
  const framed = Boolean(step.thumbnailUrl || step.frameCaption || step.frameOcr);
  return (
    <div data-slot="vie-step-node" data-view="flow" className="relative">
      <Handle
        type="target"
        position={Position.Top}
        className="!h-2 !w-2 !border-0 !bg-[var(--vie-accent,var(--primary))]"
      />
      <GlassCard
        variant={completed ? 'subtle' : 'default'}
        className={cn('w-[260px] p-3 transition-colors', completed && 'opacity-80')}
      >
        <div className="flex items-start gap-3">
          <StepNumber number={step.number ?? index + 1} completed={completed} />
          <div className="flex-1 min-w-0 space-y-2">
            {step.title && (
              <h4 className={cn('text-sm font-semibold leading-snug', completed && 'line-through')}>
                {step.title}
              </h4>
            )}
            {framed && (
              <VisualEvidence
                variant="figure"
                thumbnailUrl={step.thumbnailUrl}
                caption={step.frameCaption}
                ocr={step.frameOcr}
                sceneType={step.frameSceneType}
                timestamp={step.timestamp}
                onSeek={onSeek}
              />
            )}
            <p
              className={cn(
                'text-xs leading-snug text-muted-foreground line-clamp-3',
                completed && 'line-through',
              )}
            >
              {step.instruction}
            </p>
            <DurationChip seconds={durationSeconds} />
            <CompleteToggle stepNumber={step.number ?? index + 1} index={index} completed={completed} onToggle={onToggle} />
          </div>
        </div>
      </GlassCard>
      <Handle
        type="source"
        position={Position.Bottom}
        className="!h-2 !w-2 !border-0 !bg-[var(--vie-accent,var(--primary))]"
      />
    </div>
  );
});

const NODE_TYPES: NodeTypes = { step: StepNode };

// ─── Map ───

export interface StepFlowMapProps {
  steps: StepItem[];
  completedSteps: ReadonlySet<number>;
  onToggle: (index: number, completed: boolean) => void;
  onSeek?: (seconds: number) => void;
}

/** React Flow node/edge state for the zigzag, kept in sync with the steps and
 *  with the shared completion set: nodes carry `completed`, and the edge
 *  between two complete steps animates. */
function useStepFlowGraph({ steps, completedSteps, onToggle, onSeek }: StepFlowMapProps) {
  const initialNodes = useMemo<Node<StepNodeData>[]>(() => {
    const positions = zigzagLayout(steps.length);
    return steps.map((step, index) => ({
      id: stepNodeId(index),
      type: 'step',
      position: positions[index],
      data: {
        step,
        index,
        completed: false,
        durationSeconds: parseStepDurationSeconds(step.duration),
        onToggle,
        onSeek,
      },
    }));
  }, [steps, onToggle, onSeek]);

  const initialEdges = useMemo<Edge[]>(
    () =>
      steps.slice(1).map((_, index) => ({
        id: `${stepNodeId(index)}->${stepNodeId(index + 1)}`,
        source: stepNodeId(index),
        target: stepNodeId(index + 1),
        type: 'smoothstep',
        animated: false,
        style: { stroke: 'var(--border)', strokeWidth: 1.4 },
      })),
    [steps],
  );

  const [nodes, setNodes, onNodesChange] = useNodesState<Node<StepNodeData>>(initialNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>(initialEdges);

  useEffect(() => setNodes(initialNodes), [initialNodes, setNodes]);
  useEffect(() => setEdges(initialEdges), [initialEdges, setEdges]);

  // Sync per-node `completed` flag + animate edges where both endpoints are
  // complete. Re-runs when completion changes OR when the nodes/edges are
  // rebuilt (steps changed), so a fresh node set picks up existing completion.
  // Only the nodes and edges whose state flips are replaced: the rest keep
  // their identity, and every node keeps the measurements React Flow stored.
  useEffect(() => {
    setNodes((prev) =>
      prev.map((node) => {
        const completed = completedSteps.has(node.data.index);
        return completed === node.data.completed ? node : { ...node, data: { ...node.data, completed } };
      }),
    );
    setEdges((prev) =>
      prev.map((edge) => {
        const sourceIndex = Number(edge.source.replace('step-', ''));
        const targetIndex = Number(edge.target.replace('step-', ''));
        const animated = completedSteps.has(sourceIndex) && completedSteps.has(targetIndex);
        if (animated === edge.animated) return edge;
        return {
          ...edge,
          animated,
          style: animated
            ? { stroke: 'var(--vie-accent, var(--primary))', strokeWidth: 1.8 }
            : { stroke: 'var(--border)', strokeWidth: 1.4 },
        };
      }),
    );
  }, [completedSteps, initialNodes, initialEdges, setNodes, setEdges]);

  return { nodes, edges, onNodesChange, onEdgesChange };
}

/**
 * The flow view of the step canvas: the xyflow zigzag, one node per step,
 * edges between consecutive steps that animate when both endpoints are
 * complete. Opens on the first steps, not the middle of a long procedure.
 * Mounted only while the flow is the chosen view, so React Flow state never
 * runs for the list.
 */
export const StepFlowMap = memo(function StepFlowMap(props: StepFlowMapProps) {
  const { nodes, edges, onNodesChange, onEdgesChange } = useStepFlowGraph(props);
  const startNodeIds = useMemo(
    () => props.steps.slice(0, OPENING_STEPS).map((_, index) => stepNodeId(index)),
    [props.steps],
  );
  // Skeleton covers the blank mount window until React Flow reports it has
  // initialised + measured the viewport (onInit).
  const [canvasReady, setCanvasReady] = useState(false);
  const handleCanvasInit = useCallback(() => setCanvasReady(true), []);
  const canvasHeight = Math.min(720, 280 + props.steps.length * 160);

  return (
    <div className="relative" style={{ height: canvasHeight }}>
      <VieCanvas
        nodes={nodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onInit={handleCanvasInit}
        nodesDraggable={false}
        nodeWidth={STEP_NODE_WIDTH}
        startNodeIds={startNodeIds}
        height={canvasHeight}
      />
      {!canvasReady && (
        <Skeleton aria-hidden="true" className="absolute inset-0 rounded-2xl bg-muted/40 motion-reduce:animate-none" />
      )}
    </div>
  );
});
