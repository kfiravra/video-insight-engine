/**
 * D-LIB: opening a copy the user already has is always free. A copy an older
 * pipeline wrote is served as is with `updateAvailable: true`. An update
 * (`updateToLatest`) goes the new-video way at the current dedupKey and
 * re-points the user's entry at the result; the old row is never touched. A
 * video the user already has, filed into another folder, never forces a run.
 * Real VideoRepository on the test replica set.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { ObjectId } from 'mongodb';
import type { FastifyBaseLogger } from 'fastify';
import { getTestDb, mockLogger } from '../../test/setup.js';
import { config } from '../../config.js';
import { VideoRepository } from '../../repositories/video.repository.js';
import { RunNotAllowedError } from '../../utils/errors.js';
import { computeContentKey } from '../idempotency.service.js';
import { VideoService } from '../video.service.js';
import { noOpDispatchGuard } from '../dispatch-guard.service.js';
import type { CreateVideoOptions } from '../video.service.js';
import type { IdempotencyService } from '../idempotency.service.js';
import type { QueuePublisher } from '../queue-publisher.service.js';
import type { SummarizerClient } from '../summarizer-client.js';

const YOUTUBE_ID = 'dQw4w9WgXcQ';
const URL = `https://www.youtube.com/watch?v=${YOUTUBE_ID}`;
/** The shared row the current version's submits meet. */
const CURRENT_KEY = computeContentKey({ youtubeId: YOUTUBE_ID, providers: undefined, version: 1 });
/** A row an older pipeline wrote under its own version's key. */
const OLD = { status: 'completed', pipelineVersion: 'v0-old', dedupKey: 'old-version-key' };
const CURRENT = { status: 'completed', pipelineVersion: config.PIPELINE_VERSION, dedupKey: CURRENT_KEY };

describe('VideoService — library copies and update to latest (D-LIB)', () => {
  let service: VideoService;
  let triggerSummarization: ReturnType<typeof vi.fn>;
  let publishVideoJob: ReturnType<typeof vi.fn>;
  const userId = new ObjectId().toHexString();
  const folderA = new ObjectId().toHexString();
  const folderB = new ObjectId().toHexString();

  const cache = () => getTestDb().collection('videoSummaryCache');
  const entries = () => getTestDb().collection('userVideos');
  const seedRow = async (fields: Record<string, unknown>): Promise<ObjectId> => {
    const { insertedId } = await cache().insertOne({
      youtubeId: YOUTUBE_ID, url: URL, version: 1, isLatest: true, retryCount: 0,
      title: 'Copy', createdAt: new Date(), updatedAt: new Date(), ...fields,
    });
    return insertedId;
  };
  const seedCopy = async (summaryId: ObjectId, folderId: string | null = null): Promise<string> => {
    const { insertedId } = await entries().insertOne({
      userId: new ObjectId(userId), videoSummaryId: summaryId, youtubeId: YOUTUBE_ID,
      folderId: folderId ? new ObjectId(folderId) : null, status: 'completed', createdAt: new Date(),
    });
    return insertedId.toHexString();
  };
  const entry = (id: string) => entries().findOne({ _id: new ObjectId(id) });
  const row = (id: ObjectId) => cache().findOne({ _id: id });
  const submit = (extra: Partial<CreateVideoOptions> = {}) =>
    service.createVideo(userId, URL, { queuePlan: 'free', ...extra });
  const dispatches = (): number => triggerSummarization.mock.calls.length + publishVideoJob.mock.calls.length;

  beforeEach(() => {
    triggerSummarization = vi.fn();
    publishVideoJob = vi.fn();
    service = new VideoService(
      new VideoRepository(getTestDb()),
      { triggerSummarization } as unknown as SummarizerClient,
      { publishVideoJob } as unknown as QueuePublisher,
      { computeContentKey } as unknown as IdempotencyService,
      noOpDispatchGuard,
      { isEvalUser: vi.fn().mockResolvedValue(false), mayRunCold: vi.fn().mockResolvedValue(false) },
      mockLogger as unknown as FastifyBaseLogger,
    );
  });

  describe('opening the user\'s own copy', () => {
    it('should serve a copy an older pipeline wrote free, with the update on offer', async () => {
      await seedCopy(await seedRow(OLD));

      const result = await submit();

      expect(result).toMatchObject({ cached: true, alreadyExists: true, updateAvailable: true });
      expect(dispatches()).toBe(0);
    });

    it.each([
      ['a current copy', CURRENT],
      ['an unstamped (current-legacy) copy', { status: 'completed', dedupKey: CURRENT_KEY }],
    ])('should not offer an update for %s', async (_label, fields) => {
      await seedCopy(await seedRow(fields));

      expect(await submit()).not.toHaveProperty('updateAvailable');
    });

    it('should serve an outdated copy to a user the ledger will not pay for', async () => {
      await seedCopy(await seedRow(OLD));

      expect(await submit({ serveOnly: true })).toMatchObject({ cached: true, updateAvailable: true });
    });
  });

  describe('updating to the latest version', () => {
    it('should run the current version on a new row and re-point the entry, leaving the old row untouched', async () => {
      const oldRow = await seedRow(OLD);
      const entryId = await seedCopy(oldRow);

      const result = await submit({ updateToLatest: true });

      const updated = await entry(entryId);
      expect([result.cached, result.video.id, dispatches(), await entries().countDocuments()]).toEqual([false, entryId, 1, 1]);
      expect(updated?.videoSummaryId.equals(oldRow)).toBe(false);
      expect(await row(oldRow)).toMatchObject({ status: 'completed', pipelineVersion: 'v0-old' });
    });

    it('should re-point the entry at a completed current version for free', async () => {
      const current = await seedRow(CURRENT);
      const entryId = await seedCopy(await seedRow(OLD));

      const result = await submit({ updateToLatest: true });

      expect([result.cached, dispatches()]).toEqual([true, 0]);
      expect(await entry(entryId)).toMatchObject({ videoSummaryId: current, status: 'completed' });
    });

    it('should attach the entry to a current version still running, for free', async () => {
      const running = await seedRow({ status: 'processing', dedupKey: CURRENT_KEY });
      const entryId = await seedCopy(await seedRow(OLD));

      const result = await submit({ updateToLatest: true });

      expect(result).toMatchObject({ cached: false, attached: true });
      expect([dispatches(), (await entry(entryId))?.videoSummaryId.equals(running)]).toEqual([0, true]);
    });

    it('should serve a current copy free even when the user asks for the update', async () => {
      await seedCopy(await seedRow(CURRENT));

      const result = await submit({ updateToLatest: true });

      expect([result.cached, dispatches()]).toEqual([true, 0]);
    });

    it('should serve a completed current version to a user the ledger will not pay for', async () => {
      await seedRow(CURRENT);
      await seedCopy(await seedRow(OLD));

      expect((await submit({ serveOnly: true, updateToLatest: true })).cached).toBe(true);
    });

    it('should refuse, before any write, an update that needs a run the ledger will not pay for', async () => {
      const oldRow = await seedRow(OLD);
      const entryId = await seedCopy(oldRow);

      await expect(submit({ serveOnly: true, updateToLatest: true })).rejects.toBeInstanceOf(RunNotAllowedError);
      expect([dispatches(), await cache().countDocuments(), (await entry(entryId))?.videoSummaryId.equals(oldRow)]).toEqual([0, 1, true]);
    });
  });

  describe('filing a video the user already has into another folder', () => {
    it('should file the user\'s outdated copy free, with the update on offer, when the current version would need a run', async () => {
      const oldRow = await seedRow(OLD);
      await seedCopy(oldRow, folderA);

      const result = await submit({ folderId: folderB });

      expect(result).toMatchObject({ cached: true, updateAvailable: true, video: { videoSummaryId: oldRow.toHexString() } });
      expect([dispatches(), await cache().countDocuments(), await entries().countDocuments()]).toEqual([0, 1, 2]);
    });

    it('should prefer a completed current version over the user\'s outdated copy', async () => {
      const current = await seedRow(CURRENT);
      await seedCopy(await seedRow(OLD), folderA);

      const result = await submit({ folderId: folderB });

      expect(result).toMatchObject({ cached: true, video: { videoSummaryId: current.toHexString() } });
      expect(result).not.toHaveProperty('updateAvailable');
    });

    it('should prefer a current version still running over the user\'s outdated copy', async () => {
      await seedRow({ status: 'pending', dedupKey: CURRENT_KEY });
      await seedCopy(await seedRow(OLD), folderA);

      expect([await submit({ folderId: folderB }), dispatches()]).toEqual([expect.objectContaining({ attached: true }), 0]);
    });

    it('should file the user\'s copy rather than retry a failed current version', async () => {
      const oldRow = await seedRow(OLD);
      await seedRow({ status: 'failed', dedupKey: CURRENT_KEY });
      await seedCopy(oldRow, folderA);

      const result = await submit({ folderId: folderB });

      expect([result.cached, result.video.videoSummaryId, dispatches()]).toEqual([true, oldRow.toHexString(), 0]);
    });

    it('should file the user\'s copy for a user the ledger will not pay for', async () => {
      await seedCopy(await seedRow(OLD), folderA);

      expect((await submit({ folderId: folderB, serveOnly: true })).cached).toBe(true);
    });
  });

  describe('a stale shared row at the current dedupKey (an api / summarizer version split)', () => {
    const STALE_SHARED = { status: 'completed', pipelineVersion: 'v0-old', dedupKey: CURRENT_KEY };

    it('should serve it free to a user who already has it, with the update on offer', async () => {
      const shared = await seedRow(STALE_SHARED);
      await seedCopy(shared, folderA);

      const result = await submit({ folderId: folderB });

      expect(result).toMatchObject({ cached: true, updateAvailable: true, video: { videoSummaryId: shared.toHexString() } });
      expect(dispatches()).toBe(0);
    });

    it('should regenerate it on the same row for a user who never had the video, and set it pending', async () => {
      const shared = await seedRow(STALE_SHARED);

      const result = await submit({ folderId: folderB });

      expect([result.cached, dispatches(), (await row(shared))?.status]).toEqual([false, 1, 'pending']);
    });
  });

  describe('getVideo', () => {
    it.each([
      ['a completed copy an older pipeline wrote', OLD, true],
      ['a current copy', CURRENT, undefined],
      ['an unstamped copy', { status: 'completed' }, undefined],
      ['an outdated copy being regenerated', { status: 'processing', pipelineVersion: 'v0-old' }, undefined],
    ])('should flag updateAvailable for %s only when an update is on offer', async (_label, fields, expected) => {
      const videoId = await seedCopy(await seedRow(fields));

      const video = await service.getVideo(userId, videoId);

      expect(video.updateAvailable).toBe(expected);
    });
  });
});
