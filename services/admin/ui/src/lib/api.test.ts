import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// Tests for API client module
describe('api client', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe('setApiKey / hasApiKey / clearApiKey', () => {
    it('should store and detect API key', async () => {
      const { setApiKey, hasApiKey } = await import('./api');
      expect(hasApiKey()).toBe(false);
      setApiKey('test-key');
      expect(hasApiKey()).toBe(true);
    });

    it('should clear API key', async () => {
      const { setApiKey, hasApiKey, clearApiKey } = await import('./api');
      setApiKey('test-key');
      clearApiKey();
      expect(hasApiKey()).toBe(false);
    });
  });

  describe('api.usage.stats', () => {
    it('should call /usage/stats with days param', async () => {
      const { api, setApiKey } = await import('./api');
      setApiKey('my-key');
      const mockFetch = vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ total_calls: 10, total_cost_usd: 1.5 }),
      });
      vi.stubGlobal('fetch', mockFetch);

      const result = await api.usage.stats(7);
      expect(mockFetch).toHaveBeenCalledWith('/usage/stats?days=7', expect.objectContaining({
        headers: expect.objectContaining({ Authorization: 'Bearer my-key' }),
      }));
      expect(result).toEqual({ total_calls: 10, total_cost_usd: 1.5 });
    });

    it('should throw ApiError on 401 and clear key', async () => {
      const { api, setApiKey, hasApiKey, ApiError } = await import('./api');
      setApiKey('bad-key');
      const reloadMock = vi.fn();
      Object.defineProperty(window, 'location', { value: { reload: reloadMock }, writable: true });
      vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 401, statusText: 'Unauthorized' }));

      await expect(api.usage.stats()).rejects.toBeInstanceOf(ApiError);
      expect(hasApiKey()).toBe(false);
    });

    it('should throw ApiError with status on non-401 failure', async () => {
      const { api, setApiKey, ApiError } = await import('./api');
      setApiKey('k');
      vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 500, statusText: 'Server Error' }));

      try {
        await api.usage.stats();
        throw new Error('should have thrown');
      } catch (err) {
        expect(err).toBeInstanceOf(ApiError);
        expect((err as InstanceType<typeof ApiError>).status).toBe(500);
      }
    });
  });

  describe('logout', () => {
    it('should clear key and reload', async () => {
      const { setApiKey, hasApiKey, logout } = await import('./api');
      setApiKey('k');
      const reloadMock = vi.fn();
      Object.defineProperty(window, 'location', { value: { reload: reloadMock }, writable: true });

      logout();
      expect(hasApiKey()).toBe(false);
      expect(reloadMock).toHaveBeenCalledOnce();
    });
  });

  describe('ApiError', () => {
    it('should have name ApiError and status field', async () => {
      const { ApiError } = await import('./api');
      const err = new ApiError(404, 'not found');
      expect(err.name).toBe('ApiError');
      expect(err.status).toBe(404);
      expect(err.message).toBe('not found');
      expect(err).toBeInstanceOf(Error);
    });
  });

  describe('api.health.services', () => {
    it('should call /health/services', async () => {
      const { api, setApiKey } = await import('./api');
      setApiKey('key');
      vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
        ok: true, status: 200,
        json: () => Promise.resolve({ 'vie-api': { status: 'healthy' } }),
      }));

      const result = await api.health.services();
      expect(result).toHaveProperty('vie-api');
    });
  });

  describe('api.usage.byRun', () => {
    it('should call /usage/by-run with days, limit, offset params', async () => {
      const { api, setApiKey } = await import('./api');
      setApiKey('key');
      const mockFetch = vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: () => Promise.resolve([]),
      });
      vi.stubGlobal('fetch', mockFetch);

      await api.usage.byRun(7, 10, 0);
      expect(mockFetch).toHaveBeenCalledWith(
        '/usage/by-run?days=7&limit=10&offset=0',
        expect.anything(),
      );
    });
  });

  describe('api.users.activity', () => {
    it('should call /users/{id}/activity', async () => {
      const { api, setApiKey } = await import('./api');
      setApiKey('key');
      const mockFetch = vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ userId: 'u1', videos: [], assistantCalls: [], costTimeline: [] }),
      });
      vi.stubGlobal('fetch', mockFetch);

      const result = await api.users.activity('user-123');
      expect(mockFetch).toHaveBeenCalledWith(
        '/users/user-123/activity',
        expect.anything(),
      );
      expect(result).toHaveProperty('userId', 'u1');
    });
  });

  describe('error details', () => {
    async function failWith(status: number, body: unknown) {
      const { api, setApiKey } = await import('./api');
      setApiKey('k');
      vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
        ok: false,
        status,
        statusText: 'Bad Request',
        json: () => (body === undefined ? Promise.reject(new SyntaxError('no body')) : Promise.resolve(body)),
      }));
      return api.usage.stats().then(
        () => { throw new Error('should have thrown'); },
        (err: unknown) => err as InstanceType<typeof import('./api').ApiError>,
      );
    }

    it('should keep the generic message and add vie-api\'s own explanation as the detail', async () => {
      const err = await failWith(400, { detail: { error: 'VALIDATION_ERROR', message: 'expiresAt must be in the future' } });

      expect([err.message, err.detail]).toEqual(['API error: 400 Bad Request', 'expiresAt must be in the future']);
    });

    it('should read a plain string detail', async () => {
      expect((await failWith(404, { detail: 'User not found' })).detail).toBe('User not found');
    });

    it('should join the messages of a FastAPI 422 issue list', async () => {
      const err = await failWith(422, { detail: [{ msg: 'Input should be >= 1' }, { msg: 'Field required' }] });

      expect(err.detail).toBe('Input should be >= 1; Field required');
    });

    it('should leave the detail empty when the error body is not JSON', async () => {
      expect((await failWith(502, undefined)).detail).toBeUndefined();
    });

    it('should leave the detail empty when the body carries nothing usable', async () => {
      expect((await failWith(500, { detail: { error: 'X' } })).detail).toBeUndefined();
    });
  });

  describe('describeApiError', () => {
    it('should prefer the server detail, then the message', async () => {
      const { ApiError, describeApiError } = await import('./api');

      expect(describeApiError(new ApiError(400, 'API error: 400', 'reason is required'))).toBe('reason is required');
      expect(describeApiError(new ApiError(500, 'API error: 500'))).toBe('API error: 500');
      expect(describeApiError(new Error('offline'))).toBe('offline');
      expect(describeApiError('weird')).toBe('Something went wrong');
    });
  });

  describe('api.users.grantCredit', () => {
    it('should POST minutes and reason as JSON with the idempotency key as a header', async () => {
      const { api, setApiKey } = await import('./api');
      setApiKey('key');
      const fetchMock = vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ lotId: 'l', auditId: 'a' }),
      });
      vi.stubGlobal('fetch', fetchMock);

      await api.users.grantCredit('u'.repeat(24), { minutes: 150, reason: 'goodwill' }, 'dialog-open-0001');

      const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
      expect(url).toBe(`/users/${'u'.repeat(24)}/grant-credit`);
      expect(init.method).toBe('POST');
      expect(init.headers).toMatchObject({ 'Content-Type': 'application/json', 'Idempotency-Key': 'dialog-open-0001' });
      expect(init.body).toBe(JSON.stringify({ minutes: 150, reason: 'goodwill' }));
    });

    it('should send no Idempotency-Key header when none is given', async () => {
      const { api, setApiKey } = await import('./api');
      setApiKey('key');
      const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: () => Promise.resolve({}) });
      vi.stubGlobal('fetch', fetchMock);

      await api.users.grantCredit('u'.repeat(24), { minutes: 5, reason: 'x' });

      const headers = (fetchMock.mock.calls[0] as [string, RequestInit])[1].headers as Record<string, string>;
      expect(headers).not.toHaveProperty('Idempotency-Key');
    });
  });

  describe('api.alerts.updateConfig', () => {
    it('should POST config as JSON body, not query string', async () => {
      const { api, setApiKey } = await import('./api');
      setApiKey('key');
      const fetchMock = vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ updated: true }),
      });
      vi.stubGlobal('fetch', fetchMock);

      const config = {
        cost_threshold_usd: 1.23,
        daily_spike_multiplier: 2.5,
        failure_rate_threshold: 0.3,
      };
      await api.alerts.updateConfig(config, 'noisy week');

      expect(fetchMock).toHaveBeenCalledOnce();
      const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
      // URL must have no query string
      expect(url).toBe('/alerts/config');
      expect(init.method).toBe('POST');
      const headers = init.headers as Record<string, string>;
      expect(headers['Content-Type']).toBe('application/json');
      expect(JSON.parse(init.body as string)).toEqual({ ...config, reason: 'noisy week' });
    });

    it('should send the reason, which vie-api requires and audits', async () => {
      const { api, setApiKey } = await import('./api');
      setApiKey('key');
      const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: () => Promise.resolve({}) });
      vi.stubGlobal('fetch', fetchMock);

      await api.alerts.updateConfig(
        { cost_threshold_usd: 1, daily_spike_multiplier: 2, failure_rate_threshold: 0.2 },
        'raised after the backfill',
      );

      const init = (fetchMock.mock.calls[0] as [string, RequestInit])[1];
      expect(JSON.parse(init.body as string)).toMatchObject({ reason: 'raised after the backfill' });
    });
  });

  describe('error details', () => {
    async function failWith(status: number, body: unknown) {
      const { api, setApiKey } = await import('./api');
      setApiKey('key');
      vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
        ok: false,
        status,
        statusText: 'Bad',
        json: () => (body === undefined ? Promise.reject(new Error('no body')) : Promise.resolve(body)),
      }));
      return api.usage.stats().catch((error: unknown) => error as { detail?: string });
    }

    it('should use a string detail', async () => {
      expect((await failWith(403, { detail: 'A named admin session is required' })).detail).toBe(
        'A named admin session is required',
      );
    });

    it('should use the message of vie-api\'s envelope', async () => {
      expect((await failWith(400, { detail: { error: 'VALIDATION_ERROR', message: 'minutes: too big' } })).detail).toBe(
        'minutes: too big',
      );
    });

    it('should join FastAPI 422 issues', async () => {
      expect((await failWith(422, { detail: [{ msg: 'a is required' }, { msg: 'b is too long' }] })).detail).toBe(
        'a is required; b is too long',
      );
    });

    it('should fall back to the top-level error vie-admin\'s own middleware answers with', async () => {
      expect((await failWith(403, { error: 'The machine key may only read' })).detail).toBe(
        'The machine key may only read',
      );
    });

    it('should have no detail when the body is not JSON', async () => {
      expect((await failWith(502, undefined)).detail).toBeUndefined();
    });
  });
});
