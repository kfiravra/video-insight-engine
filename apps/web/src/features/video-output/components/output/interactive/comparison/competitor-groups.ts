import { hasRadarAxes } from './radar-scoring';
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

/** One radar: its rows, and the competitor its second series is — empty for
 *  a page with a single opponent, which keeps the page's column label. */
export interface RadarSet {
  name: string;
  rows: ComparisonRow[];
}

/**
 * The radars a comparison can draw honestly. One opponent → one radar when the
 * rows hold ≥ 3 numeric axes. Two or more → one radar per competitor on its own
 * rows, like the tables and cards — and none when any competitor lacks three
 * axes: a pooled "Competitor" series plots one spoke per rival and crowns a
 * product that does not exist (D13).
 */
export function radarSets(comparisons: ComparisonRow[]): RadarSet[] {
  if (!hasMultipleCompetitors(comparisons)) {
    return hasRadarAxes(comparisons) ? [{ name: '', rows: comparisons }] : [];
  }
  const groups = groupByCompetitor(comparisons).map((group) => ({ name: group.name, rows: group.rows.map((r) => r.row) }));
  return groups.every((group) => group.name && hasRadarAxes(group.rows)) ? groups : [];
}
