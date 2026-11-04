import { queryClient } from "@/lib/query-client";
import { abortChatStreams } from "@/lib/chat-streams";
import { useChatStore } from "@/stores/chat-store";
import { useUIStore } from "@/stores/ui-store";
import { useProcessingStore } from "@/features/video-output/stores/processing-store";
import { abortAllStreams } from "@/features/video-output/lib/streaming/stream-registry";

/**
 * Drop everything on the client that belongs to the signed-in user: the
 * React Query cache (and the fetches still filling it), the chat transcript,
 * the folder and favourite state the UI store persists, and every open
 * pipeline or chat stream. Runs on sign-out and again right before a new
 * session is established, so no user is ever served another user's cache.
 *
 * Layout preferences (sidebar width, text size, sort, theme) are not user
 * data and survive.
 */
export function resetClientSession(): void {
  // Cancel first so an in-flight response can't land in the cache after it
  // is cleared; cancelled fetches settle silently.
  void queryClient.cancelQueries();
  queryClient.clear();
  abortAllStreams();
  abortChatStreams();
  useProcessingStore.getState().clearAllStreams();
  useChatStore.getState().clearMessages();
  useUIStore.getState().resetUserScope();
}
