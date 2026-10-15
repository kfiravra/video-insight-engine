import type { ReviewComparison } from '@vie/types';

// ─── Radar scoring (merged from the retired ComparisonRadar) ───

const RADAR_MIN_AXES = 3;
export const DEFAULT_WEIGHT = 5;
export const MAX_SCORE = 10;

export interface AxisScore {
  feature: string;
  left: number;
  right: number;
}

/**
 * A cell that IS a number — an optional currency/approx prefix, the number,
 * an optional short unit: "4,422 mAh", "$249", "6.1 in", "48". A sentence that
 * merely contains a digit ("Sony WF-1000XM4", "AirPods Pro 2 — placed…") is
 * not an axis. Mirrors `_NUMERIC_CELL` in the summarizer's assembly/promotion.py.
 */
const NUMERIC_CELL = /^[$€£¥₪~≈<>]?\s*[-+]?\d[\d,]*(?:\.\d+)?\s*[A-Za-z%°µ/]{0,8}$/;
const NUMBER_TOKEN = /[-+]?\d[\d,]*(?:\.\d+)?/;

function parseNumeric(value: string | number | undefined): number | null {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null;
  const text = value?.trim();
  if (!text || !NUMERIC_CELL.test(text)) return null;
  const match = text.match(NUMBER_TOKEN);
  if (!match) return null;
  const n = Number(match[0].replace(/,/g, ''));
  return Number.isFinite(n) ? n : null;
}

function clamp01(n: number): number {
  if (!Number.isFinite(n)) return 0;
  if (n <= 0) return 0;
  if (n >= 1) return 1;
  return n;
}

/**
 * The numeric axes of a comparison: rows whose BOTH sides are numbers, scaled
 * so the larger side is 10. Rows without numbers on both sides are not axes —
 * a radar spoke was never invented from `winner` (output-quality R10).
 * Exported for unit testing.
 */
export function scoreComparisonAxes(comparisons: ReviewComparison[]): AxisScore[] {
  const axes: AxisScore[] = [];
  for (const row of comparisons) {
    const left = parseNumeric(row.thisProduct);
    const right = parseNumeric(row.competitor);
    if (left == null || right == null) continue;
    const max = Math.max(Math.abs(left), Math.abs(right), 1);
    axes.push({
      feature: row.feature,
      left: clamp01(left / max) * MAX_SCORE,
      right: clamp01(right / max) * MAX_SCORE,
    });
  }
  return axes;
}

/** The radar needs ≥3 numeric axes; it degenerates below 3 spokes and lies without numbers. */
export function hasRadarAxes(comparisons: ReviewComparison[]): boolean {
  return scoreComparisonAxes(comparisons).length >= RADAR_MIN_AXES;
}
