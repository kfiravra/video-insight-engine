import type { FastifyBaseLogger } from 'fastify';
import { z } from 'zod';
import { config } from '../config.js';
import { SummarizerPurgeError } from '../utils/errors.js';

export type Provider = 'anthropic' | 'openai' | 'gemini';

export interface ProviderConfig {
  default: Provider;
  fast?: Provider;
  fallback?: Provider | null;
}

export interface SummarizeRequest {
  videoSummaryId: string;
  youtubeId: string;
  url: string;
  userId?: string;
  providers?: ProviderConfig;
  /**
   * Correlates this dispatch with the originating Fastify request. Forwarded
   * as the `X-Request-ID` header so the summarizer pipeline binds it to its
   * structlog contextvars and Langfuse trace tags.
   */
  requestId?: string;
}

export interface PurgeVideoRequest {
  youtubeId: string;
  /** Every `videoSummaryCache` version of the video; their per-run Redis keys go too. */
  videoSummaryIds: string[];
}

export interface PurgeVideoResult {
  qdrantPoints: number;
  s3Objects: number;
  redisKeys: number;
  warnings: string[];
}

const purgeResultSchema = z.object({
  qdrantPoints: z.number().int().nonnegative(),
  s3Objects: z.number().int().nonnegative(),
  redisKeys: z.number().int().nonnegative(),
  warnings: z.array(z.string()).default([]),
});

const SUMMARIZER_TIMEOUT_MS = 10000; // 10 seconds
// S3 listing + batched deletes for a long video can take a while.
const PURGE_TIMEOUT_MS = 60000;
const MAX_RETRIES = 3;
const RETRY_DELAY_MS = 1000;

async function fetchWithTimeout(
  url: string,
  options: RequestInit,
  timeoutMs: number
): Promise<Response> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const response = await fetch(url, {
      ...options,
      signal: controller.signal,
    });
    return response;
  } finally {
    clearTimeout(timeoutId);
  }
}

async function sleep(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms));
}

export class SummarizerClient {
  constructor(private readonly logger: FastifyBaseLogger) {}

  triggerSummarization(request: SummarizeRequest): void {
    // The header is the propagation channel; strip requestId from the body so
    // we don't break the summarizer's Pydantic schema (which doesn't declare it).
    const { requestId, ...bodyFields } = request;
    const headers: Record<string, string> = { 'Content-Type': 'application/json' };
    if (requestId) {
      headers['X-Request-ID'] = requestId;
    }

    // Fire and forget with retry logic - but don't block the caller
    (async () => {
      let lastError: Error | null = null;

      for (let attempt = 1; attempt <= MAX_RETRIES; attempt++) {
        try {
          const response = await fetchWithTimeout(
            `${config.SUMMARIZER_URL}/summarize`,
            {
              method: 'POST',
              headers,
              body: JSON.stringify(bodyFields),
            },
            SUMMARIZER_TIMEOUT_MS
          );

          if (response.ok) {
            this.logger.info({ youtubeId: request.youtubeId }, 'Summarization triggered successfully');
            return;
          }

          lastError = new Error(`Summarizer returned ${response.status}`);
        } catch (err) {
          lastError = err instanceof Error ? err : new Error(String(err));

          if (lastError.name === 'AbortError') {
            this.logger.warn({ attempt, maxRetries: MAX_RETRIES }, 'Summarization request timed out');
          } else {
            this.logger.warn({ attempt, maxRetries: MAX_RETRIES, error: lastError.message }, 'Summarization request failed');
          }
        }

        if (attempt < MAX_RETRIES) {
          await sleep(RETRY_DELAY_MS * Math.pow(2, attempt - 1)); // Exponential backoff: 1s, 2s, 4s
        }
      }

      this.logger.error({ maxRetries: MAX_RETRIES, error: lastError?.message }, 'Failed to trigger summarization after retries');
    })().catch((err) => {
      this.logger.error({ error: err }, 'Unexpected error in triggerSummarization');
    });
  }

  /**
   * Remove every artifact the summarizer owns for a video (Qdrant points, S3
   * objects, Redis keys). The endpoint is idempotent, so a failure is thrown
   * for the caller to retry instead of being retried blindly here.
   */
  async purgeVideo(request: PurgeVideoRequest): Promise<PurgeVideoResult> {
    const url = `${config.SUMMARIZER_URL}/internal/videos/${encodeURIComponent(request.youtubeId)}/purge`;
    let response: Response;
    try {
      response = await fetchWithTimeout(
        url,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-Internal-Secret': config.INTERNAL_SECRET,
          },
          body: JSON.stringify({ videoSummaryIds: request.videoSummaryIds }),
        },
        PURGE_TIMEOUT_MS,
      );
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      throw new SummarizerPurgeError(`summarizer unreachable (${message})`);
    }
    if (!response.ok) {
      throw new SummarizerPurgeError(`summarizer returned ${response.status}`);
    }
    let payload: unknown;
    try {
      payload = await response.json();
    } catch {
      throw new SummarizerPurgeError('summarizer returned a non-JSON body');
    }
    const parsed = purgeResultSchema.safeParse(payload);
    if (!parsed.success) {
      throw new SummarizerPurgeError('summarizer returned an unexpected payload');
    }
    this.logger.info({ youtubeId: request.youtubeId, ...parsed.data }, 'video_purge_completed');
    return parsed.data;
  }
}
