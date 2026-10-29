/**
 * D-LIB: an update re-points the user's entry at the current version's row
 * and remembers the completed copy it left while that row's run can still
 * fail. The summarizer's terminal status relay settles it: FAILED hands the
 * entry back to that copy (the update is on offer again), COMPLETED forgets
 * the copy. Real VideoService and LibraryUpdateRepository on the test replica
 * set; `relay` does to the rows what POST /internal/status does.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { ObjectId } from 'mongodb';
import type { FastifyBaseLogger } from 'fastify';
import { getTestDb, mockLogger } from '../../test/setup.js';
import { config } from '../../config.js';
import { LibraryUpdateRepository } from '../../repositories/library-update.repository.js';
import { VideoRepository } from '../../repositories/video.repository.js';
import { computeContentKey } from '../idempotency.service.js';
import { VideoService } from '../video.service.js';
import { noOpDispatchGuard } from '../dispatch-guard.service.js';
import type { IdempotencyService } from '../idempotency.service.js';
import type { QueuePublisher } from '../queue-publisher.service.js';
import type { SummarizerClient } from '../summarizer-client.js';

const YOUTUBE_ID = 'dQw4w9WgXcQ';
const URL = `https://www.youtube.com/watch?v=${YOUTUBE_ID}`;
const CURRENT_KEY = computeContentKey({ youtubeId: YOUTUBE_ID, providers: undefined, version: 1 });

describe('a run an update moved entries onto, when it ends (D-LIB)', () => {
  let service: VideoService;
  let updates: LibraryUpdateRepository;
  let oldRow: ObjectId;

  const cache = () => getTestDb().collection('videoSummaryCache');
  const entries = () => getTestDb().collection('userVideos');
  const seedRow = async (fields: Record<string, unknown>): Promise<ObjectId> => {
    const { insertedId } = await cache().insertOne({
      youtubeId: YOUTUBE_ID, url: URL, version: 1, isLatest: true, retryCount: 0, createdAt: new Date(), updatedAt: new Date(), ...fields,
    });
    return insertedId;
  };
  const seedCopy = async (userId: string): Promise<string> => {
    const { insertedId } = await entries().insertOne({
      userId: new ObjectId(userId), videoSummaryId: oldRow, youtubeId: YOUTUBE_ID, folderId: null,
      status: 'completed', title: 'Old title', createdAt: new Date(),
    });
    return insertedId.toHexString();
  };
  const entry = (id: string) => entries().findOne({ _id: new ObjectId(id) });
  const updateFor = (userId: string) => service.createVideo(userId, URL, { queuePlan: 'free', updateToLatest: true });
  /** The terminal status relay: the row and its entries take the status, then the update settle runs. */
  const relay = async (rowId: string, status: 'completed' | 'failed') => {
    await cache().updateOne({ _id: new ObjectId(rowId) }, { $set: { status } });
    await entries().updateMany({ videoSummaryId: new ObjectId(rowId) }, { $set: { status } });
    return status === 'failed' ? updates.restoreFailedUpdates(rowId) : updates.clearSettledUpdates(rowId);
  };

  beforeEach(async () => {
    service = new VideoService(
      new VideoRepository(getTestDb()),
      { triggerSummarization: vi.fn() } as unknown as SummarizerClient,
      { publishVideoJob: vi.fn() } as unknown as QueuePublisher,
      { computeContentKey } as unknown as IdempotencyService,
      noOpDispatchGuard,
      { isEvalUser: vi.fn().mockResolvedValue(false), mayRunCold: vi.fn().mockResolvedValue(false) },
      mockLogger as unknown as FastifyBaseLogger,
    );
    updates = new LibraryUpdateRepository(getTestDb());
    oldRow = await seedRow({ status: 'completed', pipelineVersion: 'v0-old', dedupKey: 'old-version-key', title: 'Old title' });
  });

  it('should hand a failed created update back to the copy it left, with the update on offer again', async () => {
    const userId = new ObjectId().toHexString();
    const entryId = await seedCopy(userId);
    const { video } = await updateFor(userId);

    const outcome = await relay(video.videoSummaryId, 'failed');

    const restored = await service.getVideo(userId, entryId);
    expect(outcome).toEqual({ restored: 1, stranded: 0 });
    expect([restored.videoSummaryId, restored.status, restored.updateAvailable]).toEqual([oldRow.toHexString(), 'completed', true]);
    expect(await entry(entryId)).not.toHaveProperty('previousVideoSummaryId');
  });

  it('should hand back every user attached to a current version whose run failed', async () => {
    const running = await seedRow({ status: 'processing', dedupKey: CURRENT_KEY });
    const users = [new ObjectId().toHexString(), new ObjectId().toHexString()];
    const entryIds = [await seedCopy(users[0]), await seedCopy(users[1])];
    const attached = [await updateFor(users[0]), await updateFor(users[1])];

    await relay(running.toHexString(), 'failed');

    const back = await Promise.all(entryIds.map(async (id) => (await entry(id))?.videoSummaryId.equals(oldRow)));
    expect([attached, back]).toEqual([[expect.objectContaining({ attached: true }), expect.objectContaining({ attached: true })], [true, true]]);
  });

  it('should keep the entry on the completed current version and forget the copy it left', async () => {
    const userId = new ObjectId().toHexString();
    const entryId = await seedCopy(userId);
    const { video } = await updateFor(userId);
    const remembered = (await entry(entryId))?.previousVideoSummaryId;

    await relay(video.videoSummaryId, 'completed');

    const settled = await entry(entryId);
    expect([remembered?.equals(oldRow), settled?.videoSummaryId.toHexString(), 'previousVideoSummaryId' in (settled ?? {})])
      .toEqual([true, video.videoSummaryId, false]);
  });

  it('should change nothing when the FAILED relay comes twice', async () => {
    const userId = new ObjectId().toHexString();
    const entryId = await seedCopy(userId);
    const { video } = await updateFor(userId);
    await relay(video.videoSummaryId, 'failed');
    const afterFirst = await entry(entryId);

    const second = await relay(video.videoSummaryId, 'failed');

    expect([second, await entry(entryId)]).toEqual([{ restored: 0, stranded: 0 }, afterFirst]);
  });

  it('should keep the failed state when the copy the update left is gone', async () => {
    const userId = new ObjectId().toHexString();
    const entryId = await seedCopy(userId);
    const { video } = await updateFor(userId);
    await cache().deleteOne({ _id: oldRow });

    const outcome = await relay(video.videoSummaryId, 'failed');

    expect([outcome, (await entry(entryId))?.status]).toEqual([{ restored: 0, stranded: 1 }, 'failed']);
  });

  it('should remember no previous copy for a free re-point to a completed current version', async () => {
    await seedRow({ status: 'completed', pipelineVersion: config.PIPELINE_VERSION, dedupKey: CURRENT_KEY });
    const userId = new ObjectId().toHexString();
    const entryId = await seedCopy(userId);

    const result = await updateFor(userId);

    expect([result.cached, 'previousVideoSummaryId' in ((await entry(entryId)) ?? {})]).toEqual([true, false]);
  });
});
