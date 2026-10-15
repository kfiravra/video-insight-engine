import { memo, useCallback, useEffect, useMemo, useState } from 'react';
import {
  Handle,
  Position,
  useEdgesState,
  useNodesState,
  type Edge,
  type Node,
  type NodeTypes,
} from '@xyflow/react';

import { AlertTriangle, Clock, Lightbulb, Workflow } from 'lucide-react';

import { CanvasViewToggle, VieCanvas } from '@/components/vie/canvas/CanvasShell';
import { GlassCard, TextBlock, Timestamp, VisualEvidence } from '@/components/vie';
import { EmptyTabState } from './EmptyTabState';
import { zigzagLayout } from './step-flow-layout';
import { Checkbox } from '@/components/ui/checkbox';
import { Skeleton } from '@/components/ui/skeleton';
import { cn } from '@/lib/utils';
import { useTabState } from '@/features/video-output/contexts/TabStateContext';
import { parseStepDurationSeconds } from '@/features/video-output/lib/step-duration';
import type { StepItem } from '@vie/types';

// ─── Props ───

interface StepFlowCanvasProps {
  steps: StepItem[];
  onSeek?: (seconds: number) => void;
  tabId?: string;
}

type StepView = 'list' | 'flow';
const VIEW_OPTIONS = [
  { value: 'list', label: 'List' },
  { value: 'flow', label: 'Flow' },
] as const satisfies ReadonlyArray<{ value: StepView; label: string }>;

/** Design width of a step node card; the canvas zoom floor keeps it legible. */
const STEP_NODE_WIDTH = 260;

// ─── Helpers ───

function stepNodeId(index: number): string {
  return `step-${index}`;
}

/** Compact human label for the step-duration chip — "5 min" / "45s" / "1h 10m". */
function formatDurationLabel(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min`;
  const hrs = Math.floor(minutes / 60);
  const rem = minutes % 60;
  return rem === 0 ? `${hrs}h` : `${hrs}h ${rem}m`;
}

function hasFrame(step: StepItem): boolean {
  return Boolean(step.thumbnailUrl || step.frameCaption || step.frameOcr);
}

// ─── Shared step chrome (node card + list row) ───

interface StepData {
  step: StepItem;
  index: number;
  completed: boolean;
  durationSeconds: number;
  onToggle: (index: number, completed: boolean) => void;
  onSeek?: (seconds: number) => void;
}

function StepNumber({ number, completed }: { number: number; completed: boolean }) {
  return (
    <span
      className={cn(
        'inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full border text-xs font-bold tabular-nums',
        completed
          ? 'border-success bg-success text-success-foreground'
          : 'border-[color:var(--vie-accent,var(--primary))]/50 text-[color:var(--vie-accent,var(--primary))]',
      )}
      aria-hidden="true"
    >
      {number}
    </span>
  );
}

function DurationChip({ seconds }: { seconds: number }) {
  if (seconds <= 0) return null;
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-muted/40 px-2 py-0.5 text-[11px] font-medium tabular-nums text-muted-foreground">
      <Clock className="h-3 w-3 shrink-0" aria-hidden="true" />
      {formatDurationLabel(seconds)}
    </span>
  );
}

function CompleteToggle({ step, index, completed, onToggle }: StepData) {
  const stepNumber = step.number ?? index + 1;
  return (
    <label className="flex items-center gap-2 pt-1 text-xs font-medium text-foreground/80">
      <Checkbox
        checked={completed}
        onCheckedChange={() => onToggle(index, completed)}
        aria-label={completed ? `Mark step ${stepNumber} incomplete` : `Mark step ${stepNumber} complete`}
      />
      <span>{completed ? 'Done' : 'Mark complete'}</span>
    </label>
  );
}

// ─── Node ───

interface StepNodeData extends Record<string, unknown>, StepData {}

const StepNode = memo(function StepNode({ data }: { data: StepNodeData }) {
  const { step, index, completed, durationSeconds, onSeek } = data;
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
            {hasFrame(step) && (
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
            <CompleteToggle {...data} />
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

// ─── List row ───

/**
 * One step with its whole instruction, tips and safety note inline, plus the
 * duration and seek chips — the first screen reads without a click (R8).
 * Carries the same `data-slot` as the flow node: it is the step node, listed.
 */
const StepRow = memo(function StepRow(data: StepData) {
  const { step, index, completed, durationSeconds, onSeek } = data;
  const { timestamp } = step;
  const framed = hasFrame(step);
  return (
    <li data-slot="vie-step-node" data-view="list">
      <GlassCard variant={completed ? 'subtle' : 'default'} className={cn('p-3', completed && 'opacity-80')}>
        <div className="flex items-start gap-3">
          <StepNumber number={step.number ?? index + 1} completed={completed} />
          <div className="flex-1 min-w-0 space-y-2">
            {step.title && (
              <h4 className={cn('text-sm font-semibold leading-snug', completed && 'line-through')}>
                {step.title}
              </h4>
            )}
            <p className={cn('text-sm leading-snug text-foreground/90', completed && 'line-through')}>
              {step.instruction}
            </p>
            <div className="flex flex-wrap items-center gap-2">
              <DurationChip seconds={durationSeconds} />
              {!framed && timestamp != null && (
                <Timestamp seconds={timestamp} onClick={onSeek ? () => onSeek(timestamp) : undefined} />
              )}
            </div>
            {framed && (
              <VisualEvidence
                variant="compact"
                thumbnailUrl={step.thumbnailUrl}
                caption={step.frameCaption}
                ocr={step.frameOcr}
                sceneType={step.frameSceneType}
                timestamp={timestamp}
                onSeek={onSeek}
              />
            )}
            {step.tips && (
              <TextBlock intent="tip" icon={<Lightbulb />}>
                {step.tips}
              </TextBlock>
            )}
            {step.safetyNote && (
              <TextBlock intent="warning" icon={<AlertTriangle />}>
                {step.safetyNote}
              </TextBlock>
            )}
            <CompleteToggle {...data} />
          </div>
        </div>
      </GlassCard>
    </li>
  );
});

// ─── Canvas ───

/**
 * Step-by-step recipe / tutorial flow. Opens as a list on every viewport —
 * full instruction, tips, safety note and seek chip per step (R8 / D8); the
 * xyflow zigzag is a toggle. Both views share one completion state through
 * `useTabState`, so this tab and StepByStepInteractive stay in sync.
 *
 * In the flow view, edges between consecutive steps animate when both
 * endpoints are complete.
 */
export const StepFlowCanvas = memo(function StepFlowCanvas({
  steps,
  onSeek,
  tabId = 'steps',
}: StepFlowCanvasProps) {
  const tabState = useTabState();
  // Completion lives in the shared TabStateContext (single source of truth) so
  // this canvas and StepByStepInteractive stay in sync, and pre-existing
  // completions are reflected the moment the canvas mounts. `completedSteps`
  // gets a fresh identity on every change, which drives the sync effect below.
  const completedSteps = tabState.completedSteps;
  const [view, setView] = useState<StepView>('list');

  const toggleComplete = useCallback(
    (index: number, isCompleted: boolean) => {
      if (isCompleted) {
        tabState.uncompleteStep(index);
      } else {
        tabState.completeStep(index);
      }
    },
    [tabState],
  );

  const initialNodes = useMemo<Node<StepNodeData>[]>(() => {
    if (!steps?.length) return [];
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
        onToggle: toggleComplete,
        onSeek,
      },
    }));
    // toggleComplete is stable per render of this hook because tabState is
    // memoized; onSeek changes infrequently. We re-create nodes only when the
    // step shape changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [steps, onSeek]);

  const initialEdges = useMemo<Edge[]>(() => {
    if (!steps?.length) return [];
    return steps.slice(1).map((_, index) => {
      const sourceIndex = index;
      const targetIndex = index + 1;
      return {
        id: `${stepNodeId(sourceIndex)}->${stepNodeId(targetIndex)}`,
        source: stepNodeId(sourceIndex),
        target: stepNodeId(targetIndex),
        type: 'smoothstep',
        animated: false,
        style: { stroke: 'var(--border)', strokeWidth: 1.4 },
      };
    });
  }, [steps]);

  const [nodes, setNodes, onNodesChange] = useNodesState<Node<StepNodeData>>(initialNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>(initialEdges);

  // Skeleton covers the blank mount window until React Flow reports it has
  // initialised + measured the viewport (onInit).
  const [canvasReady, setCanvasReady] = useState(false);
  const handleCanvasInit = useCallback(() => setCanvasReady(true), []);

  useEffect(() => {
    setNodes(initialNodes);
  }, [initialNodes, setNodes]);

  useEffect(() => {
    setEdges(initialEdges);
  }, [initialEdges, setEdges]);

  // Sync per-node `completed` flag + animate edges where both endpoints are
  // complete. Re-runs when completion changes OR when the nodes/edges are
  // rebuilt (steps changed), so a fresh node set picks up existing completion.
  useEffect(() => {
    setNodes((prev) =>
      prev.map((node) => ({
        ...node,
        data: {
          ...node.data,
          completed: completedSteps.has(node.data.index),
        },
      })),
    );
    setEdges((prev) =>
      prev.map((edge) => {
        const sourceIndex = Number(edge.source.replace('step-', ''));
        const targetIndex = Number(edge.target.replace('step-', ''));
        const animated = completedSteps.has(sourceIndex) && completedSteps.has(targetIndex);
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

  if (!steps?.length) {
    return <EmptyTabState message="No steps were extracted for this video." icon={Workflow} />;
  }

  const canvasHeight = Math.min(720, 280 + steps.length * 160);
  const progress = `${completedSteps.size}/${steps.length} done`;

  return (
    <div className="space-y-3" data-tab-id={tabId}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs font-medium tabular-nums text-muted-foreground">{progress}</p>
        <CanvasViewToggle label="Steps view" value={view} options={VIEW_OPTIONS} onChange={setView} />
      </div>

      {view === 'flow' ? (
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
        <ol className="space-y-2" aria-label="Steps">
          {nodes.map((node) => (
            <StepRow key={node.id} {...node.data} />
          ))}
        </ol>
      )}
    </div>
  );
});
