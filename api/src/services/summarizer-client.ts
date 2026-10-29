import type { FastifyBaseLogger } from 'fastify';
import type { Redis } from 'ioredis';
import { z } from 'zod';
import { config } from '../config.js';
import {
  MetadataUnavailableError,
  SummarizerPurgeError,
  type MetadataUnavailableReason,
} from '../utils/errors.js';

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

/** Pre-flight facts about one video, from the summarizer's yt-dlp metadata call. */
export interface VideoMetadata {
  youtubeId: string;
  title: string;
  channel: string;
  /** Seconds, always > 0. */
  duration: number;
  thumbnailUrl: string | null;
}

const videoMetadataSchema = z.object({
  youtubeId: z.string(),
  title: z.string(),
  channel: z.string(),
  duration: z.number().int().positive(),
  thumbnailUrl: z.string().nullable().default(null),
});

const purgeResultSchema = z.object({
  qdrantPoints: z.number().int().nonnegative(),
  s3Objects: z.number().int().nonnegative(),
  redisKeys: z.number().int().nonnegative(),
  warnings: z.array(z.string()).default([]),
});

const SUMMARIZER_TIMEOUT_MS = 10000; // 10 seconds
// S3 listing + batched deletes for a long video can take a while.
const PURGE_TIMEOUT_MS = 60000;
// The summarizer's proxy-exit rotation (EXIT_ROTATION_BUDGET_S = 15 s) only checks
// its budget before starting an exit beyond the third, so a rotation that is still
// working can answer 200 at ~15 s plus the attempt in flight. 25 s lets that answer
// through; a shorter abort would throw it away as `timeout` exactly when rotation works.
const METADATA_TIMEOUT_MS = 25000;
const METADATA_CACHE_TTL_SECONDS = 24 * 60 * 60;
// A dead id is remembered briefly so a retry loop does not ask YouTube each time.
const METADATA_MISS_TTL_SECONDS = 10 * 60;
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

/** The summarizer's /video/metadata statuses: 404 unknown video, 422 live or not yet playable, the rest are its trouble. */
function reasonForStatus(status: number): MetadataUnavailableReason {
  if (status === 404) return 'not_found';
  if (status === 422) return 'unsupported';
  return 'upstream';
}

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

async function sleep(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms));
}

/** Redis key of a video's cached metadata. */
export function videoMetaCacheKey(youtubeId: string): string {
  return `vie:api:video-meta:${youtubeId}`;
}

/** Redis marker that the summarizer answered "no such video" for this id in the last 10 minutes. */
export function videoMetaMissKey(youtubeId: string): string {
  return `vie:api:video-meta-miss:${youtubeId}`;
}

export class SummarizerClient {
  /** `redis` is optional: without it every metadata call goes to the summarizer. */
  constructor(
    private readonly logger: FastifyBaseLogger,
    private readonly redis?: Redis,
  ) {}

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

  /**
   * Duration, title, channel and thumbnail of a video before any submit — no
   * captions, no LLM. Cached 24 h in Redis (a Redis failure only costs the
   * cache). Throws MetadataUnavailableError: `not_found` on a 404, `unsupported`
   * on a 422 (live stream, no duration yet), `timeout` after 25 s, `upstream`
   * on anything else. Only a `not_found` is remembered (10 min, so a dead id
   * does not reach YouTube on every submit); other failures are never cached.
   */
  async getVideoMetadata(youtubeId: string): Promise<VideoMetadata> {
    const cached = await this.readMetadataCache(youtubeId);
    if (cached) return cached;
    if (await this.isKnownMissing(youtubeId)) {
      this.logger.debug({ youtubeId }, 'video_metadata_known_missing');
      throw new MetadataUnavailableError('not_found');
    }

    try {
      const metadata = await this.fetchVideoMetadata(youtubeId);
      await this.writeMetadataCache(metadata);
      return metadata;
    } catch (err) {
      if (err instanceof MetadataUnavailableError && err.reason === 'not_found') {
        await this.markMissing(youtubeId);
      }
      throw err;
    }
  }

  private async isKnownMissing(youtubeId: string): Promise<boolean> {
    if (!this.redis) return false;
    try {
      return (await this.redis.get(videoMetaMissKey(youtubeId))) !== null;
    } catch (err) {
      this.logger.warn({ youtubeId, error: errorMessage(err) }, 'video_meta_miss_read_failed');
      return false;
    }
  }

  private async markMissing(youtubeId: string): Promise<void> {
    if (!this.redis) return;
    try {
      await this.redis.set(videoMetaMissKey(youtubeId), '1', 'EX', METADATA_MISS_TTL_SECONDS);
    } catch (err) {
      this.logger.warn({ youtubeId, error: errorMessage(err) }, 'video_meta_miss_write_failed');
    }
  }

  private async readMetadataCache(youtubeId: string): Promise<VideoMetadata | null> {
    if (!this.redis) return null;
    try {
      const raw = await this.redis.get(videoMetaCacheKey(youtubeId));
      if (raw === null) return null;
      const parsed = videoMetadataSchema.safeParse(JSON.parse(raw));
      if (parsed.success) return parsed.data;
      this.logger.warn({ youtubeId }, 'video_meta_cache_entry_invalid');
    } catch (err) {
      this.logger.warn({ youtubeId, error: errorMessage(err) }, 'video_meta_cache_read_failed');
    }
    return null;
  }

  private async writeMetadataCache(metadata: VideoMetadata): Promise<void> {
    if (!this.redis) return;
    try {
      await this.redis.set(
        videoMetaCacheKey(metadata.youtubeId),
        JSON.stringify(metadata),
        'EX',
        METADATA_CACHE_TTL_SECONDS,
      );
    } catch (err) {
      this.logger.warn(
        { youtubeId: metadata.youtubeId, error: errorMessage(err) },
        'video_meta_cache_write_failed',
      );
    }
  }

  private async fetchVideoMetadata(youtubeId: string): Promise<VideoMetadata> {
    const query = new URLSearchParams({ youtubeId });
    let response: Response;
    try {
      response = await fetchWithTimeout(
        `${config.SUMMARIZER_URL}/video/metadata?${query}`,
        { method: 'GET', headers: { 'X-Internal-Secret': config.INTERNAL_SECRET } },
        METADATA_TIMEOUT_MS,
      );
    } catch (err) {
      const reason = err instanceof Error && err.name === 'AbortError' ? 'timeout' : 'upstream';
      throw this.metadataFailure(youtubeId, reason, errorMessage(err));
    }
    if (!response.ok) {
      throw this.metadataFailure(youtubeId, reasonForStatus(response.status), `summarizer returned ${response.status}`);
    }
    return this.parseMetadata(youtubeId, response);
  }

  private async parseMetadata(youtubeId: string, response: Response): Promise<VideoMetadata> {
    let payload: unknown;
    try {
      payload = await response.json();
    } catch {
      throw this.metadataFailure(youtubeId, 'upstream', 'non-JSON body');
    }
    const parsed = videoMetadataSchema.safeParse(payload);
    if (!parsed.success) {
      throw this.metadataFailure(youtubeId, 'upstream', 'unexpected payload');
    }
    return parsed.data;
  }

  private metadataFailure(
    youtubeId: string,
    reason: MetadataUnavailableReason,
    detail: string,
  ): MetadataUnavailableError {
    this.logger.warn({ youtubeId, reason, detail }, 'video_metadata_unavailable');
    return new MetadataUnavailableError(reason);
  }
}
