import { memo, useCallback, useMemo, useState } from 'react';
import { Check, Square, Minus, Plus, ListChecks } from 'lucide-react';
import { cn } from '@/lib/utils';
import { scaleAmount } from '@/features/video-output/components/output/lib/format-utils';
import { GlassCard, FadeIn, Badge } from '@/components/vie';
import { Celebration } from '../Celebration';
import { EmptyTabState } from './EmptyTabState';

import { Button } from '@/components/ui/button';
import { useTabState } from '@/features/video-output/contexts/TabStateContext';
import { useTabCoordination } from '../TabCoordinationContext';

interface ChecklistItem {
  label: string;
  note?: string;
  /** A talk's ask quoted in another language: the note is the verbatim line, this its English (D12, 4.2). */
  quoteEn?: string;
  emoji?: string;
  amount?: number;
  displayAmount?: string;
  unit?: string;
  essential?: boolean;
  group?: string;
}

interface ChecklistInteractiveProps {
  items: ChecklistItem[];
  tabLabel: string;
  groups?: string[];
  scalable?: boolean;
  baseServings?: number;
  tabId?: string;
  nextTab?: string;
  onNavigateTab?: (id: string) => void;
}

export const ChecklistInteractive = memo(function ChecklistInteractive({
  items,
  tabLabel,
  groups,
  scalable,
  baseServings = 1,
  tabId = 'checklist',
  nextTab,
  onNavigateTab,
}: ChecklistInteractiveProps) {
  const [checked, setChecked] = useState<Set<number>>(new Set());
  const [servings, setServings] = useState(baseServings);
  // Screen-reader announcement for toggle feedback — progress is otherwise
  // conveyed only by the color-coded bar and strikethrough styling.
  const [liveMessage, setLiveMessage] = useState('');
  const tabState = useTabState();
  const tabCoord = useTabCoordination();

  const multiplier = baseServings > 0 ? servings / baseServings : 1;

  const toggle = useCallback((index: number) => {
    const willCheck = !checked.has(index);
    const nextCount = checked.size + (willCheck ? 1 : -1);
    const label = items[index]?.label ?? 'Item';
    setLiveMessage(
      willCheck && nextCount === items.length
        ? `${label} checked. All ${items.length} items complete.`
        : `${label} ${willCheck ? 'checked' : 'unchecked'}. ${nextCount} of ${items.length} complete.`,
    );
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(index)) {
        next.delete(index);
        tabState.uncheckItem(tabId, index);
      } else {
        next.add(index);
        tabState.checkItem(tabId, index);
      }
      // Check if all items are now checked
      if (next.size === items.length && items.length > 0) {
        tabCoord?.markTabCompleted(tabId);
      }
      return next;
    });
  }, [checked, items, tabState, tabId, tabCoord]);

  const progress = useMemo(
    () => (items.length > 0 ? Math.round((checked.size / items.length) * 100) : 0),
    [checked.size, items.length],
  );

  const allChecked = items.length > 0 && checked.size === items.length;

  const groupedItems = useMemo(() => {
    if (!groups || groups.length === 0) return [{ group: '', indices: items.map((_, i) => i) }];
    const map = new Map<string, number[]>();
    const ungrouped: number[] = [];
    for (let i = 0; i < items.length; i++) {
      const g = items[i].group ?? '';
      if (g && groups.includes(g)) {
        if (!map.has(g)) map.set(g, []);
        map.get(g)!.push(i);
      } else {
        ungrouped.push(i);
      }
    }
    const result: Array<{ group: string; indices: number[] }> = [];
    for (const g of groups) {
      const indices = map.get(g);
      if (indices && indices.length > 0) result.push({ group: g, indices });
    }
    if (ungrouped.length > 0) result.push({ group: 'Other', indices: ungrouped });
    return result;
  }, [items, groups]);

  if (items.length === 0) return <EmptyTabState message="No checklist items were extracted for this video." icon={ListChecks} />;

  return (
    <GlassCard className="space-y-4">
      {/* Screen-reader toggle feedback */}
      <div role="status" aria-live="polite" className="sr-only">
        {liveMessage}
      </div>

      {/* Progress bar with dynamic color: red → amber → green */}
      <div className="space-y-1.5">
        <div className="flex items-center justify-between text-xs text-muted-foreground">
          <span>{tabLabel}</span>
          <span className="tabular-nums">{progress}%</span>
        </div>
        <div className="h-1.5 w-full rounded-full bg-muted/30 overflow-hidden">
          <div
            className="h-full rounded-full transition-all duration-300 ease-out"
            style={{
              width: `${progress}%`,
              backgroundColor: progress <= 33
                ? 'var(--destructive)'
                : progress <= 66
                  ? 'var(--warning)'
                  : 'var(--success)',
            }}
          />
        </div>
      </div>

      {/* Servings scaler */}
      {scalable && (
        <div className="flex items-center justify-center gap-3 py-1">
          <Button
            variant="outline"
            size="icon"
            onClick={() => setServings((s) => Math.max(1, s - 1))}
            disabled={servings <= 1}
            aria-label="Decrease servings"
          >
            <Minus className="h-3.5 w-3.5" />
          </Button>
          <span className="text-sm font-medium tabular-nums min-w-[80px] text-center">
            {servings} serving{servings !== 1 ? 's' : ''}
          </span>
          <Button
            variant="outline"
            size="icon"
            onClick={() => setServings((s) => s + 1)}
            aria-label="Increase servings"
          >
            <Plus className="h-3.5 w-3.5" />
          </Button>
        </div>
      )}

      {/* Grouped items */}
      {groupedItems.map((section, sIdx) => (
        <div key={sIdx} className="space-y-1">
          {section.group && (
            <h4 className="text-xs font-bold uppercase tracking-wider text-muted-foreground/70 px-3 pt-2">
              {section.group}
            </h4>
          )}
          <ul className="space-y-1" role="list">
            {section.indices.map((index, fadeIdx) => {
              const item = items[index];
              if (!item) return null;
              const isChecked = checked.has(index);
              const displayLabel = scalable && item.amount
                ? `${scaleAmount(item.amount, multiplier)}${item.unit ? ` ${item.unit}` : ''} ${item.label}`
                : item.displayAmount
                  ? `${item.displayAmount} ${item.label}`
                  : item.label;

              // The <li> stays the list's direct child (FadeIn renders a <div>).
              return (
                <li key={index}>
                  <FadeIn index={fadeIdx}>
                    <button
                      type="button"
                      onClick={() => toggle(index)}
                      className={cn(
                        'w-full flex items-start gap-3 rounded-lg px-3 py-2.5 text-start',
                        'transition-all duration-150 active:scale-[0.98]',
                        'hover:bg-muted/30',
                        isChecked && 'opacity-60',
                      )}
                      aria-pressed={isChecked}
                    >
                      <span className="mt-0.5 shrink-0 text-[var(--vie-accent)]" aria-hidden="true">
                        {isChecked ? <Check className="h-4 w-4" /> : <Square className="h-4 w-4" />}
                      </span>
                      <div className="flex-1 min-w-0">
                        <span className={cn(
                          'text-sm font-medium',
                          isChecked && 'line-through text-muted-foreground',
                        )}>
                          {item.emoji && <span className="me-1.5 text-base" aria-hidden="true">{item.emoji}</span>}
                          {displayLabel}
                        </span>
                        {item.essential && !isChecked && (
                          <Badge variant="warning" className="ms-2 text-xs">essential</Badge>
                        )}
                        {item.note && (
                          <p dir="auto" className={cn(
                            'text-xs text-muted-foreground mt-0.5',
                            isChecked && 'line-through',
                          )}>{item.note}</p>
                        )}
                        {item.quoteEn && (
                          <p lang="en" dir="ltr" className={cn(
                            'text-xs text-muted-foreground/80 mt-0.5',
                            isChecked && 'line-through',
                          )}>{item.quoteEn}</p>
                        )}
                      </div>
                    </button>
                  </FadeIn>
                </li>
              );
            })}
          </ul>
        </div>
      ))}

      {/* Celebration */}
      {allChecked && (
        <Celebration
          emoji="🎉"
          title="All done!"
          subtitle={`You completed all ${items.length} items`}
          nextTabId={nextTab}
          nextLabel={nextTab ? 'Continue' : undefined}
          onNavigateTab={onNavigateTab}
        />
      )}

    </GlassCard>
  );
});
