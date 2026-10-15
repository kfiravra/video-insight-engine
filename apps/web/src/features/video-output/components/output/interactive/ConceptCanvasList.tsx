import { memo, useMemo, type ReactNode } from 'react';

import { GlassCard, Timestamp } from '@/components/vie';
import { cn } from '@/lib/utils';
import type { ConceptItem } from '@vie/types';

import type { ConceptGraphModel } from './concept-graph';

// ─── Group key ───

const GROUP_DOT_CLASSES = [
  'bg-foreground/30',
  'bg-foreground/45',
  'bg-foreground/60',
  'bg-foreground/75',
  'bg-foreground/90',
];

/** Lightness-only group marker — groups differ by tone, never by hue. */
export function GroupDot({ index, className }: { index: number; className?: string }) {
  return (
    <span
      className={cn('h-2.5 w-2.5 shrink-0 rounded-full', GROUP_DOT_CLASSES[index % GROUP_DOT_CLASSES.length], className)}
      aria-hidden="true"
    />
  );
}

// ─── Row ───

interface ConceptRowProps {
  id: string;
  concept: ConceptItem;
  selected: boolean;
  onSelect: (id: string) => void;
  onSeek?: (seconds: number) => void;
  /** The inspector, rendered under the row while it is the selected one. */
  inspector?: ReactNode;
}

function LabelledLine({ label, text }: { label: string; text: string }) {
  return (
    <p className="text-xs leading-snug text-muted-foreground">
      <span className="me-1 text-[10px] font-semibold uppercase tracking-wider text-foreground/70">
        {label}
      </span>
      {text}
    </p>
  );
}

/**
 * One concept with its substance inline — definition, example, analogy and
 * the seek chip — so the first screen reads without a click (R8). The name
 * is the only control: it selects the concept and opens the inspector
 * (frame evidence + connected concepts) right under it.
 */
const ConceptRow = memo(function ConceptRow({ id, concept, selected, onSelect, onSeek, inspector }: ConceptRowProps) {
  const { timestamp } = concept;
  return (
    <li
      className={cn(
        'rounded-lg px-2 py-2 transition-colors',
        selected ? 'bg-[color:var(--vie-accent,var(--primary))]/10' : 'hover:bg-muted/30',
      )}
    >
      <div className="flex items-start gap-2">
        <span className="pt-0.5 text-base leading-none" aria-hidden="true">
          {concept.emoji || '💡'}
        </span>
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => onSelect(id)}
              aria-pressed={selected}
              className="min-w-0 flex-1 truncate text-start text-sm font-medium text-foreground outline-none hover:underline focus-visible:underline"
            >
              {concept.name}
            </button>
            {timestamp != null && (
              <Timestamp
                seconds={timestamp}
                onClick={onSeek ? () => onSeek(timestamp) : undefined}
                className="shrink-0"
              />
            )}
          </div>
          <p className="text-sm leading-snug text-foreground/90">{concept.definition}</p>
          {concept.example && <LabelledLine label="Example" text={concept.example} />}
          {concept.analogy && <LabelledLine label="Analogy" text={concept.analogy} />}
        </div>
      </div>
      {selected && inspector}
    </li>
  );
});

// ─── List ───

export interface ConceptCanvasListProps {
  model: ConceptGraphModel;
  selectedId: string | null;
  onSelect: (id: string) => void;
  onSeek?: (seconds: number) => void;
  /** The inspector for the selected concept — placed inline under its row. */
  inspector?: ReactNode;
}

/** Group-sectioned concept list — the default view of the concept canvas on every viewport (D8). */
export const ConceptCanvasList = memo(function ConceptCanvasList({
  model,
  selectedId,
  onSelect,
  onSeek,
  inspector,
}: ConceptCanvasListProps) {
  const byGroup = useMemo(() => {
    const map = new Map<string, ConceptGraphModel['nodes']>();
    for (const group of model.groups) map.set(group, []);
    for (const node of model.nodes) {
      const list = map.get(node.group) ?? [];
      list.push(node);
      map.set(node.group, list);
    }
    return map;
  }, [model]);

  return (
    <div className={cn('grid gap-3', model.groups.length > 1 && 'md:grid-cols-2')} data-slot="concept-list">
      {model.groups.map((group, groupIndex) => {
        const members = byGroup.get(group) ?? [];
        if (members.length === 0) return null;
        return (
          <GlassCard key={group} variant="default" className="space-y-2 p-3">
            <header className="flex items-center gap-2">
              <GroupDot index={groupIndex} />
              <h4 className="flex-1 text-sm font-semibold text-foreground">{group}</h4>
              <span className="text-xs tabular-nums text-muted-foreground">{members.length}</span>
            </header>
            <ul className="space-y-1">
              {members.map((node) => (
                <ConceptRow
                  key={node.id}
                  id={node.id}
                  concept={node.concept}
                  selected={node.id === selectedId}
                  onSelect={onSelect}
                  onSeek={onSeek}
                  inspector={inspector}
                />
              ))}
            </ul>
          </GlassCard>
        );
      })}
    </div>
  );
});
