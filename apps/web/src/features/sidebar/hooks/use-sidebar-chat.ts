import { useCallback, useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError } from "@/api/client";
import {
  sendAssistantMessage,
  sendLibraryMessage,
  type AssistantChatMessage,
  type AssistantChatEvent,
  type AssistantSource,
} from "@/lib/assistant";
import { useLabels } from "@/lib/i18n";
import { queryKeys } from "@/lib/query-keys";
import {
  useChatStore,
  type ChatMessage,
  type ChatSource,
  type PendingConfirmation,
} from "@/stores/chat-store";

export type {
  ChatMessage,
  ChatSource,
  ChatStatus,
  PendingConfirmation,
} from "@/stores/chat-store";

interface UseSidebarChatOptions {
  /** When provided, chat messages are sent to this video's assistant endpoint. */
  videoSummaryId?: string;
}

// Agent actions that mutate the library server-side. When one completes we must
// invalidate the folder/video caches so the sidebar tree reflects it live.
const MUTATING_ACTIONS = new Set([
  "create_folder",
  "rename_folder",
  "move_folder",
  "delete_folder",
  "move_video",
  "generate_video",
  "organize_library",
]);

const RATE_LIMITED_MESSAGE = "Too many messages. Please wait a moment and try again.";
const CHAT_LIMIT_REACHED_MESSAGE =
  "You've used your free chat messages for now. More open up as the last 24 hours pass.";
const CONNECT_FAILED_MESSAGE = "Failed to connect to assistant. Please try again.";

/** Copy for a request that failed before any SSE event arrived. */
function requestFailureMessage(err: unknown): string {
  if (err instanceof ApiError && err.code === "CHAT_LIMIT_REACHED") {
    return CHAT_LIMIT_REACHED_MESSAGE;
  }
  const rateLimited =
    err instanceof ApiError && (err.status === 429 || err.code === "RATE_LIMITED");
  return rateLimited ? RATE_LIMITED_MESSAGE : CONNECT_FAILED_MESSAGE;
}

/** Map the wire field `timestamp_seconds` (snake_case per the Python
 * RAGSource model; null on v1-legacy chunks) to ChatSource.timestampSeconds —
 * whole seconds for YouTube `&t=` deep links and player seeks; undefined
 * hides the seek UI. */
function toTimestampSeconds(source: AssistantSource): number | undefined {
  const seconds = source.timestamp_seconds;
  return typeof seconds === "number" && seconds >= 0
    ? Math.floor(seconds)
    : undefined;
}

/** Narrow the untyped `confirmation` metadata of a `pending_confirmation`
 * tool event into the store's shape. Returns null on any malformed payload. */
function parseConfirmation(
  metadata: Record<string, unknown> | undefined,
): PendingConfirmation | null {
  const raw = metadata?.confirmation;
  if (!raw || typeof raw !== "object") return null;
  const { token, action, summary } = raw as Record<string, unknown>;
  if (typeof token !== "string" || token.length === 0) return null;
  return {
    token,
    action: typeof action === "string" ? action : "",
    summary: typeof summary === "string" ? summary : "",
  };
}

export function useSidebarChat(options: UseSidebarChatOptions = {}) {
  const { videoSummaryId } = options;

  // Chat transcript lives in a persisted Zustand store so the conversation
  // survives the Sidebar remounting on every navigation (and page reloads).
  // The SAME conversation is shared across videos/pages — videoSummaryId only
  // selects the transport (single-video vs library), it never wipes history.
  const messages = useChatStore((s) => s.messages);
  const status = useChatStore((s) => s.status);
  const pendingConfirmation = useChatStore((s) => s.pendingConfirmation);
  const setMessages = useChatStore((s) => s.setMessages);
  const setStatus = useChatStore((s) => s.setStatus);
  const setPendingConfirmation = useChatStore((s) => s.setPendingConfirmation);
  const storeClearMessages = useChatStore((s) => s.clearMessages);

  const labels = useLabels();

  const queryClient = useQueryClient();

  const abortControllerRef = useRef<AbortController | null>(null);
  const mountedRef = useRef(true);
  // Latest-messages ref for event handlers (sendChat builds history from it).
  // Written in an effect, not during render, per the react-hooks refs rule —
  // handlers only run post-commit, so the effect-written value is always
  // current when read. Deliberately trails a same-tick optimistic append: the
  // history sent to the assistant must exclude the message just added.
  const messagesRef = useRef(messages);
  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  // Tracks whether the current turn triggered a library-mutating agent action,
  // so we refetch the folder/video trees once at the end (not per tool call).
  const didMutateRef = useRef(false);

  // Reset on mount, clean up on unmount. Re-setting `true` here is essential:
  // React 19 StrictMode (dev) does mount → unmount → remount, and the cleanup
  // sets mountedRef false. Without restoring it on the remount, every streamed
  // event below is dropped by the `if (!mountedRef.current) return` guards —
  // the chat renders an empty bubble and the send button stays stuck loading.
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      abortControllerRef.current?.abort();
    };
  }, []);

  // Stream a regular RAG chat turn (single-video or library mode). When
  // `confirmToken` is set, the request also redeems a pending server-side
  // confirmation (destructive/costly agent action) before the model runs.
  const sendChat = useCallback(
    (message: string, confirmToken?: string) => {
      // Build conversation history from current messages (excluding the one we just added)
      const history: AssistantChatMessage[] = messagesRef.current.map((m) => ({
        role: m.role,
        content: m.content,
      }));

      // Create a streaming assistant message placeholder
      const assistantId = `msg-${Date.now()}-reply`;
      const assistantMsg: ChatMessage = {
        id: assistantId,
        role: "assistant",
        content: "",
        isStreaming: true,
        createdAt: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, assistantMsg]);
      setStatus("pending");

      // Abort any in-flight request
      abortControllerRef.current?.abort();
      const controller = new AbortController();
      abortControllerRef.current = controller;

      const handleEvent = (event: AssistantChatEvent) => {
        if (!mountedRef.current) return;

        switch (event.type) {
          case "text":
            setStatus("streaming");
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId
                  ? { ...m, content: m.content + (event.content ?? "") }
                  : m,
              ),
            );
            break;

          case "source": {
            // One chip per video (deduped by id), labelled by title, no score —
            // a clean "Sources" row rather than a debug list of every chunk.
            const seen = new Set<string>();
            const mapped: ChatSource[] = [];
            for (const s of event.sources ?? []) {
              const youtubeId = s.video_id ?? "";
              const key = youtubeId || s.text.slice(0, 24);
              if (seen.has(key)) continue;
              seen.add(key);
              mapped.push({
                title: s.title || s.text.slice(0, 60),
                youtubeId,
                timestamp: s.timestamp ?? undefined,
                timestampSeconds: toTimestampSeconds(s),
              });
            }
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId ? { ...m, sources: mapped } : m,
              ),
            );
            break;
          }

          case "tool": {
            // A gated (destructive/costly) action parked server-side — surface
            // the inline Confirm/Cancel affordance carrying its token.
            if (event.metadata?.status === "pending_confirmation") {
              const confirmation = parseConfirmation(event.metadata);
              if (confirmation) setPendingConfirmation(confirmation);
              break;
            }

            // An echoed token was invalid/expired — show the outcome as a step.
            if (event.metadata?.status === "confirmation_failed") {
              const note = event.content;
              if (!note) break;
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantId
                    ? { ...m, steps: [...(m.steps ?? []), note] }
                    : m,
                ),
              );
              break;
            }

            // The agent emits a "start"/"done" pair per tool call. Only the
            // finished step carries a human summary worth showing, so append
            // its content as a ✓ step line on the streaming assistant message.
            if (event.metadata?.status !== "done") break;

            // If the finished tool mutated the library server-side, flag a
            // refetch for the chat "done" event (one refresh per turn).
            const action = event.metadata?.action;
            if (typeof action === "string" && MUTATING_ACTIONS.has(action)) {
              didMutateRef.current = true;
            }

            const step = event.content;
            if (!step) break;
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId
                  ? { ...m, steps: [...(m.steps ?? []), step] }
                  : m,
              ),
            );
            break;
          }

          case "error":
            setStatus("error");
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId
                  ? {
                      ...m,
                      content:
                        event.content ?? "Something went wrong. Please try again.",
                      isStreaming: false,
                    }
                  : m,
              ),
            );
            break;

          case "done":
            setStatus("idle");
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId ? { ...m, isStreaming: false } : m,
              ),
            );
            // The agent mutated the library bypassing client mutation hooks, so
            // refetch the exact caches the sidebar tree (useFolders/useAllVideos)
            // reads — making folder/video changes appear live, no manual refresh.
            if (didMutateRef.current) {
              queryClient.invalidateQueries({
                queryKey: queryKeys.folders.lists(),
              });
              queryClient.invalidateQueries({
                queryKey: queryKeys.videos.lists(),
              });
              didMutateRef.current = false;
            }
            break;
        }
      };

      // No active video → library mode (cross-video RAG over the user's videos).
      // Otherwise scope the chat to the single open video.
      const request = videoSummaryId
        ? sendAssistantMessage(
            videoSummaryId, message, history, handleEvent, controller.signal, confirmToken,
          )
        : sendLibraryMessage(message, history, handleEvent, controller.signal, confirmToken);

      request.catch((err) => {
        if (!mountedRef.current) return;
        // Ignore abort errors (user navigated away or sent another message)
        if (err instanceof Error && err.name === "AbortError") return;

        setStatus("error");
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantId
              ? {
                  ...m,
                  content: requestFailureMessage(err),
                  isStreaming: false,
                }
              : m,
          ),
        );
      });
    },
    [videoSummaryId, setMessages, setStatus, setPendingConfirmation, queryClient],
  );

  // Every message — single-video or library — now goes through the streaming
  // agent. Tool calls (folder creation, library organization, video dispatch)
  // are handled server-side and surfaced as ✓ step lines via "tool" events.
  const sendMessage = useCallback(
    (message: string) => {
      // A fresh message abandons any pending confirmation prompt — the parked
      // action simply expires server-side; its token is never sent.
      setPendingConfirmation(null);

      // Always echo the user's message into the transcript first.
      const userMsg: ChatMessage = {
        id: `msg-${Date.now()}`,
        role: "user",
        content: message,
        createdAt: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, userMsg]);

      sendChat(message);
    },
    [setMessages, setPendingConfirmation, sendChat],
  );

  // Confirm the pending destructive/costly action: echo the confirmation as a
  // user turn and resend with the single-use token so the assistant executes
  // the parked action server-side.
  const confirmPendingAction = useCallback(() => {
    const pending = useChatStore.getState().pendingConfirmation;
    if (!pending) return;
    setPendingConfirmation(null);

    const userMsg: ChatMessage = {
      id: `msg-${Date.now()}`,
      role: "user",
      content: labels.actionConfirm,
      createdAt: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, userMsg]);

    sendChat(labels.actionConfirm, pending.token);
  }, [labels.actionConfirm, setMessages, setPendingConfirmation, sendChat]);

  // Decline the pending action. Client-side only — the unused token expires
  // server-side within its TTL, so nothing can execute later.
  const cancelPendingAction = useCallback(() => {
    if (!useChatStore.getState().pendingConfirmation) return;
    setPendingConfirmation(null);

    const assistantMsg: ChatMessage = {
      id: `msg-${Date.now()}-cancelled`,
      role: "assistant",
      content: labels.actionCancelled,
      createdAt: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, assistantMsg]);
  }, [labels.actionCancelled, setMessages, setPendingConfirmation]);

  // Abort any in-flight stream before wiping the transcript (the store's
  // clearMessages only resets state; the abort lives in the hook).
  const clearMessages = useCallback(() => {
    abortControllerRef.current?.abort();
    didMutateRef.current = false;
    storeClearMessages();
  }, [storeClearMessages]);

  return {
    messages,
    status,
    pendingConfirmation,
    sendMessage,
    confirmPendingAction,
    cancelPendingAction,
    clearMessages,
  };
}
