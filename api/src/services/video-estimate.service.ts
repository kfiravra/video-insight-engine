import { InvalidYouTubeUrlError } from '../utils/errors.js';
import { extractYoutubeId } from '../utils/youtube.js';
import { estimateTokens } from './billing/tokens.js';
import type { VideoEstimateResponse } from '@vie/types';
import type { BillingGate } from './billing/billing-gate.js';
import type { IdempotencyService } from './idempotency.service.js';
import type { EvalUserReader } from './video.service.js';
import type { VideoRepository } from '../repositories/video.repository.js';

/** What a submit of `url` would hold before it runs (the shared GET /api/videos/estimate shape). */
export type VideoEstimate = VideoEstimateResponse;

/**
 * The "≈ N tokens" hint before a submit. Informational: the submit decides
 * again on the account's state at that moment (an eval account always pays,
 * so it is never shown as free).
 */
export class VideoEstimateService {
  constructor(
    private readonly videoRepository: Pick<VideoRepository, 'findUserVideoByYoutubeId' | 'findCacheByDedupKey' | 'findKnownDuration'>,
    private readonly idempotencyService: Pick<IdempotencyService, 'computeContentKey'>,
    private readonly evalUsers: Pick<EvalUserReader, 'isEvalUser'>,
    private readonly durations: Pick<BillingGate, 'durationOf'>,
  ) {}

  /** Throws InvalidYouTubeUrlError, or MetadataUnavailableError when the length cannot be told. */
  async estimate(userId: string, url: string): Promise<VideoEstimate> {
    const youtubeId = extractYoutubeId(url);
    if (!youtubeId) throw new InvalidYouTubeUrlError();
    const known = await this.videoRepository.findKnownDuration(youtubeId);
    if (await this.servedFree(userId, youtubeId)) {
      return { youtubeId, durationSec: known ?? 0, tokens: 0, free: true };
    }
    const durationSec = await this.durations.durationOf(youtubeId, known);
    return { youtubeId, durationSec, tokens: estimateTokens(durationSec), free: false };
  }

  private async servedFree(userId: string, youtubeId: string): Promise<boolean> {
    if (await this.evalUsers.isEvalUser(userId)) return false;
    if (await this.videoRepository.findUserVideoByYoutubeId(userId, youtubeId)) return true;
    const dedupKey = this.idempotencyService.computeContentKey({ youtubeId, version: 1 });
    const row = await this.videoRepository.findCacheByDedupKey(dedupKey);
    return row?.status === 'completed';
  }
}
