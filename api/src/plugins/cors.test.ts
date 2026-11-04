import { describe, it, expect } from 'vitest';
import Fastify from 'fastify';
import { config } from '../config.js';
import { corsPlugin } from './cors.js';

describe('CORS plugin', () => {
  it('should use ALLOWED_ORIGINS from config', () => {
    // Verify config.ALLOWED_ORIGINS exists and includes expected origins
    expect(config.ALLOWED_ORIGINS).toBeDefined();
    expect(Array.isArray(config.ALLOWED_ORIGINS)).toBe(true);
    expect(config.ALLOWED_ORIGINS.length).toBeGreaterThan(0);

    // Should include FRONTEND_URL
    expect(config.ALLOWED_ORIGINS).toContain(config.FRONTEND_URL);
  });

  it('should include localhost origins in development', () => {
    // In test environment (which mimics dev), localhost should be included
    const hasLocalhost = config.ALLOWED_ORIGINS.some(
      origin => origin.includes('localhost')
    );
    expect(hasLocalhost).toBe(true);
  });

  it('should allow the chat session header on a preflight', async () => {
    const app = Fastify({ logger: false });
    await app.register(corsPlugin);
    app.post('/chat', async () => ({ ok: true }));
    try {
      const res = await app.inject({
        method: 'OPTIONS',
        url: '/chat',
        headers: {
          origin: config.FRONTEND_URL,
          'access-control-request-method': 'POST',
          'access-control-request-headers': 'content-type,authorization,x-session-id',
        },
      });

      const allowed = String(res.headers['access-control-allow-headers']).toLowerCase();
      expect(allowed).toContain('x-session-id');
    } finally {
      await app.close();
    }
  });
});
