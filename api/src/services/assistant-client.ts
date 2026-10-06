import type { FastifyBaseLogger } from 'fastify';
import { config } from '../config.js';
import { ServiceTimeoutError, ServiceUnavailableError } from '../utils/errors.js';

const ASSISTANT_TIMEOUT_MS = 60000;
const ACTION_TIMEOUT_MS = 60000;

/**
 * Canonical list of assistant actions — the single source of truth. The
 * `AssistantAction` union and every route's Zod enum derive from this array so
 * the type and the validators can never drift apart.
 */
export const ASSISTANT_ACTIONS = [
  'save_note',
  'quiz_me',
  'find_moment',
  'explain',
  'generate_video',
  'organize_library',
  'create_folder',
  'rename_folder',
  'move_folder',
  'delete_folder',
  'move_video',
] as const;

export type AssistantAction = (typeof ASSISTANT_ACTIONS)[number];

export interface AssistantChatOptions {
  videoId: string;
  userId: string;
  message: string;
  conversationHistory?: Array<{ role: 'user' | 'assistant'; content: string }>;
  /** Single-use token echoed from a `pending_confirmation` SSE event — lets the
   *  assistant run a parked destructive/costly action. Validated server-side. */
  confirmToken?: string;
  /** Forwarded as `X-Request-ID` header so the assistant binds it on contextvars. */
  requestId?: string;
  /** Caller abort (e.g. the HTTP client disconnecting). Tears down both the
   *  connect and the returned SSE body stream — the internal 60s timeout only
   *  covers connect. */
  signal?: AbortSignal;
}

export interface AssistantActionOptions {
  /** Optional — library-scoped actions (organize_library, *_folder) have no video. */
  videoId?: string;
  userId: string;
  action: AssistantAction;
  params?: Record<string, string | number | boolean>;
  /** Forwarded as `X-Request-ID` header so the assistant binds it on contextvars. */
  requestId?: string;
}

export interface AssistantActionResponse {
  success: boolean;
  action: string;
  data: Record<string, unknown> | null;
  error: string | null;
  trace_id: string;
}

export interface AssistantLibraryChatOptions {
  /** YouTube ids the gateway derived server-side from the user's owned videos. */
  youtubeIds: string[];
  userId: string;
  message: string;
  conversationHistory?: Array<{ role: 'user' | 'assistant'; content: string }>;
  /** Owned-video inventory ({video_id, title}) so the assistant can name videos
   *  by title and answer "what videos do I have?". Derived server-side. */
  library?: Array<{ video_id: string; title: string }>;
  /** Single-use token echoed from a `pending_confirmation` SSE event — lets the
   *  assistant run a parked destructive/costly action. Validated server-side. */
  confirmToken?: string;
  /** Forwarded as `X-Request-ID` header so the assistant binds it on contextvars. */
  requestId?: string;
  /** Caller abort (e.g. the HTTP client disconnecting). Tears down both the
   *  connect and the returned SSE body stream — the internal 60s timeout only
   *  covers connect. */
  signal?: AbortSignal;
}

export interface AssistantLibrarySearchOptions {
  /** YouTube ids the gateway derived server-side from the user's owned videos. */
  youtubeIds: string[];
  userId: string;
  query: string;
  topK?: number;
  sources?: string[];
  /** Forwarded as `X-Request-ID` header so the assistant binds it on contextvars. */
  requestId?: string;
}

/**
 * History as the assistant service accepts it. A turn whose reply was only a
 * tool step or a confirmation prompt has no text, and the assistant's
 * ChatMessage requires non-empty content, so one blank turn made it reject the
 * whole request with 422 (e.g. the Confirm click on a pending action).
 */
function toAssistantHistory(
  history: Array<{ role: 'user' | 'assistant'; content: string }> | undefined,
): Array<{ role: 'user' | 'assistant'; content: string }> {
  return (history ?? [])
    .filter(m => m.content.trim().length > 0)
    .map(m => ({ role: m.role, content: m.content }));
}

export class AssistantClient {
  constructor(private readonly logger: FastifyBaseLogger) {}

  /**
   * Forward a caller-supplied abort signal onto the fetch controller. The
   * internal timeout is cleared once the SSE body is handed back, so this is
   * what lets a client disconnect tear down the upstream stream mid-read —
   * aborting the fetch signal also errors the returned body's reader loop.
   */
  private forwardAbort(signal: AbortSignal | undefined, controller: AbortController): void {
    if (!signal) return;
    if (signal.aborted) {
      controller.abort();
      return;
    }
    signal.addEventListener('abort', () => controller.abort(), { once: true });
  }

  /**
   * Send a chat message to the assistant service and get SSE stream back.
   * Returns a ReadableStream that can be piped directly to the client.
   */
  async chat(options: AssistantChatOptions): Promise<ReadableStream<Uint8Array>> {
    const url = `${config.ASSISTANT_URL}/chat`;

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), ASSISTANT_TIMEOUT_MS);
    this.forwardAbort(options.signal, controller);

    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      'X-Internal-Secret': config.INTERNAL_SECRET,
      // Forwarded so the assistant attributes this RAG chat's llm_usage rows to
      // the user (otherwise user_id lands null and admin can't bill the call).
      'X-User-Id': options.userId,
    };
    if (options.requestId) {
      headers['X-Request-ID'] = options.requestId;
    }

    try {
      const response = await fetch(url, {
        method: 'POST',
        headers,
        body: JSON.stringify({
          video_id: options.videoId,
          message: options.message,
          conversation_history: toAssistantHistory(options.conversationHistory),
          // undefined is dropped by JSON.stringify — only sent when present.
          confirm_token: options.confirmToken,
        }),
        signal: controller.signal,
      });

      if (!response.ok) {
        const errorText = await response.text().catch(() => 'Unknown error');
        this.logger.error({ status: response.status, error: errorText }, 'Assistant chat request failed');
        throw new ServiceUnavailableError('Assistant');
      }

      if (!response.body) {
        throw new ServiceUnavailableError('Assistant: empty response body');
      }

      clearTimeout(timeout);
      return response.body;
    } catch (err) {
      clearTimeout(timeout);
      if (err instanceof ServiceUnavailableError || err instanceof ServiceTimeoutError) {
        throw err;
      }
      if ((err as Error).name === 'AbortError') {
        throw new ServiceTimeoutError('Assistant');
      }
      this.logger.error(err, 'Assistant chat connection failed');
      throw new ServiceUnavailableError('Assistant');
    }
  }

  /**
   * Dispatch a structured action to the assistant service.
   * Returns the parsed JSON response — the assistant always replies with an
   * AssistantActionResponse envelope, even on validation/business errors.
   */
  async action(options: AssistantActionOptions): Promise<{
    status: number;
    body: AssistantActionResponse;
  }> {
    const url = `${config.ASSISTANT_URL}/action`;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), ACTION_TIMEOUT_MS);

    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      'X-Internal-Secret': config.INTERNAL_SECRET,
      'X-User-Id': options.userId,
    };
    if (options.requestId) {
      headers['X-Request-ID'] = options.requestId;
    }

    const requestBody: Record<string, unknown> = {
      action: options.action,
      params: options.params ?? {},
    };
    if (options.videoId !== undefined) {
      requestBody.video_id = options.videoId;
    }

    try {
      const response = await fetch(url, {
        method: 'POST',
        headers,
        body: JSON.stringify(requestBody),
        signal: controller.signal,
      });

      const body = (await response.json().catch(() => null)) as AssistantActionResponse | null;
      clearTimeout(timeout);

      if (!body) {
        this.logger.error({ status: response.status }, 'Assistant action returned non-JSON body');
        throw new ServiceUnavailableError('Assistant');
      }

      // Never relay an upstream 5xx body to the client — it can carry internal
      // paths/stack detail. Collapse to a generic unavailable error; 4xx
      // (validation/business) envelopes still pass through for the UI.
      if (response.status >= 500) {
        this.logger.error({ status: response.status }, 'Assistant action returned a server error');
        throw new ServiceUnavailableError('Assistant');
      }

      return { status: response.status, body };
    } catch (err) {
      clearTimeout(timeout);
      if (err instanceof ServiceUnavailableError || err instanceof ServiceTimeoutError) {
        throw err;
      }
      if ((err as Error).name === 'AbortError') {
        throw new ServiceTimeoutError('Assistant');
      }
      this.logger.error(err, 'Assistant action connection failed');
      throw new ServiceUnavailableError('Assistant');
    }
  }

  /**
   * Send a library-mode chat message to the assistant service and get an SSE
   * stream back. The `youtubeIds` are derived server-side from the caller's
   * owned videos — never trust a client-supplied id list.
   * Returns a ReadableStream that can be piped directly to the client.
   */
  async libraryChat(options: AssistantLibraryChatOptions): Promise<ReadableStream<Uint8Array>> {
    const url = `${config.ASSISTANT_URL}/library/chat`;

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), ASSISTANT_TIMEOUT_MS);
    this.forwardAbort(options.signal, controller);

    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      'X-Internal-Secret': config.INTERNAL_SECRET,
      'X-User-Id': options.userId,
    };
    if (options.requestId) {
      headers['X-Request-ID'] = options.requestId;
    }

    try {
      const response = await fetch(url, {
        method: 'POST',
        headers,
        body: JSON.stringify({
          video_ids: options.youtubeIds,
          message: options.message,
          conversation_history: toAssistantHistory(options.conversationHistory),
          library: options.library ?? [],
          // undefined is dropped by JSON.stringify — only sent when present.
          confirm_token: options.confirmToken,
        }),
        signal: controller.signal,
      });

      if (!response.ok) {
        const errorText = await response.text().catch(() => 'Unknown error');
        this.logger.error({ status: response.status, error: errorText }, 'Assistant library chat request failed');
        throw new ServiceUnavailableError('Assistant');
      }

      if (!response.body) {
        throw new ServiceUnavailableError('Assistant: empty response body');
      }

      clearTimeout(timeout);
      return response.body;
    } catch (err) {
      clearTimeout(timeout);
      if (err instanceof ServiceUnavailableError || err instanceof ServiceTimeoutError) {
        throw err;
      }
      if ((err as Error).name === 'AbortError') {
        throw new ServiceTimeoutError('Assistant');
      }
      this.logger.error(err, 'Assistant library chat connection failed');
      throw new ServiceUnavailableError('Assistant');
    }
  }

  /**
   * Retrieval-only library search. The `youtubeIds` are derived server-side
   * from the caller's owned videos — never trust a client-supplied id list.
   * Returns the parsed JSON `{ results: [...] }` envelope from the assistant.
   */
  async librarySearch(options: AssistantLibrarySearchOptions): Promise<{
    status: number;
    body: Record<string, unknown>;
  }> {
    const url = `${config.ASSISTANT_URL}/library/search`;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), ACTION_TIMEOUT_MS);

    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      'X-Internal-Secret': config.INTERNAL_SECRET,
      'X-User-Id': options.userId,
    };
    if (options.requestId) {
      headers['X-Request-ID'] = options.requestId;
    }

    const requestBody: Record<string, unknown> = {
      video_ids: options.youtubeIds,
      query: options.query,
    };
    if (options.topK !== undefined) {
      requestBody.top_k = options.topK;
    }
    if (options.sources !== undefined) {
      requestBody.sources = options.sources;
    }

    try {
      const response = await fetch(url, {
        method: 'POST',
        headers,
        body: JSON.stringify(requestBody),
        signal: controller.signal,
      });

      const body = (await response.json().catch(() => null)) as Record<string, unknown> | null;
      clearTimeout(timeout);

      if (!body) {
        this.logger.error({ status: response.status }, 'Assistant library search returned non-JSON body');
        throw new ServiceUnavailableError('Assistant');
      }

      // Never relay an upstream 5xx body to the client — it can carry internal
      // paths/stack detail. Collapse to a generic unavailable error.
      if (response.status >= 500) {
        this.logger.error({ status: response.status }, 'Assistant library search returned a server error');
        throw new ServiceUnavailableError('Assistant');
      }

      return { status: response.status, body };
    } catch (err) {
      clearTimeout(timeout);
      if (err instanceof ServiceUnavailableError || err instanceof ServiceTimeoutError) {
        throw err;
      }
      if ((err as Error).name === 'AbortError') {
        throw new ServiceTimeoutError('Assistant');
      }
      this.logger.error(err, 'Assistant library search connection failed');
      throw new ServiceUnavailableError('Assistant');
    }
  }
}
