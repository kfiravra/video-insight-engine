// Set required environment variables for tests BEFORE any imports that use config
// This file runs first in setupFiles to ensure config validation passes
process.env.MONGODB_URI = 'mongodb://localhost:27017/test?directConnection=true';
process.env.JWT_SECRET = 'test-jwt-secret-key-for-testing-only';
process.env.JWT_REFRESH_SECRET = 'test-jwt-refresh-secret-key-for-testing-only';
process.env.NODE_ENV = 'test';
process.env.SUMMARIZER_URL = 'http://localhost:8000';
process.env.ASSISTANT_URL = 'http://localhost:8001';
// Non-empty so webhook signature verification is exercised (never skipped) in tests
process.env.PADDLE_WEBHOOK_SECRET = 'test-paddle-webhook-secret';
// Redis plugin connects eagerly (lazyConnect:false). The production default
// is `redis://vie-redis:6379` — the Docker network hostname, which won't
// resolve from the host running vitest. Point at localhost so the local
// redis container is used; tests that need to simulate a refused connection
// override this in-test (see redis.test.ts).
process.env.REDIS_URL = process.env.REDIS_URL ?? 'redis://localhost:6379';
