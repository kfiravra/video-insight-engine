import type { ComparisonRow } from './types';

/** One competitor's rows. `index` is the row's position in the full list, so a
 *  table split per competitor still selects the right row. */
export interface CompetitorGroup {
  name: string;
  rows: { row: ComparisonRow; index: number }[];
}

/**
 * Group comparison rows by `competitorName` in first-seen order. Names match as
 * the assembler's `_competitor_names` does — trimmed, case-insensitive ("Sony"
 * and "sony" are one) — and a group keeps its first-seen spelling. Rows without
 * a name keep a group of their own — never dropped.
 */
export function groupByCompetitor(comparisons: ComparisonRow[]): CompetitorGroup[] {
  const groups = new Map<string, CompetitorGroup>();
  comparisons.forEach((row, index) => {
    const name = row.competitorName?.trim() ?? '';
    const key = name.toLowerCase();
    const group = groups.get(key) ?? { name, rows: [] };
    group.rows.push({ row, index });
    groups.set(key, group);
  });
  return [...groups.values()];
}

/** Two or more named competitors → the page reads as one table per competitor. */
export function hasMultipleCompetitors(comparisons: ComparisonRow[]): boolean {
  return groupByCompetitor(comparisons).filter((g) => g.name).length >= 2;
}
