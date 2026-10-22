import type { ComparisonRow } from './types';

/** One competitor's rows. `index` is the row's position in the full list, so a
 *  table split per competitor still selects the right row. */
export interface CompetitorGroup {
  name: string;
  rows: { row: ComparisonRow; index: number }[];
}

/**
 * Group comparison rows by `competitorName` in first-seen order. Rows without a
 * name keep a group of their own — never dropped.
 */
export function groupByCompetitor(comparisons: ComparisonRow[]): CompetitorGroup[] {
  const groups = new Map<string, CompetitorGroup>();
  comparisons.forEach((row, index) => {
    const name = row.competitorName?.trim() ?? '';
    const group = groups.get(name) ?? { name, rows: [] };
    group.rows.push({ row, index });
    groups.set(name, group);
  });
  return [...groups.values()];
}

/** Two or more named competitors → the page reads as one table per competitor. */
export function hasMultipleCompetitors(comparisons: ComparisonRow[]): boolean {
  return groupByCompetitor(comparisons).filter((g) => g.name).length >= 2;
}
