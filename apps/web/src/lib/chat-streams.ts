/**
 * Live assistant chat streams, so sign-out can abort them from outside the
 * hook that owns them. `useSidebarChat` registers the controller of every turn
 * it sends; a controller leaves the set when it aborts or when the turn ends.
 */
const liveChatStreams = new Set<AbortController>();

export function registerChatStream(controller: AbortController): void {
  liveChatStreams.add(controller);
  controller.signal.addEventListener(
    "abort",
    () => {
      liveChatStreams.delete(controller);
    },
    { once: true },
  );
}

export function releaseChatStream(controller: AbortController): void {
  liveChatStreams.delete(controller);
}

/** Abort every chat turn still streaming. Called on sign-out. */
export function abortChatStreams(): void {
  for (const controller of liveChatStreams) controller.abort();
  liveChatStreams.clear();
}

/** Test-only: how many turns are registered. */
export function _getChatStreamCount(): number {
  return liveChatStreams.size;
}
