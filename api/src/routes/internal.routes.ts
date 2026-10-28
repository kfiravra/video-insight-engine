import { FastifyInstance, type FastifyBaseLogger } from 'fastify';
import { z } from 'zod';
import { ObjectId } from 'mongodb';
import { isValidInternalSecret } from '../utils/internal-auth.js';
import type { VideoRepository } from '../repositories/video.repository.js';
import type { LibraryUpdateRepository } from '../repositories/library-update.repository.js';

const videoStatusSchema = z.object({
  type: z.literal('video.status'),
  payload: z.object({
    videoSummaryId: z.string(),
    userId: z.string().optional(),
    status: z.enum(['pending', 'processing', 'completed', 'failed']),
    progress: z.number().optional(),
    message: z.string().optional(),
    error: z.string().optional().nullable(),
    /** `completed` served from the summarizer's response cache: no run happened (I3). */
    cacheHit: z.boolean().optional(),
  }),
});

const expansionStatusSchema = z.object({
  type: z.literal('expansion.status'),
  payload: z.object({
    videoSummaryId: z.string(),
    targetType: z.enum(['section', 'concept']),
    targetId: z.string(),
    status: z.enum(['pending', 'processing', 'completed', 'failed']),
    error: z.string().optional().nullable(),
  }),
});

const statusEventSchema = z.union([videoStatusSchema, expansionStatusSchema]);

const runDeletionsBodySchema = z
  .object({
    /** Cap how many users this run will process. Defaults to the service-side batch size. */
    limit: z.number().int().positive().max(500).optional(),
  })
  .optional();

/**
 * 1d.8: a re-run becomes the served version only once it completes; a failed
 * one hands its library entries back to the completed version still served.
 * Both repository calls are idempotent, so duplicate status events are safe.
 * Best-effort: the status relay itself must not fail on it.
 */
async function settleServedVersion(
  videoRepository: VideoRepository,
  videoSummaryId: string,
  status: 'completed' | 'failed',
  log: FastifyBaseLogger,
): Promise<void> {
  try {
    if (status === 'completed') {
      await videoRepository.promoteCompletedVersion(videoSummaryId);
    } else {
      await videoRepository.restoreServedVersion(videoSummaryId);
    }
  } catch (err) {
    log.warn({ err, videoSummaryId, status }, 'served-version settle failed');
  }
}

/**
 * D-LIB: the entries an update to the latest version moved onto this row go
 * back to the copy they left when its run fails, so the update stays on offer
 * and can be retried, and forget that copy when it completes. Idempotent and
 * best-effort, like the served-version settle; it runs first, so a failed
 * version row's restore cannot carry such an entry elsewhere.
 */
async function settleLibraryUpdates(
  libraryUpdates: LibraryUpdateRepository,
  videoSummaryId: string,
  status: 'completed' | 'failed',
  log: FastifyBaseLogger,
): Promise<void> {
  try {
    if (status === 'completed') {
      await libraryUpdates.clearSettledUpdates(videoSummaryId);
      return;
    }
    const { stranded } = await libraryUpdates.restoreFailedUpdates(videoSummaryId);
    if (stranded > 0) log.warn({ videoSummaryId, stranded }, 'update failed and the copy it left is gone; entries keep the failed state');
  } catch (err) {
    log.warn({ err, videoSummaryId, status }, 'library-update settle failed');
  }
}

export async function internalRoutes(fastify: FastifyInstance) {
  const {
    idempotencyService,
    dispatchGuardService,
    userDeletionService,
    videoRepository,
    libraryUpdateRepository,
    billingEnforcement,
  } = fastify.container;

  // POST /internal/status - Receive status updates from summarizer/agent
  fastify.post<{
    Body: z.infer<typeof statusEventSchema>;
  }>('/status', async (req, reply) => {
    // Validate internal secret
    if (!isValidInternalSecret(req.headers['x-internal-secret'])) {
      return reply.status(401).send({ error: 'Unauthorized' });
    }

    const parsed = statusEventSchema.safeParse(req.body);
    if (!parsed.success) {
      return reply.status(400).send({
        error: 'Bad Request',
        message: parsed.error.errors[0]?.message || 'Invalid status event',
      });
    }

    const event = parsed.data;

    if (event.type === 'video.status') {
      // Update userVideos status to match
      const { videoSummaryId, status, userId, cacheHit } = event.payload;

      await fastify.mongo.db.collection('userVideos').updateMany(
        { videoSummaryId: new ObjectId(videoSummaryId) },
        { $set: { status, updatedAt: new Date() } }
      );

      // If we have userId, broadcast to that user
      if (userId) {
        fastify.broadcast(userId, event);
      } else {
        // Find all users with this video and broadcast to each
        const userVideos = await fastify.mongo.db
          .collection('userVideos')
          .find({ videoSummaryId: new ObjectId(videoSummaryId) })
          .toArray();

        for (const uv of userVideos) {
          const userIdStr = uv.userId.toHexString();
          fastify.broadcast(userIdStr, {
            ...event,
            payload: {
              ...event.payload,
              userVideoId: uv._id.toHexString(),
            },
          });
        }
      }

      // On FAILED, drop any idempotency keys pointing at this summary so the
      // user can retry immediately rather than wait out the TTL. Fire-and-
      // forget — failure here only degrades dedup, doesn't break anything.
      //
      // Also force-release the dispatch guard so branch D (failed-retry) in
      // createVideo can re-dispatch instead of being silenced by the 900s TTL.
      // We gate the release on `videoRepository.tryClaimDispatchRelease(...)`:
      // that's an atomic Mongo find-and-update guaranteeing only ONE concurrent
      // FAILED event (per terminal-failure transition on the row) will reach
      // the release call. The two gates inside `tryClaimDispatchRelease` —
      // `status: 'failed'` and `dispatchGuardReleasedAt < updatedAt` — together
      // make duplicate FAILED events idempotent AND make a stale FAILED that
      // arrives after a user-driven retry a no-op.
      //
      // The blind DEL (`token: null`) is still the right semantic here because
      // the original publisher's token isn't available cross-process. The
      // residual retry-between-events race (microseconds between the Mongo
      // claim and the Redis release) is documented inside
      // `tryClaimDispatchRelease` and bounded by the summarizer's own
      // per-`video_summary_id` lock.
      if (status === 'failed') {
        idempotencyService.invalidateByVideoSummaryId(videoSummaryId).catch((err) => {
          req.log.warn({ err, videoSummaryId }, 'idempotency invalidate on failure failed');
        });

        // Awaited (not fire-and-forget) so we can gate the Redis release on
        // the Mongo claim. The added latency is one Mongo round-trip on the
        // FAILED status callback path — an internal-only, low-rate endpoint
        // where the additional ~5-15ms is invisible.
        let claimed: boolean;
        try {
          claimed = await videoRepository.tryClaimDispatchRelease(videoSummaryId);
        } catch (err) {
          // If the claim itself errored (Mongo blip), fall back to the
          // pre-fix behaviour: best-effort blind release. Worst case is the
          // old race window, which is strictly no worse than where we were.
          req.log.warn(
            { err, videoSummaryId },
            'tryClaimDispatchRelease failed; falling back to unconditional release',
          );
          claimed = true;
        }

        if (claimed) {
          await dispatchGuardService.release(videoSummaryId, null).catch((err) => {
            req.log.warn(
              { err, videoSummaryId },
              'dispatch-guard release on failure failed',
            );
          });
        } else {
          req.log.debug(
            { videoSummaryId },
            'dispatch-guard release skipped (duplicate FAILED event or row no longer failed)',
          );
        }
      }

      if (status === 'completed' || status === 'failed') {
        // Minutes ledger: settle (completed) / release (failed, or a cache
        // hit that ran nothing) the run's hold. Awaited, never throws; a hold
        // it misses is the repair job's.
        await billingEnforcement.runFinished(videoSummaryId, status === 'completed' && cacheHit ? 'cached' : status);
        await settleLibraryUpdates(libraryUpdateRepository, videoSummaryId, status, req.log);
        await settleServedVersion(videoRepository, videoSummaryId, status, req.log);
      }
    } else if (event.type === 'expansion.status') {
      // Expansion status - broadcast to all users who have this video
      const { videoSummaryId } = event.payload;

      const userVideos = await fastify.mongo.db
        .collection('userVideos')
        .find({ videoSummaryId: new ObjectId(videoSummaryId) })
        .toArray();

      for (const uv of userVideos) {
        const userIdStr = uv.userId.toHexString();
        fastify.broadcast(userIdStr, event);
      }
    }

    return { received: true };
  });

  // POST /internal/run-deletions — sweep `users.hardDeleteAt <= now()` and
  // run the GDPR cascade for each. The billing repair job runs the same sweep
  // on its interval (BILLING_REPAIR_ENABLED); this route is the manual /
  // external-cron entrypoint. Running it more often is harmless because every
  // step is idempotent. See `docs/GDPR.md` for runbook.
  fastify.post<{
    Body: z.infer<typeof runDeletionsBodySchema>;
  }>('/run-deletions', async (req, reply) => {
    if (!isValidInternalSecret(req.headers['x-internal-secret'])) {
      return reply.status(401).send({ error: 'Unauthorized' });
    }

    const parsed = runDeletionsBodySchema.safeParse(req.body);
    if (!parsed.success) {
      return reply.status(400).send({
        error: 'Bad Request',
        message: parsed.error.errors[0]?.message ?? 'Invalid body',
      });
    }

    const result = await userDeletionService.runScheduledDeletions(
      new Date(),
      parsed.data?.limit,
    );
    return result;
  });
}
