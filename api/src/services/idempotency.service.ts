import { createHash } from 'node:crypto';
import { FastifyBaseLogger } from 'fastify';
import {
  IdempotencyRepository,
  type IdempotencyKeyDocument,
  type ReserveHashResult,
} from '../repositories/idempotency.repository.js';
import { config } from '../config.js';
import type { ProviderConfig } from './summarizer-client.js';

export interface ComputeKeyInput {
  userId: string;
  youtubeId: string;
  providers?: ProviderConfig;
  /** Optional Stripe-style `Idempotency-Key` header value. Mixed INTO the
   *  payload (not used in place of it), so reusing the same key with a
   *  different URL or provider config produces a distinct hash. */
  clientKey?: string;
}

export interface ComputeContentKeyInput {
  youtubeId: string;
  providers?: ProviderConfig;
  /** Version of the cache row this key addresses. Starts at 1 for fresh
   *  submissions; bypassCache version-bumps increment. Including version means
   *  two concurrent first-time submits collapse onto v1, but a deliberate
   *  Retry that allocates v2 produces a distinct key. */
  version: number;
}

export interface ReserveInput {
  hash: string;
  userId: string;
  youtubeId: string;
}

export interface CompleteInput {
  hash: string;
  videoSummaryId: string;
  userVideoId: string;
}

/**
 * Exhaustive list of ProviderConfig keys. The `satisfies` clause ties this to
 * the TS interface — adding a new key to ProviderConfig without listing it
 * here fails to compile. Without that guard, a new field would silently drop
 * out of the canonical hash and let two requests with different configs dedup
 * to each other.
 */
const PROVIDER_KEYS = ['default', 'fast', 'fallback'] as const satisfies readonly (keyof ProviderConfig)[];

/**
 * Stable canonical serialization of provider overrides. Keys are emitted in
 * sorted order so two equivalent configs (e.g. `{default: 'anthropic'}` vs
 * `{default: 'anthropic', fallback: undefined}`) produce the same string.
 *
 * Exported so the cross-user content-addressed dedup key (computed by
 * `computeContentKey` and used as the `videoSummaryCache.dedupKey`) shares
 * the exact canonicalization with the user-scoped `computeKey` — diverging
 * here would let two hash families disagree on whether two provider configs
 * are equivalent.
 */
export function canonicalizeProviders(providers: ProviderConfig | undefined): string {
  if (!providers) return '';
  const sortedKeys = [...PROVIDER_KEYS].sort();
  const ordered: Record<string, string> = {};
  for (const key of sortedKeys) {
    ordered[key] = String(providers[key] ?? '');
  }
  return JSON.stringify(ordered);
}

/**
 * Module-level computation of the content-addressed dedup key. The
 * `IdempotencyService.computeContentKey` method delegates here so that
 * one-off callers (the mongodb plugin's backfill loop, scripts, etc.) can
 * reuse the exact derivation without spinning up a service instance.
 */
export function computeContentKey(input: ComputeContentKeyInput): string {
  const providersHash = canonicalizeProviders(input.providers);
  const payload = [
    input.youtubeId,
    config.PIPELINE_VERSION,
    providersHash,
    `v${input.version}`,
  ].join(':');
  return createHash('sha256').update(payload).digest('hex');
}

export class IdempotencyService {
  constructor(
    private readonly repo: IdempotencyRepository,
    private readonly logger: FastifyBaseLogger,
  ) {}

  /**
   * Deterministic SHA-256 hash of the dedup-relevant inputs. Two submissions
   * producing the same hash collide; bumping `PIPELINE_VERSION` invalidates
   * every key in one shot.
   *
   * `clientKey` is mixed INTO the payload — it does NOT replace it. This is
   * stricter than Stripe's "use the same key with the same parameters or
   * we'll error" semantic; instead, different parameters with the same key
   * simply produce a different hash, and the work runs fresh. This trades
   * the ability to dedup arbitrary payloads (Stripe's design) for the
   * guarantee that reusing a key by mistake never returns the wrong video.
   */
  computeKey(input: ComputeKeyInput): string {
    const providersHash = canonicalizeProviders(input.providers);
    const clientPart = input.clientKey ? `client:${input.clientKey}` : '';
    const payload = [
      input.userId,
      input.youtubeId,
      config.PIPELINE_VERSION,
      providersHash,
      clientPart,
    ].join(':');
    return createHash('sha256').update(payload).digest('hex');
  }

  /**
   * User-independent content-addressed hash used as `videoSummaryCache.dedupKey`.
   *
   * Two distinct users submitting the same `youtubeId` (same providers, same
   * PIPELINE_VERSION) compute the same key and converge on a single cache row
   * via `videoRepository.upsertCacheByDedupKey`. The route-level `computeKey`
   * stays user-scoped for per-user double-submit protection; this key powers
   * the cross-user single-flight at the cache layer.
   *
   * `version` is part of the key so that a bypassCache version bump (which
   * deliberately starts a fresh pipeline run) produces a distinct hash from
   * the existing row, while two concurrent first-time submits both targeting
   * v1 collapse correctly.
   */
  computeContentKey(input: ComputeContentKeyInput): string {
    return computeContentKey(input);
  }

  /** Strong-consistency lookup; null when the hash is fresh. */
  async findHit(hash: string): Promise<IdempotencyKeyDocument | null> {
    return this.repo.findByHash(hash);
  }

  /**
   * Insert a `pending` placeholder atomically. Concurrent submissions race
   * on the unique-hash index; the loser receives `{ created: false, doc }`
   * and the route branches on the doc's status to either return the cached
   * result or 409 the in-flight retry.
   */
  async reserveHash(input: ReserveInput): Promise<ReserveHashResult> {
    return this.repo.reserveHash({
      hash: input.hash,
      userId: input.userId,
      youtubeId: input.youtubeId,
      pipelineVersion: config.PIPELINE_VERSION,
      ttlSeconds: config.IDEMPOTENCY_TTL_SECONDS,
    });
  }

  /** Promote a reserved hash to `completed`, recording the work's IDs. */
  async completeHash(input: CompleteInput): Promise<void> {
    await this.repo.completeHash(input);
  }

  /** Drop a key — used by the failure-unwind path so retries are allowed. */
  async invalidateByHash(hash: string): Promise<void> {
    await this.repo.invalidateByHash(hash);
  }

  /**
   * Race-safe stale-hit cleanup — deletes only the specific completed row the
   * caller observed, leaving any concurrent fresh reservation untouched.
   */
  async invalidateStaleCompleted(hash: string, videoSummaryId: string): Promise<void> {
    await this.repo.invalidateStaleCompleted(hash, videoSummaryId);
  }

  /**
   * Drop every key for a videoSummaryId. The failure event arrives keyed by
   * videoSummaryId, not hash — this is the entry point used by
   * `internal.routes.ts` on terminal-FAILED status.
   */
  async invalidateByVideoSummaryId(videoSummaryId: string): Promise<void> {
    try {
      await this.repo.invalidateByVideoSummaryId(videoSummaryId);
    } catch (err) {
      this.logger.warn({ err, videoSummaryId }, 'idempotency invalidate on failure failed');
    }
  }

  /** Drop the keys that completed against one library row (user-scope delete). */
  async invalidateByUserVideoId(userVideoId: string): Promise<number> {
    return this.repo.invalidateByUserVideoId(userVideoId);
  }

  /** Drop every key for a video, all users and versions (global purge). */
  async invalidateByYoutubeId(youtubeId: string): Promise<number> {
    return this.repo.invalidateByYoutubeId(youtubeId);
  }
}
