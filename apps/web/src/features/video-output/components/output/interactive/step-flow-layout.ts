import type { Viewport } from '@xyflow/react';

export const COLUMN_OFFSET = 180;
export const ROW_SPACING = 160;

/** Vertical zigzag layout — alternates between left and right of center. */
export function zigzagLayout(count: number): Array<{ x: number; y: number }> {
  return Array.from({ length: count }, (_, index) => ({
    x: index % 2 === 0 ? -COLUMN_OFFSET : COLUMN_OFFSET,
    y: index * ROW_SPACING,
  }));
}

// ─── Opening view ───

/** Breathing room between the canvas edge and the opening frame, in px. */
export const OPENING_PAD = 24;

export interface OpeningViewportInput {
  /** The canvas element's rendered width, in px. */
  canvasWidth: number;
  /** Design width of a step node card, in px. */
  nodeWidth: number;
  /** The canvas zoom floor — never opened below it. */
  minZoom: number;
}

/**
 * The viewport the step flow opens on: step 1 at the top of the canvas, both
 * zigzag columns centred, at the largest zoom that fits them — capped at
 * 100 % so cards never blow up, floored at `minZoom` so they stay legible.
 * Reading then runs down the zigzag in step order; the whole-graph fit stays
 * on the controls' fit button. A pure function of the layout constants, so
 * no node measurement is needed before the first paint.
 */
export function openingViewport({ canvasWidth, nodeWidth, minZoom }: OpeningViewportInput): Viewport {
  // Flow-space extent of both columns: the left card's left edge to the right card's right edge.
  const spanLeft = -COLUMN_OFFSET;
  const spanWidth = 2 * COLUMN_OFFSET + nodeWidth;
  // No measured width yet (a container that has not laid out): there is no
  // centre to aim for, so open at 100 % with the span against the start edge —
  // still step 1 at the top, still readable.
  if (!(canvasWidth > 0)) {
    return { x: OPENING_PAD - spanLeft, y: OPENING_PAD, zoom: 1 };
  }
  const usable = Math.max(0, canvasWidth - 2 * OPENING_PAD);
  const zoom = Math.min(1, Math.max(minZoom, usable / spanWidth));
  const spanCenter = spanLeft + spanWidth / 2;
  return {
    x: canvasWidth / 2 - spanCenter * zoom,
    y: OPENING_PAD,
    zoom,
  };
}
