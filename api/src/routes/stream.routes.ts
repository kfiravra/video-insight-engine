import { FastifyInstance } from 'fastify';
import { z } from 'zod';
import { ObjectId } from 'mongodb';
import { config } from '../config.js';
import { VideoNotFoundError } from '../utils/errors.js';
import { synthesisCompleteEventSchema } from '../schemas/synthesis-event.schema.js';
import { handleSSEPreflight, setSSECorsHeaders, setSSEResponseHeaders } from '../utils/cors.js';
import { disableSocketInactivityTimeout } from '../utils/sse.js';

const videoSummaryIdParamSchema = z.object({
  videoSummaryId: z.string().refine((val) => ObjectId.isValid(val), 'Invalid ID'),
});

export async function streamRoutes(fastify: FastifyInstance) {
  /**
   * OPTIONS /api/videos/:videoSummaryId/stream
   *
   * Handle CORS preflight for SSE endpoint
   */
  fastify.options('/:videoSummaryId/stream', async (req, reply) => {
    return handleSSEPreflight(req, reply);
  });

  /**
   * GET /api/videos/:videoSummaryId/stream
   *
   * Proxy SSE stream from summarizer service for real-time summarization.
   * Returns character-by-character LLM output as Server-Sent Events.
   */
  fastify.get<{
    Params: z.infer<typeof videoSummaryIdParamSchema>;
  }>('/:videoSummaryId/stream', {
    preHandler: [fastify.authenticate],
  }, async (req, reply) => {
    const { videoSummaryId } = videoSummaryIdParamSchema.parse(req.params);

    // Verify user has access to this video
    const { videoRepository } = fastify.container;
    const hasAccess = await videoRepository.userHasAccessToSummary(req.user.userId, videoSummaryId);
    if (!hasAccess) {
      throw new VideoNotFoundError();
    }

    // Set CORS and SSE headers (reply.raw bypasses Fastify CORS plugin)
    setSSECorsHeaders(req, reply);
    setSSEResponseHeaders(reply);
    // Long-lived stream — exempt from the server-wide socket inactivity timeout
    disableSocketInactivityTimeout(req);

    // Proxy the stream from summarizer
    const summarizerUrl = `${config.SUMMARIZER_URL}/summarize/stream/${videoSummaryId}`;

    // Upstream abort tied to client disconnect: when the browser goes away,
    // tear down the summarizer fetch immediately instead of streaming into
    // the void for the rest of the pipeline run.
    const upstreamAbort = new AbortController();
    req.raw.on('close', () => upstreamAbort.abort());

    try {
      const response = await fetch(summarizerUrl, {
        headers: {
          'Accept': 'text/event-stream',
        },
        signal: upstreamAbort.signal,
      });

      // Issue #1: Improved error handling with proper status codes
      if (!response.ok) {
        const status = response.status;
        const errorMessage = status === 404 ? 'Video not found'
          : status === 500 ? 'Summarizer service error'
          : `Summarizer returned ${status}`;
        const errorCode = status === 404 ? 'NOT_FOUND' : 'SUMMARIZER_ERROR';
        reply.raw.write(`data: ${JSON.stringify({ event: 'error', message: errorMessage, code: errorCode })}\n\n`);
        reply.raw.end();
        return;
      }

      if (!response.body) {
        reply.raw.write(`data: ${JSON.stringify({ event: 'error', message: 'No response body', code: 'NO_BODY' })}\n\n`);
        reply.raw.end();
        return;
      }

      // Stream the response body
      const reader = response.body.getReader();
      const decoder = new TextDecoder();

      // Issue #2: Fixed memory leak - proper cleanup with tracking
      let cleanedUp = false;
      const cleanup = async () => {
        if (cleanedUp) return;
        cleanedUp = true;
        try {
          await reader.cancel();
        } catch (err) {
          req.log.debug({ err }, 'Reader cancel error during cleanup');
        }
      };

      // Handle client disconnect
      req.raw.on('close', () => {
        cleanup();
      });

      // Stream chunks with proper cleanup
      // Parse SSE events to persist metadata early (allows resumption after refresh)
      let buffer = '';
      const pendingWrites: Promise<unknown>[] = [];
      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          const chunk = decoder.decode(value, { stream: true });
          buffer += chunk;

          // Parse complete SSE events from buffer
          const lines = buffer.split('\n');
          // Keep the last incomplete line in buffer
          buffer = lines.pop() || '';

          for (const line of lines) {
            if (!line.startsWith('data: ')) continue;
            const data = line.slice(6);
            if (data === '[DONE]') continue;

            try {
              const event = JSON.parse(data);
              // Persist metadata to DB when received so title is available after refresh
              if (event.event === 'metadata' && event.title) {
                pendingWrites.push(
                  videoRepository.updateCacheEntry(videoSummaryId, {
                    title: event.title,
                    channel: event.channel,
                    thumbnailUrl: event.thumbnailUrl,
                    duration: event.duration,
                  })
                );

                // Broadcast metadata to frontend via WebSocket for sidebar sync
                fastify.broadcast(req.user.userId, {
                  type: 'video.metadata',
                  payload: {
                    videoSummaryId,
                    title: event.title,
                    channel: event.channel,
                    thumbnailUrl: event.thumbnailUrl,
                    duration: event.duration,
                  },
                });
              }

              // ─── Pipeline event persistence ───
              // Only persist synthesis for crash recovery display (SEO, sharing).
              // Raw triage/output/enrichment are no longer stored — the Python
              // summarizer saves the final assembledMeta + assembledTabs via
              // save_structured_result() at the end of the pipeline.
              // Both emissions (memory-done partial, synthesis-done superset)
              // are merged; the repository keeps a partial from overwriting the
              // superset.
              if (event.event === 'synthesis_complete') {
                const synthesis = synthesisCompleteEventSchema.safeParse(event);
                if (synthesis.success) {
                  pendingWrites.push(videoRepository.mergeSynthesis(videoSummaryId, synthesis.data));
                } else {
                  req.log.warn({ videoSummaryId, issues: synthesis.error.issues }, 'Invalid synthesis_complete event skipped');
                }
              }
            } catch (err) {
              // Log parse errors in development for debugging
              if (process.env.NODE_ENV === 'development') {
                req.log.debug({ err, data }, 'SSE event parse skipped');
              }
            }
          }

          // Forward chunk to client
          reply.raw.write(chunk);
        }
      } finally {
        // Await all pending DB writes before closing the stream to prevent data loss
        if (pendingWrites.length > 0) {
          const results = await Promise.allSettled(pendingWrites);
          const failed = results.filter(r => r.status === 'rejected');
          if (failed.length > 0) {
            req.log.warn(
              { failed: failed.length, total: results.length, videoSummaryId },
              'Some DB writes failed during stream'
            );
          }
        }
        await cleanup();
      }

      reply.raw.end();
    } catch (error) {
      if (upstreamAbort.signal.aborted) {
        // Client disconnected — the fetch/read rejection is the abort working
        // as designed, not an upstream failure.
        req.log.debug('SSE client disconnected; upstream fetch aborted');
        reply.raw.end();
        return;
      }
      req.log.error(error, 'Stream proxy error');
      reply.raw.write(`data: ${JSON.stringify({ event: 'error', message: 'Stream connection failed', code: 'CONNECTION_FAILED' })}\n\n`);
      reply.raw.end();
    }

    // Return void to prevent Fastify from trying to send a response
    return;
  });
}
