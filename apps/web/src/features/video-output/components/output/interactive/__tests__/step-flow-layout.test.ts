import { describe, it, expect } from 'vitest';

import { COLUMN_OFFSET, OPENING_PAD, openingViewport, zigzagLayout } from '../step-flow-layout';

const NODE_WIDTH = 260;
const MIN_ZOOM = 120 / NODE_WIDTH;
/** Flow-space extent of both zigzag columns. */
const SPAN_LEFT = -COLUMN_OFFSET;
const SPAN_RIGHT = COLUMN_OFFSET + NODE_WIDTH;

/** Where a flow-space x lands on screen under a viewport. */
function toScreenX(flowX: number, viewport: { x: number; zoom: number }): number {
  return viewport.x + flowX * viewport.zoom;
}

describe('openingViewport', () => {
  it('should open at 100 % with both columns centred when the canvas is wide enough', () => {
    const canvasWidth = 1000;
    const viewport = openingViewport({ canvasWidth, nodeWidth: NODE_WIDTH, minZoom: MIN_ZOOM });
    expect(viewport.zoom).toBe(1);
    expect(viewport.y).toBe(OPENING_PAD);
    const centre = (toScreenX(SPAN_LEFT, viewport) + toScreenX(SPAN_RIGHT, viewport)) / 2;
    expect(centre).toBeCloseTo(canvasWidth / 2);
  });

  it('should put step 1 at the top of the canvas, inside the padding', () => {
    const viewport = openingViewport({ canvasWidth: 800, nodeWidth: NODE_WIDTH, minZoom: MIN_ZOOM });
    const [first] = zigzagLayout(3);
    expect(viewport.y + first.y * viewport.zoom).toBe(OPENING_PAD);
  });

  it('should zoom out just enough to fit both columns on a phone, above the legibility floor', () => {
    const canvasWidth = 360;
    const viewport = openingViewport({ canvasWidth, nodeWidth: NODE_WIDTH, minZoom: MIN_ZOOM });
    expect(viewport.zoom).toBeLessThan(1);
    expect(viewport.zoom).toBeGreaterThan(MIN_ZOOM);
    expect(toScreenX(SPAN_LEFT, viewport)).toBeCloseTo(OPENING_PAD);
    expect(toScreenX(SPAN_RIGHT, viewport)).toBeCloseTo(canvasWidth - OPENING_PAD);
  });

  it('should never open below the zoom floor, even on a very narrow canvas', () => {
    const viewport = openingViewport({ canvasWidth: 200, nodeWidth: NODE_WIDTH, minZoom: MIN_ZOOM });
    expect(viewport.zoom).toBe(MIN_ZOOM);
  });

  it('should open at 100 % against the start edge when the canvas has no measured width yet', () => {
    const viewport = openingViewport({ canvasWidth: 0, nodeWidth: NODE_WIDTH, minZoom: MIN_ZOOM });
    expect(viewport.zoom).toBe(1);
    expect(viewport.y).toBe(OPENING_PAD);
    expect(toScreenX(SPAN_LEFT, viewport)).toBe(OPENING_PAD);
  });
});
