import { describe, it, expect, afterEach, vi } from 'vitest';

// Strong non-default values for every secret the production refinement checks.
// Each test overrides exactly one of these to isolate the failure it asserts.
const STRONG_ENV: Record<string, string> = {
  MONGODB_URI: 'mongodb://localhost:27017/test',
  JWT_SECRET: 'unit-test-strong-jwt-secret-0123456789abcdef',
  JWT_REFRESH_SECRET: 'unit-test-strong-refresh-secret-0123456789abcdef',
  INTERNAL_SECRET: 'unit-test-strong-internal-secret',
  ADMIN_API_KEY: 'unit-test-strong-admin-key',
  PADDLE_WEBHOOK_SECRET: 'unit-test-paddle-webhook-secret',
};

// config.ts parses process.env at import time, so each scenario needs a fresh
// module registry plus stubbed env vars before the dynamic import.
async function loadConfig(overrides: Record<string, string>): Promise<typeof import('./config.js')> {
  vi.resetModules();
  for (const [key, value] of Object.entries({ ...STRONG_ENV, ...overrides })) {
    vi.stubEnv(key, value);
  }
  return import('./config.js');
}

describe('config env validation', () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.resetModules();
  });

  describe('production dev-default secret refusal', () => {
    it('should parse successfully in production when all secrets are real', async () => {
      const { config } = await loadConfig({ NODE_ENV: 'production' });
      expect(config.NODE_ENV).toBe('production');
    });

    it('should throw in production when JWT_SECRET is the .env.example placeholder', async () => {
      await expect(
        loadConfig({
          NODE_ENV: 'production',
          JWT_SECRET: 'your-super-secret-jwt-key-change-this-in-production',
        })
      ).rejects.toThrow(/JWT_SECRET/);
    });

    it('should throw in production when JWT_REFRESH_SECRET is the docker-compose fallback', async () => {
      await expect(
        loadConfig({
          NODE_ENV: 'production',
          JWT_REFRESH_SECRET: 'dev-refresh-secret-change-in-production',
        })
      ).rejects.toThrow(/JWT_REFRESH_SECRET/);
    });

    it('should throw in production when INTERNAL_SECRET is the config default', async () => {
      await expect(
        loadConfig({
          NODE_ENV: 'production',
          INTERNAL_SECRET: 'dev-internal-secret-change-me',
        })
      ).rejects.toThrow(/INTERNAL_SECRET/);
    });

    it('should throw in production when ADMIN_API_KEY is the config default', async () => {
      await expect(
        loadConfig({
          NODE_ENV: 'production',
          ADMIN_API_KEY: 'dev-admin-key-change-me',
        })
      ).rejects.toThrow(/ADMIN_API_KEY/);
    });

    it('should accept dev-default secrets outside production', async () => {
      const { config } = await loadConfig({
        NODE_ENV: 'development',
        INTERNAL_SECRET: 'dev-internal-secret-change-me',
        ADMIN_API_KEY: 'dev-admin-key-change-me',
        PADDLE_WEBHOOK_SECRET: '',
      });
      expect(config.NODE_ENV).toBe('development');
    });
  });

  describe('PADDLE_WEBHOOK_SECRET production requirement', () => {
    it('should throw in production when PADDLE_WEBHOOK_SECRET is empty', async () => {
      await expect(
        loadConfig({ NODE_ENV: 'production', PADDLE_WEBHOOK_SECRET: '' })
      ).rejects.toThrow(/PADDLE_WEBHOOK_SECRET/);
    });

    it('should allow an empty PADDLE_WEBHOOK_SECRET outside production', async () => {
      const { config } = await loadConfig({ NODE_ENV: 'test', PADDLE_WEBHOOK_SECRET: '' });
      expect(config.PADDLE_WEBHOOK_SECRET).toBe('');
    });
  });

  describe('ALLOW_REGISTRATION', () => {
    it('should default to open when unset', async () => {
      const { config } = await loadConfig({});
      expect(config.ALLOW_REGISTRATION).toBe(true);
    });

    it('should close registration when set to false', async () => {
      const { config } = await loadConfig({ ALLOW_REGISTRATION: 'false' });
      expect(config.ALLOW_REGISTRATION).toBe(false);
    });

    it('should treat FALSE with surrounding whitespace as closed', async () => {
      const { config } = await loadConfig({ ALLOW_REGISTRATION: ' FALSE ' });
      expect(config.ALLOW_REGISTRATION).toBe(false);
    });

    it('should keep registration open for any value other than false', async () => {
      const { config } = await loadConfig({ ALLOW_REGISTRATION: 'true' });
      expect(config.ALLOW_REGISTRATION).toBe(true);
    });
  });

  describe('demo mode vars', () => {
    it('should leave demo mode unconfigured when the vars are blank', async () => {
      const { config } = await loadConfig({ DEMO_USER_EMAIL: '', DEMO_USER_PASSWORD: '' });
      expect([config.DEMO_USER_EMAIL, config.DEMO_USER_PASSWORD]).toEqual(['', '']);
    });

    it('should trim and lowercase DEMO_USER_EMAIL so it matches the stored email', async () => {
      const { config } = await loadConfig({ DEMO_USER_EMAIL: '  Demo@Example.COM ' });
      expect(config.DEMO_USER_EMAIL).toBe('demo@example.com');
    });

    it('should keep DEMO_USER_PASSWORD exactly as given', async () => {
      const { config } = await loadConfig({ DEMO_USER_PASSWORD: ' MixedCase 123 ' });
      expect(config.DEMO_USER_PASSWORD).toBe(' MixedCase 123 ');
    });
  });

  describe('PIPELINE_VERSION single source (project-score-9 4.3)', () => {
    it('should read the canonical version from packages/shared pipeline-version.json', async () => {
      const { readFileSync } = await import('node:fs');
      const canonical = JSON.parse(
        readFileSync(
          new URL('../../packages/shared/src/config/pipeline-version.json', import.meta.url),
          'utf-8',
        ),
      ) as { version: string };

      const { config } = await loadConfig({});
      expect(config.PIPELINE_VERSION).toBe(canonical.version);
      expect(config.PIPELINE_VERSION).toMatch(/^v/);
    });

    it('should ignore a PIPELINE_VERSION env var (not env-overridable)', async () => {
      const { config } = await loadConfig({ PIPELINE_VERSION: 'v999-env-injected' });
      expect(config.PIPELINE_VERSION).not.toBe('v999-env-injected');
    });
  });
});
