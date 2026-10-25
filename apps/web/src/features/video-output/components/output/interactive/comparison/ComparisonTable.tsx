import { Check } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useLabels } from '@/lib/i18n';
import { GlassCard, Badge } from '@/components/vie';

import { groupByCompetitor, hasMultipleCompetitors } from './competitor-groups';
import type { ComparisonRow } from './types';

interface ComparisonTableProps {
  comparisons: ComparisonRow[];
  colLeft: string;
  colRight: string;
  activeRow: number;
  onSelectRow: (index: number) => void;
}

interface RowsTableProps {
  rows: { row: ComparisonRow; index: number }[];
  colLeft: string;
  colRight: string;
  activeRow: number;
  onSelectRow: (index: number) => void;
}

function RowsTable({ rows, colLeft, colRight, activeRow, onSelectRow }: RowsTableProps) {
  return (
    <GlassCard variant="default" className="p-0 overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border/50">
              <th className="text-start text-xs font-semibold uppercase tracking-wider text-muted-foreground px-4 py-2.5">Feature</th>
              <th className="text-start text-xs font-semibold uppercase tracking-wider text-[var(--vie-accent)] px-4 py-2.5">{colLeft}</th>
              <th className="text-start text-xs font-semibold uppercase tracking-wider text-muted-foreground px-4 py-2.5">{colRight}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ row: item, index }) => {
              const isLeftWinner = item.winner === 'left';
              const isRightWinner = item.winner === 'right';
              return (
                <tr
                  key={index}
                  className={cn(
                    'border-b border-border/30 cursor-pointer transition-colors',
                    index === activeRow ? 'bg-[var(--vie-accent)]/5' : 'hover:bg-muted/30',
                  )}
                  onClick={() => onSelectRow(index)}
                >
                  <td className="px-4 py-2.5 text-sm font-semibold leading-snug">
                    {item.feature}
                    {item.winner === 'tie' && (
                      <Badge variant="muted" className="ms-2 text-xs font-medium">Tie</Badge>
                    )}
                  </td>
                  <td className={cn(
                    'px-4 py-2.5 text-sm leading-relaxed',
                    isLeftWinner ? 'text-success font-semibold' : 'text-muted-foreground',
                  )}>
                    {item.thisProduct}
                    {isLeftWinner && <Check className="inline h-3.5 w-3.5 ms-1 text-success" aria-hidden="true" />}
                  </td>
                  <td className={cn(
                    'px-4 py-2.5 text-sm leading-relaxed',
                    isRightWinner ? 'text-success font-semibold' : 'text-muted-foreground',
                  )}>
                    {item.competitor}
                    {isRightWinner && <Check className="inline h-3.5 w-3.5 ms-1 text-success" aria-hidden="true" />}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </GlassCard>
  );
}

/**
 * Feature comparison — table mode. One competitor → one table. Two or more →
 * one table per competitor under a "vs {name}" eyebrow, so no cell is empty
 * and the page stays readable at phone width.
 */
export function ComparisonTable({
  comparisons,
  colLeft,
  colRight,
  activeRow,
  onSelectRow,
}: ComparisonTableProps) {
  const t = useLabels();
  if (!hasMultipleCompetitors(comparisons)) {
    const rows = comparisons.map((row, index) => ({ row, index }));
    return <RowsTable rows={rows} colLeft={colLeft} colRight={colRight} activeRow={activeRow} onSelectRow={onSelectRow} />;
  }
  return (
    <div className="space-y-4">
      {groupByCompetitor(comparisons).map((group) => (
        <section key={group.name} className="space-y-2">
          {group.name && (
            <h4 className="type-eyebrow">{t.versusName.replace('{name}', group.name)}</h4>
          )}
          <RowsTable
            rows={group.rows}
            colLeft={colLeft}
            colRight={group.name || colRight}
            activeRow={activeRow}
            onSelectRow={onSelectRow}
          />
        </section>
      ))}
    </div>
  );
}
