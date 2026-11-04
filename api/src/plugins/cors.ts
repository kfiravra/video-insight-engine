import { FastifyInstance } from 'fastify';
import fp from 'fastify-plugin';
import cors from '@fastify/cors';
import { config } from '../config.js';

async function corsSetup(fastify: FastifyInstance) {
  await fastify.register(cors, {
    origin: config.ALLOWED_ORIGINS,
    credentials: true,
    methods: ['GET', 'POST', 'PUT', 'PATCH', 'DELETE'],
    // X-Session-Id: the chat surface's session id (D8), sent on every assistant request.
    allowedHeaders: ['Content-Type', 'Authorization', 'X-Session-Id'],
  });
}

export const corsPlugin = fp(corsSetup);
