import { FastifyInstance, FastifyBaseLogger } from 'fastify';
import { config } from '../../config.js';
import { auditRequestContext } from '../../utils/audit-context.js';
import { QUEUE_TOPOLOGY } from '../../services/queue-topology.js';
import type { ConfirmChannel, GetMessage } from 'amqplib';

/**
 * Admin endpoints for observing and replaying the pipeline job queue.
 *
 * Uses the RabbitMQ management HTTP API (port 15672) for read-only stats and
 * DLQ inspection — much simpler than implementing those over AMQP. The replay
 * endpoint is loss-proof and works entirely over AMQP: it validates the live
 * channel BEFORE touching the DLQ, then drains one message at a time with
 * publish → publisher-confirm → ack ordering so a crash at any point leaves
 * unconfirmed messages safely in the DLQ (at-least-once semantics).
 */

interface MgmtQueueInfo {
  messages?: number;
  messages_ready?: number;
  messages_unacknowledged?: number;
  consumers?: number;
}

interface MgmtMessageEnvelope {
  payload: string;
  payload_encoding: 'string' | 'base64';
  properties?: {
    headers?: Record<string, unknown>;
    priority?: number;
    message_id?: string;
  };
  routing_key?: string;
}

function managementBase(): { url: string; auth: string } {
  // amqp://user:pass@host:5672/vhost  →  http://host:15672  + Basic auth header
  const parsed = new URL(config.RABBITMQ_URL);
  const host = parsed.hostname || 'vie-rabbitmq';
  const user = decodeURIComponent(parsed.username || 'guest');
  const pass = decodeURIComponent(parsed.password || 'guest');
  const auth = `Basic ${Buffer.from(`${user}:${pass}`).toString('base64')}`;
  // Default management port is 15672; we don't carry it in RABBITMQ_URL.
  return { url: `http://${host}:15672`, auth };
}

function vhostPath(): string {
  const parsed = new URL(config.RABBITMQ_URL);
  // RabbitMQ's default vhost is '/' which encodes to '%2F' in URLs.
  const raw = decodeURIComponent(parsed.pathname.replace(/^\//, '') || '/');
  return encodeURIComponent(raw);
}

function clampedInt(value: unknown, fallback: number, min: number, max: number): number {
  const n = typeof value === 'string' || typeof value === 'number' ? Number(value) : NaN;
  if (!Number.isFinite(n)) return fallback;
  return Math.min(Math.max(Math.trunc(n), min), max);
}

async function fetchQueueInfo(queue: string): Promise<MgmtQueueInfo> {
  const { url, auth } = managementBase();
  const target = `${url}/api/queues/${vhostPath()}/${encodeURIComponent(queue)}`;
  const res = await fetch(target, { headers: { Authorization: auth, Accept: 'application/json' } });
  if (!res.ok) {
    throw new Error(`Management API ${res.status}: ${await res.text()}`);
  }
  return (await res.json()) as MgmtQueueInfo;
}

/**
 * Reset the attempt counter so the worker's retry budget starts fresh —
 * otherwise a replayed message re-hits the retry cap on its first failure and
 * lands right back in the DLQ. Non-JSON payloads pass through verbatim; the
 * worker's validator will quarantine them.
 */
function resetAttempt(content: Buffer): Buffer {
  try {
    const obj = JSON.parse(content.toString('utf8')) as Record<string, unknown>;
    obj.attempt = 1;
    return Buffer.from(JSON.stringify(obj), 'utf8');
  } catch {
    return content;
  }
}

interface DrainResult {
  replayed: number;
  failure?: unknown;
}

/**
 * Drain up to `cap` messages from the DLQ, one at a time. Per-message ordering
 * is publish → waitForConfirms → ack: a crash between any two steps leaves the
 * message safely in the DLQ. On failure the in-flight message is nacked back
 * (requeue) and the partial count is returned alongside the error.
 */
async function drainDlq(channel: ConfirmChannel, cap: number, log: FastifyBaseLogger): Promise<DrainResult> {
  let replayed = 0;
  let inFlight: GetMessage | false = false;
  try {
    while (replayed < cap) {
      inFlight = await channel.get(QUEUE_TOPOLOGY.dlq, { noAck: false });
      if (!inFlight) break;

      channel.publish(QUEUE_TOPOLOGY.exchange, QUEUE_TOPOLOGY.routingKey, resetAttempt(inFlight.content), {
        persistent: true,
        priority: inFlight.properties.priority,
        contentType: 'application/json',
        messageId: inFlight.properties.messageId,
        headers: {
          ...(inFlight.properties.headers ?? {}),
          'x-attempt': 1,
          'x-replayed-at': new Date().toISOString(),
        },
      });
      await channel.waitForConfirms();
      channel.ack(inFlight);
      inFlight = false;
      replayed++;
    }
    return { replayed };
  } catch (err) {
    if (inFlight) {
      try {
        channel.nack(inFlight, false, true); // requeue — back into the DLQ, not dropped
      } catch (nackErr) {
        log.warn({ err: nackErr }, 'DLQ replay: nack after failure also failed');
      }
    }
    return { replayed, failure: err };
  }
}

export async function adminQueueRoutes(fastify: FastifyInstance): Promise<void> {
  const { auditService } = fastify.container;

  // GET /stats — combined view of main queue + DLQ depth
  fastify.get('/stats', { preHandler: [fastify.requireAdmin('queue:read')] }, async (_req, reply) => {
    try {
      const [main, dlq] = await Promise.all([
        fetchQueueInfo(QUEUE_TOPOLOGY.queue),
        fetchQueueInfo(QUEUE_TOPOLOGY.dlq),
      ]);
      return reply.send({
        main: {
          messages: main.messages ?? 0,
          ready: main.messages_ready ?? 0,
          inFlight: main.messages_unacknowledged ?? 0,
          consumers: main.consumers ?? 0,
        },
        dlq: {
          messages: dlq.messages ?? 0,
        },
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      fastify.log.warn({ err }, 'Management API stats fetch failed');
      return reply.code(502).send({ error: 'MANAGEMENT_UNREACHABLE', message });
    }
  });

  // GET /dlq?limit=N — peek messages without acking
  fastify.get<{ Querystring: { limit?: string } }>('/dlq', {
    preHandler: [fastify.requireAdmin('queue:read')],
  }, async (req, reply) => {
    const limit = clampedInt(req.query.limit, 20, 1, 100);
    try {
      const { url, auth } = managementBase();
      const target = `${url}/api/queues/${vhostPath()}/${encodeURIComponent(QUEUE_TOPOLOGY.dlq)}/get`;
      const res = await fetch(target, {
        method: 'POST',
        headers: { Authorization: auth, 'Content-Type': 'application/json' },
        body: JSON.stringify({
          count: limit,
          ackmode: 'ack_requeue_true', // peek — leave in queue
          encoding: 'auto',
          truncate: 50000,
        }),
      });
      if (!res.ok) {
        throw new Error(`Management API ${res.status}: ${await res.text()}`);
      }
      const messages = (await res.json()) as MgmtMessageEnvelope[];
      return reply.send({
        messages: messages.map((m) => ({
          payload: tryParseJson(m.payload),
          routingKey: m.routing_key,
          messageId: m.properties?.message_id,
          priority: m.properties?.priority,
          headers: m.properties?.headers,
        })),
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      fastify.log.warn({ err }, 'Management API dlq peek failed');
      return reply.code(502).send({ error: 'MANAGEMENT_UNREACHABLE', message });
    }
  });

  // POST /replay — drain the DLQ over AMQP, one message at a time
  fastify.post<{ Body: { max?: number } }>('/replay', {
    preHandler: [fastify.requireAdmin('queue:replay')],
  }, async (req, reply) => {
    const cap = clampedInt(req.body?.max, 100, 1, 500);

    // Validate the queue integration BEFORE any destructive fetch — the old
    // flow drained up to 500 messages first and only then noticed the plugin
    // was disabled, losing everything it had fetched.
    if (!fastify.rabbitmq) {
      return reply.code(503).send({
        error: 'QUEUE_DISABLED',
        message: 'RabbitMQ plugin is not enabled — set USE_QUEUE_PIPELINE=true',
      });
    }

    let channel: ConfirmChannel;
    try {
      channel = await fastify.rabbitmq.getChannel();
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      fastify.log.error({ err }, 'DLQ replay: channel unavailable — DLQ untouched');
      return reply.code(503).send({ error: 'QUEUE_UNAVAILABLE', message });
    }

    const result = await drainDlq(channel, cap, fastify.log);
    // Audited even when the drain failed part-way: the messages already
    // republished are a real change to the queue.
    await auditService.recordBestEffort({
      ...auditRequestContext(req),
      action: 'queue.replay',
      targetType: 'queue',
      targetId: QUEUE_TOPOLOGY.dlq,
      after: { replayed: result.replayed, cap, failed: result.failure !== undefined },
    });
    if (result.failure !== undefined) {
      const message = result.failure instanceof Error ? result.failure.message : String(result.failure);
      fastify.log.error({ err: result.failure, replayed: result.replayed }, 'DLQ replay failed mid-drain');
      return reply.code(502).send({ error: 'REPLAY_FAILED', message, replayed: result.replayed });
    }
    return reply.send({ replayed: result.replayed });
  });
}

function tryParseJson(s: string): unknown {
  try {
    return JSON.parse(s);
  } catch {
    return s;
  }
}
