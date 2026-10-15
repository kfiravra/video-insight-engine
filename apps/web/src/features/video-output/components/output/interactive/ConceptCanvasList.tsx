import { memo, useCallback, useEffect, useId, useMemo, useRef, type ReactNode } from 'react';

import { Button } from '@/components/ui/button';
import { GlassCard, SeekChip } from '@/components/vie';
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
  onToggle: (id: string) => void;
  onSeek?: (seconds: number) => void;
  /** Hands the name button to the list, which moves focus between rows. */
  registerNameButton: (id: string, button: HTMLButtonElement | null) => void;
  /** Id of the region the open inspector renders in (the name's aria-controls). */
  detailsId: string;
  /** The inspector — passed to the selected row only, so the others stay memoised. */
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
 * is the only control: a disclosure that opens the inspector (frame evidence
 * + connected concepts) right under it, and closes it again. Long names wrap.
 */
const ConceptRow = memo(function ConceptRow({
  id,
  concept,
  selected,
  onToggle,
  onSeek,
  registerNameButton,
  detailsId,
  inspector,
}: ConceptRowProps) {
  const { timestamp } = concept;
  const nameRef = useCallback(
    (button: HTMLButtonElement | null) => registerNameButton(id, button),
    [id, registerNameButton],
  );
  return (
    <li
      className={cn(
        'scroll-my-3 rounded-lg px-2 py-2 transition-colors',
        selected ? 'bg-[color:var(--vie-accent,var(--primary))]/10' : 'hover:bg-muted/30',
      )}
    >
      <div className="flex items-start gap-2">
        <span className="pt-0.5 text-base leading-none" aria-hidden="true">
          {concept.emoji || '💡'}
        </span>
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex items-start gap-2">
            <Button
              ref={nameRef}
              variant="ghost"
              size="bare"
              onClick={() => onToggle(id)}
              aria-expanded={selected}
              aria-controls={selected ? detailsId : undefined}
              className="min-w-0 flex-1 shrink justify-start whitespace-normal text-start text-sm font-medium text-foreground hover:bg-transparent hover:text-foreground hover:underline dark:hover:bg-transparent"
            >
              <span className="min-w-0 break-words">{concept.name}</span>
            </Button>
            {timestamp != null && <SeekChip seconds={timestamp} onSeek={onSeek} className="shrink-0" />}
          </div>
          <p className="text-sm leading-snug text-foreground/90">{concept.definition}</p>
          {concept.example && <LabelledLine label="Example" text={concept.example} />}
          {concept.analogy && <LabelledLine label="Analogy" text={concept.analogy} />}
        </div>
      </div>
      {inspector && <div id={detailsId}>{inspector}</div>}
    </li>
  );
});

// ─── Focus ───

/** Focus fell to the page — its element unmounted — rather than moving on. */
function focusWasDropped(): boolean {
  const active = document.activeElement;
  return active == null || active === document.body;
}

/**
 * Keeps focus with the concept the inline inspector belongs to. The inspector
 * moves to another row when a connected concept is chosen, and unmounts on
 * close, dropping focus to <body>. So a newly selected concept's name takes
 * focus and its row scrolls into view; on close the name of the concept that
 * was open takes focus back — unless focus already moved elsewhere (Escape is
 * window-wide). Mounting with a selection (back from the map) moves nothing.
 */
function useSelectionFocus(selectedId: string | null): (id: string, button: HTMLButtonElement | null) => void {
  const nameButtons = useRef(new Map<string, HTMLButtonElement>());
  const previousId = useRef(selectedId);

  useEffect(() => {
    const previous = previousId.current;
    previousId.current = selectedId;
    if (selectedId === previous) return;
    if (selectedId) {
      const button = nameButtons.current.get(selectedId);
      button?.focus({ preventScroll: true });
      (button?.closest('li') ?? button)?.scrollIntoView({ block: 'nearest' });
    } else if (previous && focusWasDropped()) {
      nameButtons.current.get(previous)?.focus();
    }
  }, [selectedId]);

  return useCallback((id: string, button: HTMLButtonElement | null) => {
    if (button) nameButtons.current.set(id, button);
    else nameButtons.current.delete(id);
  }, []);
}

// ─── List ───

export interface ConceptCanvasListProps {
  model: ConceptGraphModel;
  selectedId: string | null;
  /** A concept's name was activated: open its details, or close them if open. */
  onToggle: (id: string) => void;
  onSeek?: (seconds: number) => void;
  /** The inspector for the selected concept — placed inline under its row. */
  inspector?: ReactNode;
}

/** Group-sectioned concept list — the default view of the concept canvas on every viewport (D8). */
export const ConceptCanvasList = memo(function ConceptCanvasList({
  model,
  selectedId,
  onToggle,
  onSeek,
  inspector,
}: ConceptCanvasListProps) {
  const registerNameButton = useSelectionFocus(selectedId);
  const listId = useId();
  const detailsId = `${listId}-details`;
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
                  onToggle={onToggle}
                  onSeek={onSeek}
                  registerNameButton={registerNameButton}
                  detailsId={detailsId}
                  inspector={node.id === selectedId ? inspector : undefined}
                />
              ))}
            </ul>
          </GlassCard>
        );
      })}
    </div>
  );
});
