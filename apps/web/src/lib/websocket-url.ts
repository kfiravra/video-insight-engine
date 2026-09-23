/**
 * Builds the WebSocket endpoint URL from the configured base.
 *
 * Documented `VITE_WS_URL` values historically included the `/ws` path while
 * the hook also appended it, producing `/ws/ws`. Accept both shapes so any
 * existing env value resolves to exactly one `/ws` suffix.
 */
export function buildWebSocketUrl(base: string): string {
  const withoutTrailingSlash = base.trim().replace(/\/+$/, "");
  const origin = withoutTrailingSlash.replace(/\/ws$/, "");
  return `${origin}/ws`;
}
