import { FastifyInstance } from 'fastify';
import { z } from 'zod';
import { RateLimitedError, VideoNotFoundError } from '../utils/errors.js';
import { disableSocketInactivityTimeout } from '../utils/sse.js';
import { actionParamsSchema } from '../schemas/assistant.schema.js';
import type { AssistantAction } from '../services/assistant-client.js';

const chatBodySchema = z.object({
  message: z.string().min(1).max(10000),
  conversationHistory: z.array(z.object({
    role: z.enum(['user', 'assistant']),
    content: z.string().max(10000),
  })).max(50).optional(),
  // Single-use token echoed from a `pending_confirmation` SSE event so the
  // assistant runs the parked destructive/costly action. Pass-through only —
  // the assistant validates ownership + expiry server-side.
  confirmToken: z.string().min(16).max(128).optional(),
});

const actionBodySchema = z.object({
  action: z.enum(['save_note', 'quiz_me', 'find_moment', 'explain']),
  params: actionParamsSchema.optional(),
});

export async function assistantRoutes(fastify: FastifyInstance) {
  const { assistantClient, videoRepository } = fastify.container;

  // POST /api/videos/:videoSummaryId/chat
  fastify.post<{
    Params: { videoSummaryId: string };
    Body: z.infer<typeof chatBodySchema>;
  }>('/:videoSummaryId/chat', {
    preHandler: [fastify.authenticate],
  }, async (req, reply) => {
    const { videoSummaryId } = req.params;

    const parsed = chatBodySchema.safeParse(req.body);
    if (!parsed.success) {
      return reply.status(400).send({
        error: 'VALIDATION_ERROR',
        message: parsed.error.errors[0]?.message || 'Invalid request body',
      });
    }

    // Verify user has access to this video summary
    const hasAccess = await videoRepository.userHasAccessToSummary(req.user.userId, videoSummaryId);
    if (!hasAccess) {
      throw new VideoNotFoundError();
    }

    // Upstream abort tied to client disconnect: when the browser goes away,
    // tear down the assistant fetch/stream immediately instead of proxying
    // into the void (same idea as stream.routes.ts). Listen on the RESPONSE:
    // on POST routes the request body is already consumed, so `req.raw` emits
    // 'close' at message completion — long before any disconnect. The
    // ServerResponse only closes on premature connection termination or after
    // we end the stream ourselves (by which point aborting is a no-op).
    const upstreamAbort = new AbortController();
    reply.raw.on('close', () => upstreamAbort.abort());

    try {
      const stream = await assistantClient.chat({
        videoId: videoSummaryId,
        userId: req.user.userId,
        message: parsed.data.message,
        conversationHistory: parsed.data.conversationHistory,
        confirmToken: parsed.data.confirmToken,
        requestId: req.id,
        signal: upstreamAbort.signal,
      });

      // Set SSE headers and pipe the stream directly.
      // @fastify/cors set ACAO/ACAC on the Fastify reply, but reply.raw.writeHead
      // bypasses Fastify's header flush — forward them so the browser doesn't block
      // the SSE stream (credentials: 'include' requires an explicit, non-* origin).
      const corsOrigin = reply.getHeader('access-control-allow-origin');
      const corsCreds = reply.getHeader('access-control-allow-credentials');
      // Long-lived stream — exempt from the socket inactivity timeout
      disableSocketInactivityTimeout(req);
      reply.raw.writeHead(200, {
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-cache',
        'Connection': 'keep-alive',
        'X-Accel-Buffering': 'no',
        ...(corsOrigin ? { 'Access-Control-Allow-Origin': String(corsOrigin), Vary: 'Origin' } : {}),
        ...(corsCreds ? { 'Access-Control-Allow-Credentials': String(corsCreds) } : {}),
      });

      const reader = stream.getReader();
      const decoder = new TextDecoder();

      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          reply.raw.write(decoder.decode(value, { stream: true }));
        }
      } finally {
        reader.releaseLock();
      }

      reply.raw.end();
    } catch (error) {
      if (upstreamAbort.signal.aborted) {
        // Client disconnected — the fetch/read rejection is the abort working
        // as designed, not an upstream failure.
        req.log.debug('assistant chat client disconnected; upstream fetch aborted');
        reply.raw.end();
        return;
      }
      // If headers already sent, can't change status
      if (reply.raw.headersSent) {
        reply.raw.end();
        return;
      }
      // The assistant's own rate limit is the user's to act on: let the global
      // handler answer 429 RATE_LIMITED instead of masking it as a 502.
      if (error instanceof RateLimitedError) throw error;
      fastify.log.error(error, 'assistant chat failed');
      return reply.status(502).send({
        error: 'SERVICE_UNAVAILABLE',
        message: 'Assistant service is temporarily unavailable. Please try again.',
      });
    }
  });

  // POST /api/videos/:videoSummaryId/action
  fastify.post<{
    Params: { videoSummaryId: string };
    Body: z.infer<typeof actionBodySchema>;
  }>('/:videoSummaryId/action', {
    preHandler: [fastify.authenticate],
  }, async (req, reply) => {
    const { videoSummaryId } = req.params;

    const parsed = actionBodySchema.safeParse(req.body);
    if (!parsed.success) {
      return reply.status(400).send({
        error: 'VALIDATION_ERROR',
        message: parsed.error.errors[0]?.message || 'Invalid action body',
      });
    }

    const hasAccess = await videoRepository.userHasAccessToSummary(req.user.userId, videoSummaryId);
    if (!hasAccess) {
      throw new VideoNotFoundError();
    }

    try {
      const { status, body } = await assistantClient.action({
        videoId: videoSummaryId,
        userId: req.user.userId,
        action: parsed.data.action satisfies AssistantAction,
        params: parsed.data.params,
        requestId: req.id,
      });

      return reply.status(status).send(body);
    } catch (error) {
      fastify.log.error(error, 'assistant action failed');
      return reply.status(502).send({
        error: 'SERVICE_UNAVAILABLE',
        message: 'Assistant service is temporarily unavailable. Please try again.',
      });
    }
  });
}
