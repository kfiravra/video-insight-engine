import { ApiError, getAccessToken, refreshToken } from "@/api/client";
import { useAuthStore } from "@/stores/auth-store";

export interface AssistantChatMessage {
  role: "user" | "assistant";
  content: string;
}

export interface AssistantSource {
  text: string;
  timestamp?: string;
  /** Numeric position in seconds — null on v1-legacy Qdrant points that predate
   * timestamped payloads (schema_version 2); drives seek/deep-link buttons. */
  timestamp_seconds?: number | null;
  end_seconds?: number | null;
  score: number;
  chunk_index: number;
  /** YouTube id of the source video. Present in library (cross-video) mode so
   * sources can deep-link back to the originating video. */
  video_id?: string;
  /** Video title — present in library mode so the chip labels by title, not a snippet. */
  title?: string;
}

export interface AssistantChatEvent {
  type: "text" | "source" | "tool" | "tool_result" | "error" | "done";
  content?: string;
  sources?: AssistantSource[];
  metadata?: Record<string, unknown>;
}

const API_URL = import.meta.env.VITE_API_URL || "http://localhost:3000/api";

/**
 * Send a chat message to the assistant service and receive SSE events.
 * Uses the API proxy at /api/videos/:videoSummaryId/chat.
 *
 * Auth follows the same pattern as the main API client:
 * - Bearer token from localStorage via getAccessToken()
 * - Automatic token refresh on 401
 * - Force logout on refresh failure
 */
export async function sendAssistantMessage(
  videoSummaryId: string,
  message: string,
  conversationHistory: AssistantChatMessage[],
  onEvent: (event: AssistantChatEvent) => void,
  signal?: AbortSignal,
  confirmToken?: string,
): Promise<void> {
  await streamAssistantChat(
    (token) =>
      fetchAssistantStream(videoSummaryId, message, conversationHistory, token, signal, confirmToken),
    onEvent,
  );
}

/**
 * Send a library-mode chat message spanning all of the user's videos.
 * Uses the API proxy at /api/assistant/library/chat. The gateway derives the
 * owned YouTube-id set server-side, so no id list is sent from the client.
 *
 * Shares the Bearer + 401-refresh + SSE-parsing flow with sendAssistantMessage.
 */
export async function sendLibraryMessage(
  message: string,
  conversationHistory: AssistantChatMessage[],
  onEvent: (event: AssistantChatEvent) => void,
  signal?: AbortSignal,
  confirmToken?: string,
): Promise<void> {
  await streamAssistantChat(
    (token) => fetchLibraryStream(message, conversationHistory, token, signal, confirmToken),
    onEvent,
  );
}

/**
 * Run an assistant SSE request with the shared auth flow: Bearer token from
 * localStorage, single 401 token-refresh + retry, force-logout on failure.
 * The request builder receives the current token so it can rebuild after refresh.
 */
async function streamAssistantChat(
  buildRequest: (token: string | null) => Promise<Response>,
  onEvent: (event: AssistantChatEvent) => void,
): Promise<void> {
  const token = getAccessToken();
  const response = await buildRequest(token);

  // Handle 401 — try token refresh once (mirrors api/client.ts pattern)
  if (response.status === 401 && token) {
    const refreshed = await refreshToken();
    if (refreshed) {
      const retryResponse = await buildRequest(getAccessToken());
      if (!retryResponse.ok) {
        throw await chatRequestFailure(retryResponse);
      }
      await readAssistantSSEStream(retryResponse, onEvent);
      return;
    }
    useAuthStore.getState().forceLogout("Session expired. Please log in again.");
    throw new Error("Session expired");
  }

  if (!response.ok) {
    throw await chatRequestFailure(response);
  }

  await readAssistantSSEStream(response, onEvent);
}

/** Pre-stream refusals the chat words itself: billing (402 / 403) and the burst limits (429). */
const REFUSAL_STATUSES = new Set([402, 403, 429]);

/**
 * The gateway refuses a turn before any stream starts: 402 INSUFFICIENT_TOKENS
 * (`{needed: 1, available}`; a turn needs a positive balance), 403
 * ACCOUNT_BLOCKED, and 429 RATE_LIMITED when the user is sending too fast.
 * Those get a typed `ApiError` carrying the envelope (code and fields), so the
 * chat can say which one happened rather than the generic connection-failure
 * copy; other statuses stay a plain transport error.
 */
async function chatRequestFailure(response: Response, what = "Assistant chat"): Promise<Error> {
  if (!REFUSAL_STATUSES.has(response.status)) {
    return new Error(`${what} failed: ${response.status}`);
  }
  const body = await readErrorBody(response);
  // A 429 without a code is still the burst limit.
  const fallbackCode = response.status === 429 ? "RATE_LIMITED" : undefined;
  const code = typeof body?.error === "string" ? body.error : fallbackCode;
  const message = typeof body?.message === "string" && body.message ? body.message : `${what} refused`;
  return new ApiError(response.status, message, code, body ?? undefined);
}

/** The gateway's `{ error, message, ... }` body, or null when it is missing or not JSON. */
async function readErrorBody(response: Response): Promise<Record<string, unknown> | null> {
  try {
    const body: unknown = await response.json();
    return typeof body === "object" && body !== null ? (body as Record<string, unknown>) : null;
  } catch {
    // A refusal without a JSON body keeps its status; the caller falls back to a code-less error.
    return null;
  }
}

/** Build and send the fetch request to the single-video assistant chat endpoint. */
function fetchAssistantStream(
  videoSummaryId: string,
  message: string,
  conversationHistory: AssistantChatMessage[],
  token: string | null,
  signal?: AbortSignal,
  confirmToken?: string,
): Promise<Response> {
  return fetch(`${API_URL}/videos/${videoSummaryId}/chat`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    credentials: "include",
    body: JSON.stringify({
      message,
      conversationHistory: conversationHistory.map((m) => ({
        role: m.role,
        content: m.content,
      })),
      // Echoed from a `pending_confirmation` event so the assistant runs the
      // parked destructive/costly action. Omitted entirely when absent.
      ...(confirmToken ? { confirmToken } : {}),
    }),
    signal,
  });
}

/** Build and send the fetch request to the library (cross-video) chat endpoint. */
function fetchLibraryStream(
  message: string,
  conversationHistory: AssistantChatMessage[],
  token: string | null,
  signal?: AbortSignal,
  confirmToken?: string,
): Promise<Response> {
  return fetch(`${API_URL}/assistant/library/chat`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    credentials: "include",
    body: JSON.stringify({
      message,
      conversationHistory: conversationHistory.map((m) => ({
        role: m.role,
        content: m.content,
      })),
      // Echoed from a `pending_confirmation` event so the assistant runs the
      // parked destructive/costly action. Omitted entirely when absent.
      ...(confirmToken ? { confirmToken } : {}),
    }),
    signal,
  });
}

/** Result envelope returned by the assistant /action endpoint via the gateway. */
export interface AssistantActionResult {
  success: boolean;
  action: string;
  data?: unknown;
  error?: string;
  trace_id?: string;
}

/**
 * Invoke an assistant action through the API gateway at /assistant/action.
 *
 * Mirrors the Bearer + single 401-refresh flow used by the chat transport.
 * Returns the parsed result envelope; throws on transport/auth failures and on
 * the gateway's pre-run refusals (402 / 403 / 429, as a typed `ApiError`).
 */
export async function runAssistantAction(
  action: string,
  params: Record<string, unknown>,
  videoSummaryId?: string,
): Promise<AssistantActionResult> {
  const buildRequest = (token: string | null): Promise<Response> =>
    fetch(`${API_URL}/assistant/action`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      credentials: "include",
      body: JSON.stringify({ action, params, video_id: videoSummaryId }),
    });

  const token = getAccessToken();
  let response = await buildRequest(token);

  if (response.status === 401 && token) {
    const refreshed = await refreshToken();
    if (!refreshed) {
      useAuthStore.getState().forceLogout("Session expired. Please log in again.");
      throw new Error("Session expired");
    }
    response = await buildRequest(getAccessToken());
  }

  if (!response.ok) {
    throw await chatRequestFailure(response, "Assistant action");
  }

  return (await response.json()) as AssistantActionResult;
}

/** Parse an SSE response body and dispatch events via the callback. */
async function readAssistantSSEStream(
  response: Response,
  onEvent: (event: AssistantChatEvent) => void,
): Promise<void> {
  if (!response.body) {
    throw new Error("No response body");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";

      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        const data = line.slice(6);
        if (data === "[DONE]") continue;

        try {
          const event = JSON.parse(data) as AssistantChatEvent;
          onEvent(event);
        } catch {
          // Skip malformed SSE events
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}
