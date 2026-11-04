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

/** Session key of the cross-video library chat; a video chat keys on its videoSummaryId. */
export const LIBRARY_CHAT_SURFACE = "library";

/** A v4 uuid. `crypto.randomUUID` exists only in secure contexts, so a page
 * served over plain http (a LAN address in dev) falls back to getRandomValues. */
function newSessionId(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

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
  /** Observability session id per chat surface (videoSummaryId or "library"),
   * sent as `X-Session-Id` so every turn on a surface lands in one Langfuse
   * session. Minted on the surface's first send; dropped with the transcript. */
  sessionIds: Record<string, string>;
  setMessages: (
    updater: ChatMessage[] | ((prev: ChatMessage[]) => ChatMessage[]),
  ) => void;
  setStatus: (status: ChatStatus) => void;
  setPendingConfirmation: (pending: PendingConfirmation | null) => void;
  /** The surface's session id, minted and stored on first use. */
  ensureSessionId: (surface: string) => string;
  /** Wipes the shared transcript and, with it, every surface's session id. */
  clearMessages: () => void;
}

interface PersistedChatState {
  messages: ChatMessage[];
  sessionIds: Record<string, string>;
}

/** v0 stored only `messages`; v1 adds `sessionIds`, which a v0 transcript never had. */
function migrateChatState(persisted: unknown): PersistedChatState {
  const { messages } = (persisted ?? {}) as Partial<PersistedChatState>;
  return { messages: Array.isArray(messages) ? messages : [], sessionIds: {} };
}

export const useChatStore = create<ChatState>()(
  persist(
    (set, get) => ({
      messages: [],
      status: "idle",
      pendingConfirmation: null,
      sessionIds: {},
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
      ensureSessionId: (surface) => {
        const existing = get().sessionIds[surface];
        if (existing) return existing;
        const sessionId = newSessionId();
        set((state) => ({ sessionIds: { ...state.sessionIds, [surface]: sessionId } }));
        return sessionId;
      },
      clearMessages: () => {
        set({ messages: [], status: "idle", pendingConfirmation: null, sessionIds: {} });
      },
    }),
    {
      name: "vie-chat",
      version: 1,
      migrate: migrateChatState,
      // Persist the transcript and its session ids — status is volatile and a
      // reload should never resurrect a "streaming"/"pending" spinner.
      partialize: (state): PersistedChatState => ({
        messages: state.messages,
        sessionIds: state.sessionIds,
      }),
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
