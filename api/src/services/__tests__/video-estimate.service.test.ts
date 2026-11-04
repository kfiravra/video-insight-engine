/**
 * GET /api/videos/estimate's service (D19): what a submit would hold before
 * it runs. `free` (0 tokens, no metadata pre-flight) for the user's own copy
 * in any folder or a completed row at the current dedupKey; an eval account
 * is never free; otherwise the estimate of the video's length — the stored
 * length when one is on record, else the pre-flight's. Real repository
 * queries on the test replica set; only the metadata pre-flight is stubbed.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { ObjectId } from 'mongodb';
import type { FastifyBaseLogger } from 'fastify';
import { getTestDb, mockLogger } from '../../test/setup.js';
import { IdempotencyRepository } from '../../repositories/idempotency.repository.js';
import { VideoRepository } from '../../repositories/video.repository.js';
import { InvalidYouTubeUrlError, MetadataUnavailableError } from '../../utils/errors.js';
import { BillingGate } from '../billing/billing-gate.js';
import { IdempotencyService, computeContentKey } from '../idempotency.service.js';
import { VideoEstimateService } from '../video-estimate.service.js';
import type { BillingService } from '../billing/billing.service.js';

const YOUTUBE_ID = 'dQw4w9WgXcQ';
const URL = `https://www.youtube.com/watch?v=${YOUTUBE_ID}`;
const CURRENT_KEY = computeContentKey({ youtubeId: YOUTUBE_ID, providers: undefined, version: 1 });

describe('VideoEstimateService', () => {
  let service: VideoEstimateService;
  let metadata: { getVideoMetadata: ReturnType<typeof vi.fn> };
  let evalUsers: { isEvalUser: ReturnType<typeof vi.fn> };
  let userId: string;

  const cache = () => getTestDb().collection('videoSummaryCache');
  const seedRow = (fields: Record<string, unknown>) =>
    cache().insertOne({ youtubeId: YOUTUBE_ID, createdAt: new Date(), updatedAt: new Date(), ...fields });
  const ownCopy = async () => {
    const { insertedId } = await seedRow({ status: 'completed', dedupKey: 'an-older-version', duration: 212 });
    await getTestDb().collection('userVideos').insertOne({
      userId: new ObjectId(userId), videoSummaryId: insertedId, youtubeId: YOUTUBE_ID, folderId: new ObjectId(), status: 'completed',
    });
  };

  beforeEach(() => {
    userId = new ObjectId().toHexString();
    metadata = { getVideoMetadata: vi.fn().mockResolvedValue({ duration: 600 }) };
    evalUsers = { isEvalUser: vi.fn().mockResolvedValue(false) };
    service = new VideoEstimateService(
      new VideoRepository(getTestDb()),
      new IdempotencyService(new IdempotencyRepository(getTestDb()), mockLogger as unknown as FastifyBaseLogger),
      evalUsers,
      // Only `durationOf` is used: it never touches the ledger.
      new BillingGate({} as unknown as BillingService, metadata),
    );
  });

  it('should estimate a video nobody has from the metadata pre-flight (10 minutes: 840 tokens)', async () => {
    expect(await service.estimate(userId, URL)).toEqual({ youtubeId: YOUTUBE_ID, durationSec: 600, tokens: 840, free: false });
    expect(metadata.getVideoMetadata).toHaveBeenCalledWith(YOUTUBE_ID);
  });

  it('should read a short youtu.be link', async () => {
    expect(await service.estimate(userId, `https://youtu.be/${YOUTUBE_ID}`)).toMatchObject({ youtubeId: YOUTUBE_ID, tokens: 840 });
  });

  it('should use the stored length of a completed older version and skip the pre-flight (I10)', async () => {
    await seedRow({ status: 'completed', dedupKey: 'an-older-version', duration: 720 });

    expect(await service.estimate(userId, URL)).toEqual({ youtubeId: YOUTUBE_ID, durationSec: 720, tokens: 876, free: false });
    expect(metadata.getVideoMetadata).not.toHaveBeenCalled();
  });

  it('should answer free for the user\'s own copy in any folder, without a pre-flight', async () => {
    await ownCopy();

    expect(await service.estimate(userId, URL)).toEqual({ youtubeId: YOUTUBE_ID, durationSec: 212, tokens: 0, free: true });
    expect(metadata.getVideoMetadata).not.toHaveBeenCalled();
  });

  it('should answer free for a completed row at the current version, someone else\'s run', async () => {
    await seedRow({ status: 'completed', dedupKey: CURRENT_KEY, duration: 300 });

    expect(await service.estimate(userId, URL)).toEqual({ youtubeId: YOUTUBE_ID, durationSec: 300, tokens: 0, free: true });
  });

  it('should answer free with a length of 0 when no length is on record', async () => {
    await seedRow({ status: 'completed', dedupKey: CURRENT_KEY });

    expect(await service.estimate(userId, URL)).toEqual({ youtubeId: YOUTUBE_ID, durationSec: 0, tokens: 0, free: true });
  });

  it('should not promise free for a current-version row still running (the submit decides)', async () => {
    await seedRow({ status: 'processing', dedupKey: CURRENT_KEY });

    expect(await service.estimate(userId, URL)).toMatchObject({ tokens: 840, free: false });
  });

  it('should never answer free to an eval account, even for its own copy', async () => {
    evalUsers.isEvalUser.mockResolvedValue(true);
    await ownCopy();
    await seedRow({ status: 'completed', dedupKey: CURRENT_KEY, duration: 212 });

    expect(await service.estimate(userId, URL)).toEqual({ youtubeId: YOUTUBE_ID, durationSec: 212, tokens: 732, free: false });
    expect(evalUsers.isEvalUser).toHaveBeenCalledWith(userId);
  });

  it('should throw InvalidYouTubeUrlError for a link that is not a YouTube video, looking nothing up', async () => {
    await expect(service.estimate(userId, 'https://example.com/watch?v=abc')).rejects.toBeInstanceOf(InvalidYouTubeUrlError);
    expect([evalUsers.isEvalUser.mock.calls.length, metadata.getVideoMetadata.mock.calls.length]).toEqual([0, 0]);
  });

  it('should pass METADATA_UNAVAILABLE through when the length cannot be told', async () => {
    metadata.getVideoMetadata.mockRejectedValue(new MetadataUnavailableError('unsupported'));

    await expect(service.estimate(userId, URL)).rejects.toMatchObject({ code: 'METADATA_UNAVAILABLE', status: 422 });
  });
});
