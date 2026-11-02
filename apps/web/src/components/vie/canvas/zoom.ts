// ─── Zoom floor ───

/** Narrowest a node card may render on screen, in px (R8: nodes stay legible). */
export const MIN_NODE_PX = 120;
export const DEFAULT_MIN_ZOOM = 0.2;

/**
 * Zoom floor for a canvas whose narrowest node card is `nodeWidth` px wide:
 * the requested floor, raised wherever it would let that card shrink under
 * `MIN_NODE_PX`. `fitView` honours the floor, so a wide layout overflows the
 * viewport (pannable) instead of collapsing into a band of specks. Opt-in:
 * without a `nodeWidth` the requested floor stands.
 */
export function clampMinZoom(nodeWidth: number | undefined, requested: number): number {
  if (nodeWidth === undefined || !(nodeWidth > 0)) return requested;
  return Math.max(requested, MIN_NODE_PX / nodeWidth);
}
