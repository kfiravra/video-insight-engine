import { create } from "zustand";
import { persist } from "zustand/middleware";

export interface ChatSource {
  title: string;
  youtubeId: string;
  timestamp?: string;
  timestampSeconds?: number;
  relevanceScore?: number;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  sources?: ChatSource[];
  /** Agent tool steps (e.g. "Created folder \"Series\"") rendered as ✓ lines. */
  steps?: string[];
  isStreaming?: boolean;
  /** A reply that is a billing refusal (402 / 403): it links to /settings/billing. */
  billingLink?: boolean;
  createdAt: string;
}

export type ChatStatus = "idle" | "pending" | "streaming" | "error";

/** A destructive/costly assistant action parked server-side until the user
 * confirms it. `token` is the single-use confirmation token echoed back on
 * confirm; deliberately NOT persisted — it expires server-side in minutes. */
export interface PendingConfirmation {
  token: string;
  action: string;
  summary: string;
}

interface ChatState {
  messages: ChatMessage[];
  status: ChatStatus;
  pendingConfirmation: PendingConfirmation | null;
  setMessages: (
    updater: ChatMessage[] | ((prev: ChatMessage[]) => ChatMessage[]),
  ) => void;
  setStatus: (status: ChatStatus) => void;
  setPendingConfirmation: (pending: PendingConfirmation | null) => void;
  clearMessages: () => void;
}

export const useChatStore = create<ChatState>()(
  persist(
    (set) => ({
      messages: [],
      status: "idle",
      pendingConfirmation: null,
      setMessages: (updater) => {
        set((state) => ({
          messages:
            typeof updater === "function" ? updater(state.messages) : updater,
        }));
      },
      setStatus: (status) => {
        set({ status });
      },
      setPendingConfirmation: (pending) => {
        set({ pendingConfirmation: pending });
      },
      clearMessages: () => {
        set({ messages: [], status: "idle", pendingConfirmation: null });
      },
    }),
    {
      name: "vie-chat",
      // Only persist the transcript — status is volatile and a reload should
      // never resurrect a "streaming"/"pending" spinner.
      partialize: (state) => ({ messages: state.messages }),
      // A stream interrupted by navigation or reload leaves a message flagged
      // `isStreaming: true`. Without sanitizing it on load the bubble would show
      // a forever-cursor ▌, so force every rehydrated message to a settled state.
      onRehydrateStorage: () => (state) => {
        if (!state) return;
        state.messages = state.messages.map((m) =>
          m.isStreaming ? { ...m, isStreaming: false } : m,
        );
      },
    },
  ),
);
