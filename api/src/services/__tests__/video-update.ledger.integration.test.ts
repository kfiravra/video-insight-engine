/**
 * D-LIB through the minutes ledger: "update to latest version" costs what a
 * new video at the current version would. A completed current version is a
 * free update (the hold goes back); one that needs a run is held and settled
 * when the run completes; a user the ledger will not pay for is still served
 * a completed current version and gets 402 only when a run would happen.
 * Real VideoService, VideoSubmissionService and LedgerEnforcement on the test
 * replica set; only the pipeline dispatch is stubbed.
 */
import { describe, it, expect, beforeAll, beforeEach, vi } from 'vitest';
import { ObjectId } from 'mongodb';
import type { FastifyBaseLogger } from 'fastify';
import { getTestDb, mockLogger } from '../../test/setup.js';
import { config } from '../../config.js';
import { createBillingIndexes } from '../../plugins/mongodb.js';
import { IdempotencyRepository } from '../../repositories/idempotency.repository.js';
import { VideoRepository } from '../../repositories/video.repository.js';
import { InsufficientMinutesError } from '../../utils/errors.js';
import { IdempotencyService, computeContentKey } from '../idempotency.service.js';
import { VideoService } from '../video.service.js';
import { VideoSubmissionService } from '../video-submission.service.js';
import { noOpDispatchGuard } from '../dispatch-guard.service.js';
import { insertUser, makeBillingHarness, makeEnforcement } from '../billing/__tests__/billing-fixtures.js';
import type { BillingHarness } from '../billing/__tests__/billing-fixtures.js';
import type { LedgerEnforcement } from '../billing/ledger-enforcement.js';
import type { QueuePublisher } from '../queue-publisher.service.js';
import type { SummarizerClient } from '../summarizer-client.js';

const YOUTUBE_ID = 'dQw4w9WgXcQ';
const URL = `https://www.youtube.com/watch?v=${YOUTUBE_ID}`;
const CURRENT_KEY = computeContentKey({ youtubeId: YOUTUBE_ID, providers: undefined, version: 1 });
const logger = mockLogger as unknown as FastifyBaseLogger;

describe('update to latest version on the minutes ledger (D-LIB)', () => {
  let h: BillingHarness;
  let enforcement: LedgerEnforcement;
  let submissions: VideoSubmissionService;
  let userId: string;
  let oldRow: ObjectId;
  let entryId: string;

  const cache = () => getTestDb().collection('videoSummaryCache');
  const ledgerCount = (type: string) => getTestDb().collection('ledgerEntries').countDocuments({ type });
  const seedRow = async (fields: Record<string, unknown>): Promise<ObjectId> => {
    const { insertedId } = await cache().insertOne({
      youtubeId: YOUTUBE_ID, url: URL, version: 1, isLatest: true, retryCount: 0, duration: 600,
      createdAt: new Date(), updatedAt: new Date(), ...fields,
    });
    return insertedId;
  };
  const update = () => submissions.submit({ userId, url: URL, updateToLatest: true, requestId: 'client-req' });
  const entryRow = async (): Promise<ObjectId | undefined> =>
    (await getTestDb().collection('userVideos').findOne({ _id: new ObjectId(entryId) }))?.videoSummaryId;
  /** Spend the free video on another video, so the ledger will not pay for this one. */
  const spendFreeVideo = () => h.gate.admit({ requestId: 'other-run', userId, youtubeId: 'othervideo1', durationSec: 300 });

  beforeAll(async () => {
    await createBillingIndexes(getTestDb());
  });

  beforeEach(async () => {
    h = makeBillingHarness(getTestDb());
    const videoRepository = new VideoRepository(getTestDb());
    enforcement = makeEnforcement(getTestDb(), h, videoRepository);
    const videoService = new VideoService(
      videoRepository,
      { triggerSummarization: vi.fn() } as unknown as SummarizerClient,
      { publishVideoJob: vi.fn() } as unknown as QueuePublisher,
      new IdempotencyService(new IdempotencyRepository(getTestDb()), logger),
      noOpDispatchGuard,
      { isEvalUser: vi.fn().mockResolvedValue(false), mayRunCold: vi.fn().mockResolvedValue(false) },
      logger,
    );
    submissions = new VideoSubmissionService(
      videoService, new IdempotencyService(new IdempotencyRepository(getTestDb()), logger), videoRepository, logger, enforcement,
    );
    userId = await insertUser(getTestDb());
    oldRow = await seedRow({ status: 'completed', pipelineVersion: 'v0-old', dedupKey: 'old-version-key' });
    const { insertedId } = await getTestDb().collection('userVideos').insertOne({
      userId: new ObjectId(userId), videoSummaryId: oldRow, youtubeId: YOUTUBE_ID, folderId: null, status: 'completed', createdAt: new Date(),
    });
    entryId = insertedId.toHexString();
  });

  it('should be free when the current version is already completed: no hold is kept', async () => {
    await seedRow({ status: 'completed', pipelineVersion: config.PIPELINE_VERSION, dedupKey: CURRENT_KEY });

    const result = await update();

    expect(result).toMatchObject({ outcome: 'created', result: { cached: true } });
    expect([(await h.billing.summary(userId)).holds, await ledgerCount('debit')]).toEqual([[], 0]);
  });

  it('should hold for a run of the current version and settle it when the run completes', async () => {
    const result = await update();
    const newRow = await entryRow();
    const heldBefore = (await h.billing.summary(userId)).holds.length;

    await enforcement.runFinished(newRow?.toHexString() ?? '', 'completed');

    expect([result.outcome, heldBefore, await ledgerCount('debit'), newRow?.equals(oldRow)]).toEqual(['created', 1, 1, false]);
    expect(await cache().findOne({ _id: oldRow })).toMatchObject({ status: 'completed', pipelineVersion: 'v0-old' });
  });

  it('should serve a completed current version to a user the ledger will not pay for', async () => {
    const current = await seedRow({ status: 'completed', pipelineVersion: config.PIPELINE_VERSION, dedupKey: CURRENT_KEY });
    await spendFreeVideo();

    const result = await update();

    expect([result.outcome, (await entryRow())?.equals(current)]).toEqual(['created', true]);
  });

  it('should answer 402 when the update needs a run the ledger will not pay for, leaving the copy as it was', async () => {
    await spendFreeVideo();

    await expect(update()).rejects.toBeInstanceOf(InsufficientMinutesError);
    expect([(await entryRow())?.equals(oldRow), await cache().countDocuments()]).toEqual([true, 1]);
  });
});
