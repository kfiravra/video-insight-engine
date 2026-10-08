import { FastifyInstance, type FastifyBaseLogger } from 'fastify';
import { z } from 'zod';
import { ObjectId } from 'mongodb';
import { isValidInternalSecret } from '../utils/internal-auth.js';
import { getUtcDateKey } from '../repositories/user-cost.repository.js';
import type { VideoRepository } from '../repositories/video.repository.js';

const videoStatusSchema = z.object({
  type: z.literal('video.status'),
  payload: z.object({
    videoSummaryId: z.string(),
    userId: z.string().optional(),
    status: z.enum(['pending', 'processing', 'completed', 'failed']),
    progress: z.number().optional(),
    message: z.string().optional(),
    error: z.string().optional().nullable(),
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

const reconcileQuerySchema = z.object({
  date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).optional(),
  userId: z.string().regex(/^[a-f0-9]{24}$/i, 'userId must be a 24-char hex ObjectId').optional(),
});

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

export async function internalRoutes(fastify: FastifyInstance) {
  const {
    costMonitorService,
    idempotencyService,
    dispatchGuardService,
    userDeletionService,
    videoRepository,
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
      const { videoSummaryId, status, userId } = event.payload;

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

      // On terminal status (completed OR failed), reconcile the user's day
      // from llm_usage so the cost gate reflects actual spend and the upfront
      // reservation gets corrected. Without reconciling failures, a failed
      // video's reservation would linger until the nightly cron.
      // This IS the canonical refund-on-failure for the per-user gate; do not
      // add a separate `refundReservation` path on failure or it will double-refund.
      if (status === 'completed' || status === 'failed') {
        const targetUsers = new Set<string>();
        if (userId) targetUsers.add(userId);
        if (targetUsers.size === 0) {
          const userVideos = await fastify.mongo.db
            .collection('userVideos')
            .find({ videoSummaryId: new ObjectId(videoSummaryId) })
            .toArray();
          for (const uv of userVideos) {
            if (uv.userId) {
              targetUsers.add(uv.userId.toHexString());
            }
          }
        }

        const todayKey = getUtcDateKey();
        await Promise.all(
          Array.from(targetUsers).map(async (uid) => {
            try {
              await costMonitorService.reconcileUserDay(uid, todayKey);
            } catch (err) {
              req.log.warn({ err, userId: uid }, 'reconcileUserDay failed on completion');
            }
          }),
        );
        // Last: the reconcile lookup above still needs the failed row's library entries.
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

  // POST /internal/reconcile-costs — nightly cron rebuilds the userCosts cache from llm_usage.
  // Supply ?date=YYYY-MM-DD to reconcile a specific UTC day, or ?userId=... for a single user.
  fastify.post<{
    Querystring: z.infer<typeof reconcileQuerySchema>;
  }>('/reconcile-costs', async (req, reply) => {
    if (!isValidInternalSecret(req.headers['x-internal-secret'])) {
      return reply.status(401).send({ error: 'Unauthorized' });
    }

    const parsed = reconcileQuerySchema.safeParse(req.query);
    if (!parsed.success) {
      return reply.status(400).send({
        error: 'Bad Request',
        message: parsed.error.errors[0]?.message || 'Invalid query',
      });
    }

    const dateKey = parsed.data.date ?? getUtcDateKey();
    if (parsed.data.userId) {
      const total = await costMonitorService.reconcileUserDay(parsed.data.userId, dateKey);
      return { dateKey, userId: parsed.data.userId, totalCostUsd: total };
    }

    const result = await costMonitorService.reconcileAllUsersForDay(dateKey);
    return { dateKey, ...result };
  });

  // POST /internal/run-deletions — sweep `users.hardDeleteAt <= now()` and
  // run the GDPR cascade for each. Driven by an external cron (k8s CronJob,
  // GitHub Actions schedule, host crontab — anything that can curl). Daily
  // is the recommended cadence; running more often is harmless because
  // every step is idempotent. See `docs/GDPR.md` for runbook.
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
